import io
import json
import logging
import os
import time

import boto3
import pandas as pd
from tenacity import RetryError, retry, stop_after_attempt, wait_exponential

from shared.llm_provider import LLMProvider
from shared.models import ExtractedAccount, ExtractorOutput, OrchestratorOutput
from shared.s3_instructions import load_text as load_instruction
from shared.job_store import save as job_save, EXTRACTOR
from .niif_validator import validate_niif

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

def _lines_to_text(blocks: list[dict]) -> str:
    """Join Textract LINE blocks into newline-separated narrative text."""
    return "\n".join(
        b.get("Text", "") for b in blocks if b.get("BlockType") == "LINE"
    )


def _poll_textract(job_id: str, textract_client, lambda_context) -> list[dict]:
    """Polls until the async Textract text-detection job finishes; returns all blocks."""
    for attempt in range(_TEXTRACT_POLL_MAX_ATTEMPTS):
        if lambda_context and lambda_context.get_remaining_time_in_millis() < 30_000:
            raise TimeoutError("Lambda running out of time before Textract completion")

        result = textract_client.get_document_text_detection(JobId=job_id)
        status = result["JobStatus"]

        if status == "FAILED":
            raise ValueError(f"Textract job failed: {result.get('StatusMessage', 'desconocido')}")

        if status == "SUCCEEDED":
            blocks = list(result.get("Blocks", []))
            next_token = result.get("NextToken")
            while next_token:
                page = textract_client.get_document_text_detection(JobId=job_id, NextToken=next_token)
                blocks.extend(page.get("Blocks", []))
                next_token = page.get("NextToken")
            return blocks

        time.sleep(_TEXTRACT_POLL_INTERVAL_SEC)

    raise TimeoutError("Textract job no completó dentro del límite de polling")


def extract_pdf(s3_key: str, textract_client, lambda_context) -> str:
    """
    Extract narrative text from the rendición PDF via Textract DetectDocumentText.

    The PDF is consumed only as narrative context (account figures come from the
    Excel), so we use the cheap text-detection API ($1.50/1k pages) instead of
    table analysis ($15/1k pages) — and read LINE blocks to capture the prose,
    which the previous TABLES-only path discarded entirely.
    """
    response = textract_client.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": BUCKET, "Name": s3_key}},
    )
    job_id = response["JobId"]
    logger.info("textract_start | job=%s key=%s mode=text_detection", job_id, s3_key)
    blocks = _poll_textract(job_id, textract_client, lambda_context)
    logger.info("textract_done  | job=%s blocks=%d", job_id, len(blocks))
    return _lines_to_text(blocks)

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
  "fund_metadata": {
    "fund_type": "tipo de fondo o null",
    "creation_date": "YYYY-MM-DD o null",
    "administrator": "nombre del gestor/administrador o null",
    "custodian": "entidad custodio o null",
    "risk_profile": "conservador | moderado | agresivo | null",
    "benchmark": "índice de referencia o null",
    "investment_policy_summary": "máx 2 oraciones sobre política de inversión o null"
  },
  "accounts": [
    {
      "raw_account_name": "nombre exacto del documento",
      "normalized_account_name": "nombre NIIF estándar en español",
      "category": "assets" | "liabilities" | "equity" | "revenue" | "expense" | "other",
      "current_value": número en COP MM,
      "previous_value": número en COP MM o null,
      "confidence_score": 0.0 a 1.0,
      "source_sheet": "nombre de la hoja Excel o null si es PDF/CSV",
      "is_total": true | false
    }
  ]
}

fund_metadata debe incluirse siempre. Si el documento NO es un fondo de inversión, devuelve
todos sus campos como null. Si SÍ es un fondo, extrae los datos de la primera página (carátula,
encabezado o sección de información general del fondo).

═══════════════════════════════════════════════════════
CAMPO source_sheet (OBLIGATORIO para Excel)
═══════════════════════════════════════════════════════

Cada vez que el texto incluya un marcador "=== Hoja: <nombre> ===" antes de una tabla,
TODOS los registros extraídos de esa tabla deben llevar:
  "source_sheet": "<nombre exacto de la hoja>"

Para documentos PDF o CSV donde no hay marcador de hoja, usar:
  "source_sheet": null

═══════════════════════════════════════════════════════
CAMPO is_total — IDENTIFICACIÓN DE FILAS RESUMEN (CRÍTICO)
═══════════════════════════════════════════════════════

Marcar "is_total": true cuando la fila del documento es una fila de SUMA o SUBTOTAL —
es decir, cuando su valor es la suma de otras filas de la misma tabla o sección.

Señales típicas de que una fila ES un total (is_total: true):
  - El raw_account_name empieza o contiene: "Total", "TOTAL", "Subtotal", "SUBTOTAL",
    "Suma", "Total activos", "Total pasivos", "Total patrimonio", "Total activo neto",
    "Efectivo neto", "Total instrumentos", "Total activos financieros".
  - Es la última fila de una sección y su valor coincide con la suma de las filas anteriores.
  - En hojas de detalle (ej. Inversiones, Efectivo, Cuentas por Pagar), la fila "Total"
    al final de la tabla.

Marcar "is_total": false para todas las demás filas (líneas individuales de cuenta,
partidas de inversión específicas, gastos individuales, etc.).

IMPORTANTE: Extraer TANTO las filas de detalle COMO las filas de total cuando sean
materiales — ambas son útiles para análisis. El campo is_total permite al Agente 2
evitar doble conteo al calcular sumas por categoría.

═══════════════════════════════════════════════════════
REGLAS DE SELECCIÓN DE COLUMNAS (CRÍTICO)
═══════════════════════════════════════════════════════

1. IDENTIFICAR current_value y previous_value:
   - current_value  → la columna del período MÁS RECIENTE (mayor año, o mayor mes dentro del mismo año).
   - previous_value → la columna del período ANTERIOR COMPARABLE:
       * Para balance general (activos/pasivos/patrimonio): el cierre del año anterior (Dic YYYY).
       * Para estado de resultados (ingresos/gastos): el mismo período acumulado del año anterior
         (ej. "Jun 2024" es el comparable de "Jun 2025", NO "Trim 2025").
       * Para flujo de efectivo: el mismo período acumulado del año anterior.

2. TABLAS CON MÁS DE 2 COLUMNAS DE VALORES:
   Si la tabla tiene 4 columnas (ej. "Jun 2025 | Jun 2024 | Trim 2025 | Trim 2024"):
   - Usar "Jun 2025" como current_value.
   - Usar "Jun 2024" como previous_value (mismo período, año anterior).
   - IGNORAR las columnas trimestrales — no son comparables con el acumulado.

   Si la tabla de inversiones tiene columnas "Nominal YYYY | Valor YYYY":
   - Usar la columna "Valor YYYY" (valor de mercado o razonable) como current_value/previous_value.
   - Capturar la columna "Nominal YYYY" del período MÁS RECIENTE en nominal_value
     (cantidad/valor nominal de la posición). Si no existe, nominal_value: null.

3. POSICIONES NUEVAS O CERRADAS (valor = 0 en un período):
   - Si el valor del período anterior es 0 y el actual es > 0 → es una posición NUEVA.
     Establecer previous_value: null (NO poner 0).
   - Si el valor actual es 0 y el anterior es > 0 → es una posición CERRADA/LIQUIDADA.
     Establecer current_value: 0, previous_value: el valor anterior.
   - Nunca extraer un 0 como previous_value cuando el campo "0" claramente indica ausencia.

4. previous_value debe ser null (no 0, no omitido) cuando:
   - La cuenta o posición NO EXISTÍA en el período anterior.
   - Solo hay UNA columna de valores en la tabla (sin dato comparativo).
   - El encabezado del período anterior no es comparable con el actual
     (ej. no mezclar acumulado anual con trimestral).

═══════════════════════════════════════════════════════
REGLAS GENERALES
═══════════════════════════════════════════════════════

- Unidades: determinar la unidad del documento (COP, COP miles, COP MM).
  * Si los valores parecen estar en pesos COP (números muy grandes, ej. 39,000,000,000):
    dividir entre 1,000,000 para convertir a COP MM.
  * Si parecen estar en miles de COP (ej. 39,000,000 para un fondo mediano):
    dividir entre 1,000 para convertir a COP MM.
  * Si ya están en millones COP (MM): usar directamente.
  Aplicar la MISMA conversión a current_value y previous_value.
- Números negativos: (1,234) = -1234. Gastos y costos pueden ser negativos.
- Separador de miles: puede ser coma o punto según el documento.
- periods: inferir de los encabezados de columna. Formato YYYY-MM. Si no hay mes, usar -12.
  Solo reportar los DOS períodos seleccionados (current y previous), no todos los encabezados.
- Incluir SOLO cuentas materiales (máximo 60): totales de sección, subtotales clave,
  utilidad del período, aportes/retiros de inversionistas, instrumentos financieros principales.
  Omitir líneas de detalle menor que no aporten al análisis de variaciones.
- Omitir filas de encabezado, notas al pie y celdas sin valor numérico.
- confidence_score: 1.0 si valor y período son claros; 0.5 si hubo ambigüedad en columna
  o unidad; 0.2 si fue inferido o el período comparativo no es directamente comparable.
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


def _unwrap_retry_error(exc: Exception) -> str:
    """Surface the underlying cause of a tenacity RetryError.

    ``RetryError`` repr is opaque (``RetryError[<Future ... raised Exception>]``),
    hiding the real failure. Return the message of the last underlying exception.
    """
    if isinstance(exc, RetryError):
        last = exc.last_attempt
        if last is not None and last.failed:
            inner = last.exception()
            if inner is not None:
                return f"{type(inner).__name__}: {inner}"
    return f"{type(exc).__name__}: {exc}"


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
        max_tokens=32000,   # headroom: large fund workbooks emit ~15k JSON tokens
    )
    return result if isinstance(result, dict) else {"accounts": result, "periods": []}


# ---------------------------------------------------------------------------
# Account assembly
# ---------------------------------------------------------------------------

# Investment-type keywords: when previous_value=0.0 and current_value>1 COP MM for these,
# the LLM likely wrote 0 where null was correct (new position, not a zero balance).
_INVESTMENT_NEW_POSITION_KEYWORDS = (
    "inversión en", "inversion en", "acciones", "fondo", "bono",
    "patrimonio clase", "etf", "título", "titulo",
)
_NEW_POSITION_CURRENT_FLOOR_COP_MM = 1.0   # positions below this threshold keep 0.0 as-is


def _coerce_zero_previous_to_null(
    category: str,
    normalized_name: str,
    current_value: float,
    previous_value: float | None,
) -> float | None:
    """
    Convert previous_value=0.0 to None for investment accounts where 0 clearly
    means the position did not exist in the prior period.
    Accounts with genuine zero balances (e.g. small liabilities) are left unchanged.
    """
    if previous_value != 0.0 or previous_value is None:
        return previous_value
    if current_value < _NEW_POSITION_CURRENT_FLOOR_COP_MM:
        return previous_value  # too small to be confident
    name_lower = normalized_name.lower()
    if any(kw in name_lower for kw in _INVESTMENT_NEW_POSITION_KEYWORDS):
        return None
    return previous_value


def _build_accounts(raw_items: list[dict], source_file: str) -> tuple[list[ExtractedAccount], list[str]]:
    accounts: list[ExtractedAccount] = []
    warnings: list[str] = []
    for i, item in enumerate(raw_items):
        try:
            prev_raw = item.get("previous_value")
            category = str(item.get("category", "other"))
            normalized_name = str(item.get("normalized_account_name", ""))
            current_value = float(item.get("current_value") or 0)
            prev_float = float(prev_raw) if prev_raw is not None else None
            previous_value = _coerce_zero_previous_to_null(
                category, normalized_name, current_value, prev_float
            )
            raw_sheet = item.get("source_sheet")
            raw_inv_type = item.get("investment_type")
            raw_issuer = item.get("issuer_name")
            nominal_raw = item.get("nominal_value")
            nominal_value = float(nominal_raw) if nominal_raw is not None else None
            accounts.append(ExtractedAccount(
                account_id=f"act-{i+1:03d}",   # re-indexed globally after merge
                raw_account_name=str(item.get("raw_account_name", "")),
                normalized_account_name=normalized_name,
                category=category,
                current_value=current_value,
                previous_value=previous_value,
                currency="COP",
                confidence_score=min(1.0, max(0.0, float(item.get("confidence_score", 0.5)))),
                source_file=source_file,
                source_sheet=str(raw_sheet) if raw_sheet else None,
                is_total=bool(item.get("is_total", False)),
                investment_type=str(raw_inv_type) if raw_inv_type else None,
                issuer_name=str(raw_issuer) if raw_issuer else None,
                nominal_value=nominal_value,
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
    # Extraction is mechanical JSON normalization — use the cheaper model when
    # LLM_MODEL_EXTRACTOR is set (e.g. claude-haiku-4-5-20251001) instead of paying
    # Sonnet rates. Falls back to the global default when the env var is unset.
    llm = LLMProvider(model=os.getenv("LLM_MODEL_EXTRACTOR"))

    business_ctx_str = (
        f"Empresa: {payload.business_context.company_name or 'No especificada'}\n"
        f"Industria: {payload.business_context.industry or 'No especificada'}\n"
        f"Período: {payload.business_context.reporting_period or 'No especificado'}\n"
        f"Contexto: {payload.business_context.raw_context[:500]}"
    )

    all_accounts: list[ExtractedAccount] = []
    all_periods: list[str] = []
    all_fund_metadata: list[dict] = []
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
                
                # Save accountability report text alongside the uploaded files
                pdf_folder = "/".join(file.s3_location.rsplit("/", 1)[:-1])
                s3_key = f"{pdf_folder}/{payload.job_id}_rendicion.txt"
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

            # Collect fund metadata if the LLM detected it (non-null fields only)
            fm = llm_result.get("fund_metadata") or {}
            if any(v is not None for v in fm.values()):
                all_fund_metadata.append(fm)

            logger.info("accounts_extracted | file=%s count=%d", file.file_name, len(file_accounts))

        except Exception as e:
            cause = _unwrap_retry_error(e)
            warnings.append(f"Error procesando '{file.file_name}': {cause}")
            logger.error("extraction_error | file=%s error=%s", file.file_name, cause, exc_info=True)

    # Merge and deduplicate periods; keep the two most recent
    seen: set[str] = set()
    unique_periods = [p for p in all_periods if p not in seen and not seen.add(p)]
    unique_periods = sorted(unique_periods, reverse=True)[:2]

    # Fall back to business_context if LLM didn't detect periods
    if not unique_periods and payload.business_context.reporting_period:
        unique_periods = [payload.business_context.reporting_period]

    company_name = payload.business_context.company_name or ""
    reindexed_accounts = _reindex(all_accounts)

    niif_validation = validate_niif(reindexed_accounts, unique_periods)
    if not niif_validation.is_niif_compliant:
        logger.warning(
            "niif_not_compliant | job=%s score=%d errors=%d warnings=%d",
            payload.job_id,
            niif_validation.compliance_score,
            sum(1 for f in niif_validation.flags if f.severity == "ERROR"),
            sum(1 for f in niif_validation.flags if f.severity == "WARNING"),
        )

    # Merge fund metadata from all files — use the first non-empty result
    merged_fund_metadata: dict | None = all_fund_metadata[0] if all_fund_metadata else None

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
        accounts=reindexed_accounts,
        extraction_confidence=_avg_confidence(all_accounts),
        extraction_warnings=warnings,
        rendicion_text_s3_key=rendicion_text_s3_key,
        niif_validation=niif_validation,
        fund_metadata=merged_fund_metadata,
    )

    logger.info(
        "extractor_done | job=%s accounts=%d periods=%s confidence=%.2f warnings=%d",
        payload.job_id, len(result.accounts), result.periods,
        result.extraction_confidence, len(result.extraction_warnings),
    )

    output = result.model_dump(mode="json")

    try:
        job_save(result.job_id, EXTRACTOR, output, s3_client=s3_client)
    except Exception as exc:
        logger.warning("extractor_output_save_failed | job=%s error=%s", result.job_id, exc)

    return output