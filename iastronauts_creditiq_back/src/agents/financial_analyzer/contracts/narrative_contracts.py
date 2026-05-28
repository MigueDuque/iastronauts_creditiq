from pydantic import BaseModel


class ExecutiveNarrativeResult(BaseModel):
    executive_narrative: str = ""
    board_summary: str = ""
    investor_takeaways: list[str] = []
    niif_notes_required: list[str] = []
    narrative_layers: dict = {}         # executive / tactical / technical
