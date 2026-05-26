import io
import json
import logging
import os
import time

import boto3
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.llm_provider import LLMProvider
from shared.models import ExtractedAccount, ExtractorOutput, OrchestratorOutput
from shared.s3_instructions import load_text as load_instruction

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET = os.environ.get("MAIN_BUCKET", "")
_TEXTRACT_POLL_INTERVAL_SEC = 5
_TEXTRACT_POLL_MAX_ATTEMPTS = 50   # 250 s max wait
_LLM_TEXT_CAP = 20_000             # chars sent to LLM per file

# S3 key for this agent's prompt
_PROMPT_S3_KEY = "instructions/prompts/01_prompt_agent_extractor.md"


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------

def _download(s3_key: str, s3_client) -> bytes:
    return s3_client.get_object(Bucket=BUCKET, Key=s3_key)["Body"].read()

# ---------------------------------------------------------------------------
# PDF → Textract async
# ---------------------------------------------------------------------------

def _cell_text(cell: dict, block_map: dict) -> str:
    words = []
    for rel in cell.get("Relationships", []):
        if rel["Type"] == "CHILD":
            for bid in rel["Ids"]:
                blk = block_map.get(bid)
                if blk and blk["BlockType"] == "WORD":
                    words.append(blk.get("Text", ""))
    return " ".join(words)


def _blocks_to_table_text(blocks: list[dict]) -> str:
    """Converts Textract TABLE blocks into a tab-separated text representation."""
    block_map = {b["Id"]: b for b in blocks}
    tables: list[str] = []

    for block in blocks:
        if block["BlockType"] != "TABLE":
            continue
        cells: dict[tuple[int, int], str] = {}
        for rel in block.get("Relationships", []):
            if rel["Type"] == "CHILD":
                for cid in rel["Ids"]:
                    cell = block_map.get(cid)
                    if cell and cell["BlockType"] == "CELL":
                        cells[(cell["RowIndex"], cell["ColumnIndex"])] = _cell_text(cell, block_map)

        if not cells:
            continue
        max_row = max(r for r, _ in cells)
        max_col = max(c for _, c in cells)
        rows = [
            "\t".join(cells.get((r, c), "") for c in range(1, max_col + 1))
            for r in range(1, max_row + 1)
        ]
        tables.append("\n".join(rows))

    return "\n\n---\n\n".join(tables)


def _poll_textract(job_id: str, textract_client, lambda_context) -> list[dict]:
    """Polls until the async Textract job finishes; returns all blocks."""
    for attempt in range(_TEXTRACT_POLL_MAX_ATTEMPTS):
        if lambda_context and lambda_context.get_remaining_time_in_millis() < 30_000:
            raise TimeoutError("Lambda running out of time before Textract completion")

        result = textract_client.get_document_analysis(JobId=job_id)
        status = result["JobStatus"]

        if status == "FAILED":
            raise ValueError(f"Textract job failed: {result.get('StatusMessage', 'desconocido')}")

        if status == "SUCCEEDED":
            blocks = list(result.get("Blocks", []))
            next_token = result.get("NextToken")
            while next_token:
                page = textract_client.get_document_analysis(JobId=job_id, NextToken=next_token)
                blocks.extend(page.get("Blocks", []))
                next_token = page.get("NextToken")
            return blocks

        time.sleep(_TEXTRACT_POLL_INTERVAL_SEC)

    raise TimeoutError("Textract job no completó dentro del límite de polling")


def extract_pdf(s3_key: str, textract_client, lambda_context) -> str:
    response = textract_client.start_document_analysis(
        DocumentLocation={"S3Object": {"Bucket": BUCKET, "Name": s3_key}},
        FeatureTypes=["TABLES"],
    )
    job_id = response["JobId"]
    logger.info("textract_start | job=%s key=%s", job_id, s3_key)
    blocks = _poll_textract(job_id, textract_client, lambda_context)
    logger.info("textract_done  | job=%s blocks=%d", job_id, len(blocks))
    return _blocks_to_table_text(blocks)

# ---------------------------------------------------------------------------
# Excel / CSV → pandas
# ---------------------------------------------------------------------------

def extract_excel(content: bytes) -> str:
    xls = pd.ExcelFile(io.BytesIO(content))
    parts: list[str] = []
    for sheet_name in xls.sheet_names:
        try:
            df = pd.read_excel(io.BytesIO(content), sheet_name=sheet_name, header=None, dtype=str)
            df = df.fillna("")
            df = df.loc[~(df == "").all(axis=1)]
            df = df.loc[:, ~(df == "").all(axis=0)]
            if df.empty:
                continue
            parts.append(f"=== Hoja: {sheet_name} ===\n{df.to_string(index=False, header=False)}")
        except Exception as e:
            logger.warning("excel_sheet_skip | sheet=%s error=%s", sheet_name, e)
    return "\n\n".join(parts)


def extract_csv(content: bytes) -> str:
    try:
        df = pd.read_csv(io.BytesIO(content), header=None, dtype=str).fillna("")
        return df.to_string(index=False, header=False)
    except Exception:
        return content.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# LLM normalization / homologation
# ---------------------------------------------------------------------------

# Inline fallback — used when the S3 prompt file is not yet uploaded or unreachable
_EXTRACTION_SYSTEM_PROMPT_FALLBACK = """
Eres un extractor especializado en estados financieros colombianos bajo NIIF (IFRS).
Recibirás texto crudo de tablas extraídas de documentos financieros (PDFs y Excel).

Devuelve ÚNICAMENTE un JSON con esta estructura:
{
  "periods": ["YYYY-MM", "YYYY-MM"],
  "accounts": [
    {
      "raw_account_name": "nombre exacto del documento",
      "normalized_account_name": "nombre NIIF estándar en español",
      "category": "assets" | "liabilities" | "equity" | "revenue" | "expense" | "other",
      "current_value": número en COP MM,
      "previous_value": número en COP MM o null,
      "confidence_score": 0.0 a 1.0
    }
  ]
}

Reglas:
- Unidades: si el documento está en pesos colombianos, dividir entre 1,000,000 para convertir a COP MM.
  Si ya está en millones (MM), usar el valor directamente.
- Numeros negativos: (1,234) = -1234.
- Separador de miles: puede ser coma o punto según el documento.
- periods: inferir de los encabezados de columna. Formato YYYY-MM. Si no hay mes, usar -12.
- La primera columna de valores es el período más reciente (current_value).
- Incluir SOLO las cuentas materiales (máximo 60): totales de sección, subtotales clave,
  utilidad del período, aportes/retiros de inversionistas, instrumentos financieros principales.
  Omitir líneas de detalle menor que no aporten al análisis de variaciones.
- Omitir filas de encabezado, notas al pie y celdas sin valor numérico.
- confidence_score: 1.0 si el valor es claro, 0.5 si hubo ambigüedad, 0.2 si fue inferido.
"""

# Lazy-loaded prompt — populated on first LLM call, cached for container lifetime
_extraction_system_prompt_cache: str | None = None


def _get_extraction_prompt() -> str:
    """Return the extraction system prompt, loading from S3 on first call."""
    global _extraction_system_prompt_cache
    if _extraction_system_prompt_cache is None:
        _extraction_system_prompt_cache = load_instruction(
            _PROMPT_S3_KEY,
            fallback=_EXTRACTION_SYSTEM_PROMPT_FALLBACK,
        )
        source = "s3" if _extraction_system_prompt_cache != _EXTRACTION_SYSTEM_PROMPT_FALLBACK else "local_fallback"
        logger.info("extractor_prompt_loaded | source=%s chars=%d", source, len(_extraction_system_prompt_cache))
    return _extraction_system_prompt_cache


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
def _call_llm_extraction(
    raw_text: str,
    business_ctx_str: str,
    llm: LLMProvider,
    tenant_id: str,
    job_id: str,
) -> dict:
    user_prompt = (
        f"Contexto del negocio:\n{business_ctx_str}\n\n"
        f"Texto extraído del documento:\n{raw_text[:_LLM_TEXT_CAP]}"
    )
    result = llm.generate_json(
        system_prompt=_get_extraction_prompt(),
        user_prompt=user_prompt,
        temperature=0.0,
        tenant_id=tenant_id,
        job_id=job_id,
        max_tokens=16384,
    )
    return result if isinstance(result, dict) else {"accounts": result, "periods": []}


# ---------------------------------------------------------------------------
# Account assembly
# ---------------------------------------------------------------------------

def _build_accounts(raw_items: list[dict], source_file: str) -> tuple[list[ExtractedAccount], list[str]]:
    accounts: list[ExtractedAccount] = []
    warnings: list[str] = []
    for i, item in enumerate(raw_items):
        try:
            prev = item.get("previous_value")
            accounts.append(ExtractedAccount(
                account_id=f"act-{i+1:03d}",   # re-indexed globally after merge
                raw_account_name=str(item.get("raw_account_name", "")),
                normalized_account_name=str(item.get("normalized_account_name", "")),
                category=str(item.get("category", "other")),
                current_value=float(item.get("current_value") or 0),
                previous_value=float(prev) if prev is not None else None,
                currency="COP",
                confidence_score=min(1.0, max(0.0, float(item.get("confidence_score", 0.5)))),
                source_file=source_file,
            ))
        except (TypeError, ValueError) as e:
            warnings.append(f"Cuenta {i + 1} de '{source_file}' omitida — error de parseo: {e}")
    return accounts, warnings


def _reindex(accounts: list[ExtractedAccount]) -> list[ExtractedAccount]:
    return [a.model_copy(update={"account_id": f"act-{i+1:03d}"}) for i, a in enumerate(accounts)]


def _avg_confidence(accounts: list[ExtractedAccount]) -> float:
    if not accounts:
        return 0.0
    return round(sum(a.confidence_score for a in accounts) / len(accounts), 3)


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    """
    Input:  OrchestratorOutput
    Output: ExtractorOutput
    """
    payload = OrchestratorOutput.model_validate(event)

    s3_client = boto3.client("s3")
    textract_client = boto3.client("textract")
    llm = LLMProvider()

    business_ctx_str = (
        f"Empresa: {payload.business_context.company_name or 'No especificada'}\n"
        f"Industria: {payload.business_context.industry or 'No especificada'}\n"
        f"Período: {payload.business_context.reporting_period or 'No especificado'}\n"
        f"Contexto: {payload.business_context.raw_context[:500]}"
    )

    all_accounts: list[ExtractedAccount] = []
    all_periods: list[str] = []
    warnings: list[str] = []
    rendicion_text_s3_key: str | None = None

    for file in payload.files_to_process:
        file_type = file.file_type.lower().lstrip(".")
        logger.info("extracting | file=%s type=%s", file.file_name, file_type)

        try:
            if file_type == "pdf":
                raw_text = extract_pdf(file.s3_location, textract_client, context)
                if not raw_text.strip():
                    warnings.append(f"Sin contenido extraído de la rendición de cuentas '{file.file_name}'")
                    continue
                
                # Save accountability report text in S3 for later qualitative use
                s3_key = f"uploads/{payload.tenant_id}/{payload.job_id}_rendicion.txt"
                try:
                    s3_client.put_object(
                        Bucket=BUCKET,
                        Key=s3_key,
                        Body=raw_text.encode("utf-8"),
                    )
                    rendicion_text_s3_key = s3_key
                    logger.info("rendicion_saved | key=%s bytes=%d", s3_key, len(raw_text))
                except Exception as exc:
                    warnings.append(f"No se pudo guardar el texto de rendición en S3: {exc}")
                    logger.error("rendicion_save_failed | job=%s error=%s", payload.job_id, exc)
                continue  # Skip account extraction for PDF (narrative only)

            elif file_type in ("excel", "xlsx", "xls"):
                raw_text = extract_excel(_download(file.s3_location, s3_client))
            elif file_type == "csv":
                raw_text = extract_csv(_download(file.s3_location, s3_client))
            else:
                warnings.append(f"Tipo no soportado: '{file.file_type}' ({file.file_name})")
                continue

            if not raw_text.strip():
                warnings.append(f"Sin contenido extraído de '{file.file_name}'")
                continue

            logger.info("raw_text | file=%s chars=%d", file.file_name, len(raw_text))

            llm_result = _call_llm_extraction(
                raw_text, business_ctx_str, llm,
                tenant_id=payload.tenant_id,
                job_id=payload.job_id,
            )

            raw_accounts = llm_result.get("accounts", [])
            file_accounts, file_warnings = _build_accounts(raw_accounts, file.file_name)
            all_accounts.extend(file_accounts)
            warnings.extend(file_warnings)

            all_periods.extend(llm_result.get("periods", []))

            logger.info("accounts_extracted | file=%s count=%d", file.file_name, len(file_accounts))

        except Exception as e:
            warnings.append(f"Error procesando '{file.file_name}': {e}")
            logger.error("extraction_error | file=%s error=%s", file.file_name, e, exc_info=True)

    # Merge and deduplicate periods; keep the two most recent
    seen: set[str] = set()
    unique_periods = [p for p in all_periods if p not in seen and not seen.add(p)]
    unique_periods = sorted(unique_periods, reverse=True)[:2]

    # Fall back to business_context if LLM didn't detect periods
    if not unique_periods and payload.business_context.reporting_period:
        unique_periods = [payload.business_context.reporting_period]

    company_name = payload.business_context.company_name or ""

    result = ExtractorOutput(
        job_id=payload.job_id,
        tenant_id=payload.tenant_id,
        business_context=payload.business_context,
        niif_standards=payload.niif_standards,
        report_language=payload.report_language,
        output_formats=payload.output_formats,
        company_name=company_name,
        currency="COP",
        periods=unique_periods,
        accounts=_reindex(all_accounts),
        extraction_confidence=_avg_confidence(all_accounts),
        extraction_warnings=warnings,
        rendicion_text_s3_key=rendicion_text_s3_key,
    )

    logger.info(
        "extractor_done | job=%s accounts=%d periods=%s confidence=%.2f warnings=%d",
        payload.job_id, len(result.accounts), result.periods,
        result.extraction_confidence, len(result.extraction_warnings),
    )

    output = result.model_dump(mode="json")

    # Save output so report_url can serve it while downstream agents are stubs
    try:
        s3_client.put_object(
            Bucket=BUCKET,
            Key=f"jobs/{result.job_id}/extractor_output.json",
            Body=json.dumps(output),
        )
    except Exception as exc:
        logger.warning("extractor_output_save_failed | job=%s error=%s", result.job_id, exc)

    return output