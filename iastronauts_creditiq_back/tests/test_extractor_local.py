"""
Test local del DocumentExtractor (Agente 1) sin necesidad de AWS.
Valida las tres capas: parsing de Excel, normalización LLM, ensamblado de cuentas.

Uso:
    cd iastronauts_creditiq_back
    python tests/test_extractor_local.py
"""
import os
import sys
from pathlib import Path

# ── paths ────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "iastronauts_creditiq_back" / "src"
TEST_FILES = REPO_ROOT / "test_files"
ENV_FILE = REPO_ROOT / "iastronauts_creditiq_back" / ".env"

sys.path.insert(0, str(SRC_DIR))

# Load .env before importing modules that read env vars
from dotenv import load_dotenv
load_dotenv(ENV_FILE)
os.environ.setdefault("MAIN_BUCKET", "test-bucket-local")

# ── imports (after path setup) ────────────────────────────────────────────────
from agents.document_extractor.handler import (
    _build_accounts,
    _call_llm_extraction,
    extract_excel,
)
from shared.llm_provider import LLMProvider


# ── helpers ───────────────────────────────────────────────────────────────────

def _sep(title: str) -> None:
    print(f"\n{'-' * 60}")
    print(f"  {title}")
    print('-' * 60)


def _check(label: str, condition: bool) -> None:
    icon = "[OK]" if condition else "[FAIL]"
    print(f"  {icon}  {label}")
    if not condition:
        raise AssertionError(f"FALLO: {label}")


# ── test 1: Excel parsing ─────────────────────────────────────────────────────

def test_excel_parsing() -> str:
    _sep("CAPA 1 — Parsing de Excel (pandas)")
    xlsx_path = TEST_FILES / "EEFF_BTGPactual_COMPLETO.xlsx"
    _check("Archivo existe", xlsx_path.exists())

    raw_text = extract_excel(xlsx_path.read_bytes())

    _check("raw_text no está vacío", bool(raw_text.strip()))
    _check("Contiene al menos una hoja", "=== Hoja:" in raw_text)
    _check("Contiene datos numéricos (dígitos)", any(c.isdigit() for c in raw_text))

    print(f"\n  Caracteres extraídos : {len(raw_text):,}")
    print(f"  Hojas detectadas     : {raw_text.count('=== Hoja:')}")
    print(f"\n  Primeros 600 chars del raw_text:\n")
    print("  " + raw_text[:600].replace("\n", "\n  "))
    return raw_text


# ── test 2: LLM normalization ─────────────────────────────────────────────────

def test_llm_normalization(raw_text: str) -> dict:
    _sep("CAPA 2 — Normalización LLM (homologación NIIF)")
    llm = LLMProvider()
    business_ctx = (
        "Empresa: Fondo de Inversión Colectiva Abierto BTG Pactual Acciones Colombia\n"
        "Industria: Fondo de inversión colectiva / Renta variable colombiana\n"
        "Período: Junio 2025"
    )

    result = _call_llm_extraction(
        raw_text=raw_text,
        business_ctx_str=business_ctx,
        llm=llm,
        tenant_id="test-tenant",
        job_id="test-job-001",
    )

    _check("LLM devolvió un dict", isinstance(result, dict))
    _check("'accounts' presente en resultado", "accounts" in result)
    _check("'periods' presente en resultado", "periods" in result)
    _check("'statement_type' presente en resultado", "statement_type" in result)
    _check("Al menos 1 cuenta extraída", len(result.get("accounts", [])) >= 1)

    accounts = result["accounts"]
    print(f"\n  statement_type : {result.get('statement_type')}")
    print(f"  periods        : {result.get('periods')}")
    print(f"  cuentas        : {len(accounts)}")
    print(f"\n  Primeras 5 cuentas:")
    for acc in accounts[:5]:
        prev = acc.get('previous_value')
        prev_str = f"{prev:,.1f}" if prev is not None else "N/A"
        print(
            f"    [{acc.get('category','?'):<10}] "
            f"{acc.get('normalized_account_name','?'):<45} "
            f"curr={acc.get('current_value',0):>12,.1f}  prev={prev_str:>12}  "
            f"conf={acc.get('confidence_score',0):.2f}"
        )

    return result


# ── test 3: Account assembly ──────────────────────────────────────────────────

def test_account_assembly(llm_result: dict) -> None:
    _sep("CAPA 3 — Ensamblado de ExtractedAccount (Pydantic)")
    raw_accounts = llm_result.get("accounts", [])
    accounts, warnings = _build_accounts(raw_accounts, source_file="EEFF_BTGPactual_COMPLETO.xlsx")

    _check("Al menos 1 cuenta ensamblada", len(accounts) >= 1)
    _check("account_id sigue patrón act-NNN", accounts[0].account_id.startswith("act-"))
    _check("current_value es float", isinstance(accounts[0].current_value, float))
    _check("confidence_score en rango [0,1]", 0.0 <= accounts[0].confidence_score <= 1.0)
    _check("source_file asignado", accounts[0].source_file == "EEFF_BTGPactual_COMPLETO.xlsx")

    if warnings:
        print(f"\n  Warnings de parseo ({len(warnings)}):")
        for w in warnings:
            print(f"    [!] {w}")
    else:
        print("\n  Sin warnings de parseo.")

    # Cobertura de categorías
    categories = {a.category for a in accounts}
    print(f"\n  Cuentas ensambladas : {len(accounts)}")
    print(f"  Categorías presentes: {sorted(categories)}")
    conf_avg = sum(a.confidence_score for a in accounts) / len(accounts)
    print(f"  Confianza promedio  : {conf_avg:.3f}")

    # Tabla de resumen
    print(f"\n  {'ID':<9} {'Categoría':<12} {'Nombre NIIF':<45} {'Valor (COP MM)':>14}")
    print(f"  {'-'*9} {'-'*12} {'-'*45} {'-'*14}")
    for a in accounts:
        print(
            f"  {a.account_id:<9} {a.category:<12} "
            f"{a.normalized_account_name[:44]:<45} "
            f"{a.current_value:>14,.1f}"
        )


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  TEST -- DocumentExtractor (Agente 1) -- modo local")
    print("=" * 60)

    try:
        raw_text = test_excel_parsing()
        llm_result = test_llm_normalization(raw_text)
        test_account_assembly(llm_result)

        print(f"\n{'=' * 60}")
        print("  [OK] Todas las capas validadas correctamente")
        print("=" * 60 + "\n")

    except AssertionError as e:
        print(f"\n  [FAIL] {e}\n")
        sys.exit(1)
    except Exception as e:
        import traceback
        print(f"\n  [ERROR] {e}\n")
        traceback.print_exc()
        sys.exit(1)
