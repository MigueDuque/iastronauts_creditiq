from pydantic import BaseModel


class AccountCausality(BaseModel):
    account_id: str
    possible_causes: list[str]        # max 3 — specific causal explanations, never raw percentages
    executive_insight: str = ""       # one-liner for board display
    linked_accounts: list[str] = []   # account_ids causally related
    confidence: float = 0.8


class CrossAccountDynamic(BaseModel):
    explanation: str
    impacted_accounts: list[str]
    confidence: float = 0.7


class CausalityAnalysisResult(BaseModel):
    account_causality: list[AccountCausality] = []
    cross_account_dynamics: list[CrossAccountDynamic] = []
