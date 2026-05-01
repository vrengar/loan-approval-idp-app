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
    doc_type: str
    page_start: int  # 1-based inclusive
    page_end: int    # 1-based inclusive
    model_id: str


def classify_page(text: str) -> str:
    t = text.lower()
    scores = {k: sum(1 for kw in v if kw in t) for k, v in KEYWORDS.items()}
    best_type, best_score = max(scores.items(), key=lambda kv: kv[1])
    return best_type if best_score > 0 else "unknown"


def segments_from_page_types(page_types: Iterable[str]) -> list[Segment]:
    segments: list[Segment] = []
    current: Segment | None = None
    for idx, ptype in enumerate(page_types, start=1):
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
            current.page_end = idx
    if current is not None:
        segments.append(current)
    return segments
