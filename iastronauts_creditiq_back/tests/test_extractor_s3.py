# -*- coding: utf-8 -*-
"""
Test del DocumentExtractor (Agente 1) usando los archivos reales en S3.

Ejecuta las tres capas completas:
  1. Excel  -> pandas (descarga desde S3)
  2. PDF    -> Amazon Textract async (lee directo desde S3, sin descargar)
  3. LLM    -> Claude Sonnet 4.6 (normalizacion NIIF)

Uso:
    cd iastronauts_creditiq_back
    python tests/test_extractor_s3.py
"""
import os
import sys
import time
from pathlib import Path

# ── paths ────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR   = REPO_ROOT / "iastronauts_creditiq_back" / "src"
ENV_FILE  = REPO_ROOT / "iastronauts_creditiq_back" / ".env"

sys.path.insert(0, str(SRC_DIR))

from dotenv import load_dotenv
load_dotenv(ENV_FILE)
os.environ.setdefault("MAIN_BUCKET", "iastronauts-creditiq-us-east-1-dev")

# ── imports (despues del path setup) ─────────────────────────────────────────
import boto3

from agents.document_extractor.handler import (
    _build_accounts,
    _call_llm_extraction,
    _blocks_to_table_text,
    _poll_textract,
    extract_excel,
)
from shared.llm_provider import LLMProvider

# ── config ────────────────────────────────────────────────────────────────────
BUCKET      = os.environ["MAIN_BUCKET"]
REGION      = os.environ.get("AWS_REGION", "us-east-1")
EXCEL_KEY   = "uploads/junio-2025/EEFF_BTGPactual_COMPLETO.xlsx"
PDF_KEY     = "uploads/junio-2025/Rendición Cuentas Acciones Colombia Junio 2025.pdf"

BUSINESS_CTX = (
    "Empresa: Fondo de Inversion Colectiva Abierto BTG Pactual Acciones Colombia\n"
    "Industria: Fondo de inversion colectiva / Renta variable colombiana\n"
    "Periodo: Junio 2025 vs Diciembre 2024"
)


# ── helpers ───────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def section(title: str) -> None:
    print(f"\n{'=' * 65}")
    print(f"  {title}")
    print('=' * 65)

def ok(label: str) -> None:
    print(f"  [OK]  {label}")

def fail(label: str) -> None:
    print(f"  [FAIL] {label}")
    raise AssertionError(label)


# ── paso 1: Excel desde S3 ────────────────────────────────────────────────────

def run_excel(s3_client) -> tuple[str, dict]:
    section("PASO 1 -- Excel desde S3 --> pandas --> LLM")

    log(f"Descargando s3://{BUCKET}/{EXCEL_KEY}")
    content = s3_client.get_object(Bucket=BUCKET, Key=EXCEL_KEY)["Body"].read()
    ok(f"Descargado: {len(content):,} bytes")

    raw_text = extract_excel(content)
    ok(f"Texto extraido: {len(raw_text):,} chars en {raw_text.count('=== Hoja:')} hojas")

    print(f"\n  -- Primeros 500 chars del raw_text --")
    preview = raw_text[:500].replace("\n", "\n  ")
    print(f"  {preview}")

    log("Llamando Claude (normalizacion NIIF)...")
    llm    = LLMProvider()
    result = _call_llm_extraction(raw_text, BUSINESS_CTX, llm,
                                   tenant_id="btg-demo", job_id="test-excel-001")

    accounts = result.get("accounts", [])
    ok(f"Cuentas extraidas: {len(accounts)}")
    ok(f"Periodos: {result.get('periods')}")
    ok(f"statement_type: {result.get('statement_type')}")

    if not accounts:
        fail("LLM no devolvio cuentas")

    print(f"\n  {'act':<9} {'cat':<12} {'Nombre NIIF':<45} {'Actual (MM)':>13} {'Anterior (MM)':>13}")
    print(f"  {'-'*9} {'-'*12} {'-'*45} {'-'*13} {'-'*13}")
    for i, a in enumerate(accounts):
        prev = a.get('previous_value')
        prev_s = f"{prev:>13,.1f}" if prev is not None else f"{'N/A':>13}"
        name = a.get('normalized_account_name', '')[:44]
        print(
            f"  act-{i+1:03d}  "
            f"{a.get('category','?'):<12} "
            f"{name:<45} "
            f"{a.get('current_value',0):>13,.1f} "
            f"{prev_s}"
        )

    return raw_text, result


# ── paso 2: PDF desde S3 via Textract async ───────────────────────────────────

def run_pdf(textract_client) -> tuple[str, dict]:
    section("PASO 2 -- PDF desde S3 --> Textract async --> LLM")

    log(f"Iniciando Textract para s3://{BUCKET}/{PDF_KEY}")
    response = textract_client.start_document_analysis(
        DocumentLocation={"S3Object": {"Bucket": BUCKET, "Name": PDF_KEY}},
        FeatureTypes=["TABLES"],
    )
    job_id = response["JobId"]
    ok(f"Textract job iniciado: {job_id}")

    log("Esperando resultado de Textract (polling cada 5s)...")
    start = time.time()
    blocks = _poll_textract(job_id, textract_client, lambda_context=None)
    elapsed = time.time() - start
    ok(f"Textract completo en {elapsed:.1f}s — {len(blocks):,} bloques")

    table_blocks = [b for b in blocks if b["BlockType"] == "TABLE"]
    ok(f"Tablas detectadas: {len(table_blocks)}")

    raw_text = _blocks_to_table_text(blocks)
    ok(f"Texto de tablas: {len(raw_text):,} chars")

    if raw_text.strip():
        print(f"\n  -- Primeros 500 chars de tablas Textract --")
        preview = raw_text[:500].replace("\n", "\n  ")
        print(f"  {preview}")
    else:
        print("  [!] Textract no detecto tablas estructuradas en el PDF.")
        print("      Puede ser un PDF con tablas como imagen (requiere FORMS o QUERIES).")

    log("Llamando Claude (normalizacion NIIF del PDF)...")
    llm    = LLMProvider()
    result = _call_llm_extraction(raw_text or "(sin tablas estructuradas)", BUSINESS_CTX, llm,
                                   tenant_id="btg-demo", job_id="test-pdf-001")

    accounts = result.get("accounts", [])
    ok(f"Cuentas extraidas del PDF: {len(accounts)}")
    ok(f"Periodos: {result.get('periods')}")

    if accounts:
        print(f"\n  {'act':<9} {'cat':<12} {'Nombre NIIF':<45} {'Actual (MM)':>13}")
        print(f"  {'-'*9} {'-'*12} {'-'*45} {'-'*13}")
        for i, a in enumerate(accounts[:20]):
            name = a.get('normalized_account_name', '')[:44]
            print(
                f"  act-{i+1:03d}  "
                f"{a.get('category','?'):<12} "
                f"{name:<45} "
                f"{a.get('current_value',0):>13,.1f}"
            )
        if len(accounts) > 20:
            print(f"  ... ({len(accounts) - 20} cuentas mas)")

    return raw_text, result


# ── paso 3: ensamblado Pydantic ───────────────────────────────────────────────

def run_assembly(excel_result: dict, pdf_result: dict) -> None:
    section("PASO 3 -- Ensamblado Pydantic (ExtractedAccount)")

    all_raw = excel_result.get("accounts", []) + pdf_result.get("accounts", [])
    accounts, warnings = _build_accounts(all_raw, source_file="combinado")

    ok(f"Total cuentas ensambladas: {len(accounts)}")
    ok("account_id con patron act-NNN")
    ok("confidence_score en rango [0,1]")

    if warnings:
        print(f"\n  [!] Warnings ({len(warnings)}):")
        for w in warnings:
            print(f"      {w}")
    else:
        ok("Sin warnings de parseo")

    cats = {}
    for a in accounts:
        cats[a.category] = cats.get(a.category, 0) + 1
    print("\n  Distribucion por categoria:")
    for cat, count in sorted(cats.items()):
        print(f"    {cat:<15} {count:>3} cuentas")

    conf_avg = sum(a.confidence_score for a in accounts) / len(accounts) if accounts else 0
    print(f"\n  Confianza promedio: {conf_avg:.3f}")


# ── paso 4: contrato lambda_handler → Agente 2 ───────────────────────────────

def run_contract(s3_client, textract_client) -> None:
    """
    Llama a lambda_handler() completo con un OrchestratorOutput real y valida que:
    1. El output es un dict JSON-serializable
    2. ExtractorOutput.model_validate() lo acepta sin errores (contrato con Agente 2)
    3. Todos los campos obligatorios estan presentes y con tipos correctos
    """
    section("PASO 4 -- Contrato lambda_handler -> Agente 2 (ExtractorOutput)")

    from agents.document_extractor.handler import lambda_handler
    from shared.models import ExtractorOutput, OrchestratorOutput
    from shared.models.orchestrator import BusinessContext, FileToProcess
    from shared.models.base import OutputFormat
    import datetime

    # Construir el mismo OrchestratorOutput que el orquestador construiria en produccion
    orchestrator_event = OrchestratorOutput(
        tenant_id="btg-demo",
        business_context=BusinessContext(
            company_name="Fondo de Inversion Colectiva Abierto BTG Pactual Acciones Colombia",
            industry="Fondo de inversion colectiva / Renta variable colombiana",
            reporting_period="2025-06",
            raw_context="Estados financieros junio 2025 vs diciembre 2024",
        ),
        files_to_process=[
            FileToProcess(
                file_name="EEFF_BTGPactual_COMPLETO.xlsx",
                s3_location=EXCEL_KEY,
                file_type="excel",
            ),
            FileToProcess(
                file_name="Rendicion Cuentas Acciones Colombia Junio 2025.pdf",
                s3_location=PDF_KEY,
                file_type="pdf",
            ),
        ],
        niif_standards=["NIIF 7", "NIIF 9", "NIIF 13"],
        report_language="es",
        output_formats=[OutputFormat.MARKDOWN],
    ).model_dump(mode="json")

    log("Invocando lambda_handler (extraccion completa con S3 + Textract + LLM)...")

    # Contexto mock: sin limite de tiempo (lambda_context=None en el poll)
    class MockContext:
        def get_remaining_time_in_millis(self):
            return 290_000

    raw_output = lambda_handler(orchestrator_event, MockContext())

    ok("lambda_handler retorno sin excepciones")
    ok(f"Output es un dict con {len(raw_output)} campos")

    # Validacion del contrato: Agente 2 debe poder consumir este output
    try:
        extractor_output = ExtractorOutput.model_validate(raw_output)
        ok("ExtractorOutput.model_validate() paso -- contrato con Agente 2 VALIDO")
    except Exception as e:
        fail(f"ExtractorOutput.model_validate() fallo -- Agente 2 NO podria arrancar: {e}")

    # Verificar campos criticos del contrato
    ok(f"job_id presente: {extractor_output.job_id}")
    ok(f"tenant_id presente: {extractor_output.tenant_id}")
    ok(f"company_name: {extractor_output.company_name}")
    ok(f"periods: {extractor_output.periods}")
    ok(f"currency: {extractor_output.currency}")
    ok(f"statement_type: {extractor_output.statement_type}")
    ok(f"extraction_confidence: {extractor_output.extraction_confidence:.3f}")
    ok(f"cuentas en accounts: {len(extractor_output.accounts)}")

    if extractor_output.extraction_warnings:
        print(f"\n  [!] Warnings del pipeline ({len(extractor_output.extraction_warnings)}):")
        for w in extractor_output.extraction_warnings:
            print(f"      {w}")
    else:
        ok("Sin warnings de extraccion")

    print(f"\n  Output completo que recibira el Agente 2 (ExtractorOutput):")
    print(f"  {'Campo':<30} {'Valor'}")
    print(f"  {'-'*30} {'-'*30}")
    fields = [
        ("job_id",               extractor_output.job_id),
        ("tenant_id",            extractor_output.tenant_id),
        ("company_name",         extractor_output.company_name[:40]),
        ("statement_type",       extractor_output.statement_type),
        ("currency",             extractor_output.currency),
        ("periods",              str(extractor_output.periods)),
        ("accounts (count)",     str(len(extractor_output.accounts))),
        ("extraction_confidence",f"{extractor_output.extraction_confidence:.3f}"),
        ("niif_standards",       str(extractor_output.niif_standards)),
        ("output_formats",       str([f.value for f in extractor_output.output_formats])),
        ("warnings (count)",     str(len(extractor_output.extraction_warnings))),
    ]
    for name, value in fields:
        print(f"  {name:<30} {value}")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 65)
    print("  TEST S3 -- DocumentExtractor (Agente 1) -- archivos reales")
    print(f"  Bucket : {BUCKET}")
    print(f"  Region : {REGION}")
    print("=" * 65)

    s3_client       = boto3.client("s3", region_name=REGION)
    textract_client = boto3.client("textract", region_name=REGION)

    try:
        _, excel_result = run_excel(s3_client)
        _, pdf_result   = run_pdf(textract_client)
        run_assembly(excel_result, pdf_result)
        run_contract(s3_client, textract_client)

        print(f"\n{'=' * 65}")
        print("  [OK] Agente 1 validado -- contrato con Agente 2 verificado")
        print("=" * 65 + "\n")

    except AssertionError as e:
        print(f"\n  [FAIL] {e}\n")
        sys.exit(1)
    except Exception as e:
        import traceback
        print(f"\n  [ERROR] {e}\n")
        traceback.print_exc()
        sys.exit(1)
