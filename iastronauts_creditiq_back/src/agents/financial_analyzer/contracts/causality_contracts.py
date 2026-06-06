from pydantic import BaseModel


class AccountCausality(BaseModel):
    account_id: str
    possible_causes: list[str]        # max 3 — specific causal explanations, never raw percentages
    executive_insight: str = ""       # one-liner for board display
    linked_accounts: list[str] = []   # account_ids causally related
    confidence: float = 0.8
    # Evidence First (Sprint 1 Item 2): each causal claim must cite its evidence source.
    # Format: [{"claim": "...", "evidence_type": "account|variation|news|policy|note", "ref": "..."}]
    # "ref" is an account_id, news headline identifier, policy clause id, or NIIF standard.
    # If no evidence exists, emit the refusal string as the single cause instead of speculating.
    evidence: list[dict] = []


class CrossAccountDynamic(BaseModel):
    explanation: str
    impacted_accounts: list[str]
    confidence: float = 0.7


class CausalityAnalysisResult(BaseModel):
    account_causality: list[AccountCausality] = []
    cross_account_dynamics: list[CrossAccountDynamic] = []
