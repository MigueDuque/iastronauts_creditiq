from pydantic import BaseModel


class StrategicShift(BaseModel):
    shift_type: str     # defensive | growth | de-risking | rotation | liquidity_stress | concentration_increase
    explanation: str
    confidence: float = 0.8


class FinancialThesisResult(BaseModel):
    portfolio_thesis: str = ""
    strategic_shifts: list[StrategicShift] = []
    investment_implications: list[str] = []
    overall_financial_health: str = "STABLE"
    earnings_sustainability: str = ""           # STRONG | MODERATE | WEAK
    insight_tiers: dict = {}                    # tier1_critical / tier2_material
    structured_analysis: dict = {}              # 6-section breakdown
    cross_statement_signals: list[dict] = []
