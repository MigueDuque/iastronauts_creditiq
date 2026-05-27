from pydantic import BaseModel


class KeyMovement(BaseModel):
    account_id: str
    movement_type: str  # capital_inflow | capital_outflow | position_elimination | new_position | concentration_shift | valuation_change
    direction: str      # increase | decrease | new | closed
    magnitude: float    # absolute variation in COP MM
    summary: str        # compact factual description, no raw percentages
    confidence: float = 0.8


class PortfolioRotation(BaseModel):
    from_assets: list[str]
    to_assets: list[str]
    rationale: str
    confidence: float = 0.7


class MovementIntelligenceResult(BaseModel):
    key_movements: list[KeyMovement] = []
    portfolio_rotations: list[PortfolioRotation] = []
    suspicious_patterns: list[str] = []
