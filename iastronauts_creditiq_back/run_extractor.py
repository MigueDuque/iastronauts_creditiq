#!/usr/bin/env python3
"""
Local runner for Agent 1 — DocumentExtractor.

Usage:
    python run_extractor.py                          # lists folders, prompts you to pick
    python run_extractor.py <folder-prefix>          # e.g. btg-pactual-q2
    python run_extractor.py uploads/may/2026/btg/    # full S3 prefix

Reads credentials and config from iastronauts_creditiq_back/.env
"""
import os
import sys
import json
import time
from pathlib import Path

# ── env setup ────────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# boto3 expects AWS_ACCESS_KEY_ID but .env uses AWS_ACCESS_KEY — fix it
if not os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_ACCESS_KEY"):
    os.environ["AWS_ACCESS_KEY_ID"] = os.environ["AWS_ACCESS_KEY"]

# put src/ on path so imports work exactly as in Lambda
sys.path.insert(0, str(Path(__file__).parent / "src"))

import boto3

BUCKET = os.environ.get("MAIN_BUCKET", "iastronauts-creditiq-us-east-1-dev")
REGION = os.environ.get("AWS_REGION", "us-east-1")

# ── helpers ───────────────────────────────────────────────────────────────────

def hr(char="─", width=65):
    print(char * width)

def section(title):
    print()
    hr("═")
    print(f"  {title}")
    hr("═")

def step(label):
    print(f"\n  ▶  {label}")

def ok(label):
    print(f"  ✓  {label}")

def warn(label):
    print(f"  ⚠  {label}")

def info(label):
    print(f"  ·  {label}")

def infer_file_type(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower()
    if ext == "pdf":   return "pdf"
    if ext in ("xlsx", "xls"): return "excel"
    if ext == "csv":   return "csv"
    return ext

# ── S3 folder listing ────────────────────────────────────────────────────────

def list_upload_folders(s3) -> list[dict]:
    """Returns list of {prefix, files} for every folder under uploads/"""
    paginator = s3.get_paginator("list_objects_v2")
    all_keys  = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix="uploads/"):
        all_keys.extend(o["Key"] for o in page.get("Contents", []))

    # group by 4-part prefix:  uploads/{month}/{year}/{folder}/
    folders: dict[str, list[str]] = {}
    for key in all_keys:
        parts = key.split("/")
        if len(parts) >= 5:                      # uploads/month/year/folder/file
            prefix = "/".join(parts[:4]) + "/"
            folders.setdefault(prefix, []).append(key)
        elif len(parts) == 4 and parts[-1]:      # uploads/month/year/folder  (no trailing slash)
            prefix = "/".join(parts[:3]) + "/" + parts[3] + "/"
            folders.setdefault(prefix, []).append(key)

    return [{"prefix": p, "files": f} for p, f in sorted(folders.items(), reverse=True)]

def resolve_prefix(s3, arg: str) -> dict | None:
    """Turn a user argument (folder name or full prefix) into a {prefix, files} dict."""
    folders = list_upload_folders(s3)
    if not folders:
        return None
    # exact match first
    for f in folders:
        if f["prefix"] == arg or f["prefix"].rstrip("/") == arg.rstrip("/"):
            return f
    # partial match on the last segment
    for f in folders:
        parts = f["prefix"].rstrip("/").split("/")
        if parts[-1] == arg.rstrip("/"):
            return f
    return None

# ── event builder ────────────────────────────────────────────────────────────

def build_event(folder: dict, company_name: str, period: str) -> dict:
    from shared.models.orchestrator import BusinessContext, FileToProcess, OrchestratorOutput
    from shared.models.base import OutputFormat

    files = [
        FileToProcess(
            file_name=k.split("/")[-1],
            s3_location=k,
            file_type=infer_file_type(k),
        )
        for k in folder["files"]
    ]

    event = OrchestratorOutput(
        tenant_id="local-test",
        business_context=BusinessContext(
            company_name=company_name,
            reporting_period=period,
            raw_context=f"Local test run. Folder: {folder['prefix']}",
        ),
        files_to_process=files,
        niif_standards=["NIIF 7", "NIIF 9", "NIIF 13"],
        output_formats=[OutputFormat.MARKDOWN],
    )
    return event.model_dump(mode="json")

# ── pretty-print ExtractorOutput ────────────────────────────────────────────

def print_result(output: dict):
    section("EXTRACTOR OUTPUT")

    meta_fields = [
        ("job_id",               output.get("job_id", "")),
        ("tenant_id",            output.get("tenant_id", "")),
        ("company_name",         output.get("company_name", "")),
        ("statement_type",       output.get("statement_type", "")),
        ("currency",             output.get("currency", "")),
        ("periods",              str(output.get("periods", []))),
        ("extraction_confidence",f"{output.get('extraction_confidence', 0):.3f}"),
        ("accounts extracted",   str(len(output.get("accounts", [])))),
        ("warnings",             str(len(output.get("extraction_warnings", [])))),
    ]
    print()
    for name, val in meta_fields:
        print(f"  {name:<28} {val}")

    accounts = output.get("accounts", [])
    if accounts:
        section(f"ACCOUNTS ({len(accounts)} extracted)")
        header = f"  {'ID':<9} {'Category':<14} {'Normalized Name':<44} {'Current (MM)':>14} {'Prev (MM)':>12} {'Conf':>6}"
        print(header)
        hr()
        for a in accounts:
            prev = a.get("previous_value")
            prev_s = f"{prev:>12,.1f}" if prev is not None else f"{'—':>12}"
            name = a.get("normalized_account_name", "")[:43]
            print(
                f"  {a.get('account_id',''):<9} "
                f"{a.get('category',''):<14} "
                f"{name:<44} "
                f"{a.get('current_value', 0):>14,.1f} "
                f"{prev_s} "
                f"{a.get('confidence_score', 0):>6.2f}"
            )

    warnings = output.get("extraction_warnings", [])
    if warnings:
        section(f"WARNINGS ({len(warnings)})")
        for w in warnings:
            warn(w)

    # save full JSON beside the script for inspection
    out_path = Path(__file__).parent / "extractor_output.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    print()
    ok(f"Full JSON saved → {out_path.name}")

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    s3 = boto3.client("s3", region_name=REGION)

    # ── 1. resolve folder ────────────────────────────────────────────────────
    if len(sys.argv) > 1:
        folder = resolve_prefix(s3, sys.argv[1])
        if not folder:
            print(f"  Folder '{sys.argv[1]}' not found in s3://{BUCKET}/uploads/")
            sys.exit(1)
    else:
        folders = list_upload_folders(s3)
        if not folders:
            print(f"  No folders found under s3://{BUCKET}/uploads/")
            sys.exit(1)

        section("AVAILABLE UPLOAD FOLDERS")
        for i, f in enumerate(folders):
            print(f"  [{i}]  {f['prefix']}  ({len(f['files'])} file(s))")
            for k in f["files"]:
                print(f"        · {k.split('/')[-1]}")

        print()
        choice = input("  Select folder number: ").strip()
        folder = folders[int(choice)]

    # ── 2. show selected files ───────────────────────────────────────────────
    section("SELECTED FOLDER")
    info(f"Prefix : {folder['prefix']}")
    for k in folder["files"]:
        info(f"File   : {k.split('/')[-1]}  [{infer_file_type(k).upper()}]")

    # ── 3. company / period ──────────────────────────────────────────────────
    print()
    company = input("  Company name (Enter to use folder name): ").strip()
    if not company:
        company = folder["prefix"].rstrip("/").split("/")[-1]
    period = input("  Reporting period (Enter for current): ").strip()
    if not period:
        from datetime import datetime
        now = datetime.utcnow()
        period = f"{now.year}-{now.month:02d}"

    # ── 4. build event ───────────────────────────────────────────────────────
    event = build_event(folder, company, period)

    section("RUNNING AGENT 1 — DocumentExtractor")
    info(f"Bucket  : {BUCKET}")
    info(f"Company : {company}")
    info(f"Period  : {period}")
    info(f"Files   : {len(folder['files'])}")
    info(f"LLM     : {os.environ.get('LLM_PROVIDER')} / {os.environ.get('LLM_MODEL', 'default')}")
    print()

    # ── 5. run handler ───────────────────────────────────────────────────────
    from agents.document_extractor.handler import lambda_handler

    class MockContext:
        def get_remaining_time_in_millis(self):
            return 290_000

    t0 = time.time()
    try:
        output = lambda_handler(event, MockContext())
    except Exception as e:
        import traceback
        print(f"\n  [ERROR] {e}")
        traceback.print_exc()
        sys.exit(1)

    elapsed = time.time() - t0
    ok(f"Completed in {elapsed:.1f}s")

    # ── 6. print results ─────────────────────────────────────────────────────
    print_result(output)


if __name__ == "__main__":
    main()
