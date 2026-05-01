"""Heuristic document-boundary splitter for merged loan-application PDFs.

Approach:
  1. Run `prebuilt-layout` on the entire PDF (one DI call returns text per page).
  2. Use keyword signatures to classify each page (paystub / bank statement / W-2 / unknown).
  3. Group consecutive pages of the same type into segments and route each
     segment to the most appropriate DI prebuilt model.

For production: replace step (2) with an Azure DI **custom classifier**, which
gives boundary detection + document type in a single managed call.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

# ---------------------------------------------------------------------------
# Keyword signatures used to classify each page by document type.
# These are intentionally simple — the goal is to demo the heuristic approach
# and contrast it with the custom-classifier strategy in splitter_classifier.py.
# In production: replace with a trained classifier (more accurate AND cheaper).
# ---------------------------------------------------------------------------
KEYWORDS = {
    "w2": [
        "form w-2",
        "wage and tax statement",
        "wages, tips, other compensation",
        "employer identification number",
    ],
    "paystub": [
        "earnings statement",
        "pay stub",
        "pay period",
        "gross pay",
        "net pay",
        "ytd",
        "year to date",
    ],
    "bank_statement": [
        "account statement",
        "beginning balance",
        "ending balance",
        "transaction history",
        "statement period",
    ],
    "passport": [
        "passport",
        "united states of america",
        "place of birth",
        "date of issue",
        "date of expiration",
        "type/type",
    ],
    "drivers_license": [
        "driver license",
        "driver's license",
        "dl no",
        "class c",
        "endorsements",
        "restrictions",
        "issued",
        "expires",
    ],
}

# Routing table: doc_type -> Azure DI model used to extract structured fields.
# Both splitters (heuristic + classifier) emit `Segment` objects whose model_id
# is looked up here. Swap individual entries to point at trained custom models
# without touching the pipeline.
MODEL_BY_TYPE = {
    "w2": "prebuilt-tax.us.w2",
    "paystub": "prebuilt-layout",       # swap to a custom paystub model in prod
    "bank_statement": "prebuilt-layout",  # swap to a custom bank-stmt model in prod
    "passport": "prebuilt-idDocument",
    "drivers_license": "prebuilt-idDocument",
    "unknown": "prebuilt-layout",
}


@dataclass
class Segment:
    """A contiguous run of pages classified as one document type.

    Both splitter strategies converge on this shape, so the rest of the
    pipeline (extraction loop, telemetry, cost) is strategy-agnostic.
    """
    doc_type: str
    page_start: int  # 1-based inclusive
    page_end: int    # 1-based inclusive
    model_id: str    # DI model used to extract fields from this segment


def classify_page(text: str) -> str:
    """Score `text` against each KEYWORDS bucket; return the best-matching type.

    Returns "unknown" when no keywords match (fallback model is prebuilt-layout).
    """
    t = text.lower()
    # One score per doc_type = number of distinct keywords found on the page.
    scores = {k: sum(1 for kw in v if kw in t) for k, v in KEYWORDS.items()}
    best_type, best_score = max(scores.items(), key=lambda kv: kv[1])
    return best_type if best_score > 0 else "unknown"


def segments_from_page_types(page_types: Iterable[str]) -> list[Segment]:
    """Collapse a per-page list like [paystub, paystub, bank, bank, w2] into
    Segment objects with page_start/page_end ranges.
    """
    segments: list[Segment] = []
    current: Segment | None = None
    for idx, ptype in enumerate(page_types, start=1):
        # New segment whenever the doc_type changes (or on the first page).
        if current is None or ptype != current.doc_type:
            if current is not None:
                segments.append(current)
            current = Segment(
                doc_type=ptype,
                page_start=idx,
                page_end=idx,
                model_id=MODEL_BY_TYPE.get(ptype, MODEL_BY_TYPE["unknown"]),
            )
        else:
            # Same type as previous page -> extend the current segment.
            current.page_end = idx
    if current is not None:
        segments.append(current)
    return segments
