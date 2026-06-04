"""
niif_notes.py

Resolves a NIIF/NIC standard string (e.g. "NIIF 9", "NIC 12") to the authored
disclosure-note text for that standard.

Three-tier fallback, mirroring how system prompts are handled:
    S3 (instructions/niif_notes/{slug}.md, cached)  →  local repo file  →  ""

The slug is derived deterministically from the standard string so the same rule
is the single source of truth across the loader, the §1 file names, and any test
that asserts every standard in `_NIIF_MAP` has a corresponding note file:

    "NIIF 9"  -> "niif_9"
    "NIC 12"  -> "nic_12"

Never raises — a missing note returns "" so the pipeline never blocks.
"""

import logging
import os

from .s3_instructions import load_text

logger = logging.getLogger("shared.niif_notes")

_S3_PREFIX = "instructions/niif_notes/"
_LOCAL_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "agents", "system_pompts", "niif_notes")
)

# In-process cache: slug → resolved note text (including resolved-to-"" misses).
_cache: dict[str, str] = {}


def slug_for_standard(standard: str) -> str:
    """Derive the canonical file slug for a NIIF/NIC standard string.

    "NIIF 9" -> "niif_9", "NIC 12" -> "nic_12". Lowercase, spaces → underscores,
    collapsing any internal whitespace runs.
    """
    return "_".join(standard.strip().lower().split())


def load_niif_note(standard: str) -> str:
    """Return the authored note text for a standard, or "" when none is available."""
    slug = slug_for_standard(standard)
    if not slug:
        return ""
    if slug in _cache:
        return _cache[slug]

    text = load_text(f"{_S3_PREFIX}{slug}.md", fallback="")
    if not text:
        local_path = os.path.join(_LOCAL_DIR, f"{slug}.md")
        try:
            with open(local_path, encoding="utf-8") as f:
                text = f.read()
            logger.info("niif_note | standard=%s source=local chars=%d", standard, len(text))
        except OSError:
            text = ""
            logger.info("niif_note | standard=%s source=missing", standard)
    else:
        logger.info("niif_note | standard=%s source=s3 chars=%d", standard, len(text))

    _cache[slug] = text
    return text


def clear_cache() -> None:
    """Evict all cached note text. Intended for tests only."""
    _cache.clear()
