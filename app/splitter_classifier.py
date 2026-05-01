"""Classifier-based splitter — uses an Azure DI **custom classifier** to detect
document boundaries and types in a single call.

Compare with `splitter.py` (heuristic, app-side) — same `Segment` output shape,
so the rest of the pipeline (extraction, telemetry, cost) is unchanged.

Pricing advantage: classifier is ~$3/1k pages vs ~$10/1k for `prebuilt-layout`.
Accuracy advantage: real ML, not keyword regex.
"""
from __future__ import annotations

from typing import Any

from .splitter import MODEL_BY_TYPE, Segment


def segments_from_classifier_result(result: Any) -> list[Segment]:
    """Convert a DI classify-document result into a list of Segments.

    DI's classifier returns one `documents[i]` per detected segment with:
      - doc_type  (matches one of the classifier's training class names)
      - bounding_regions[*].page_number  (1-based pages covered)
    """
    segments: list[Segment] = []
    for doc in (getattr(result, "documents", None) or []):
        pages = sorted({
            int(r.page_number)
            for r in (doc.bounding_regions or [])
            if getattr(r, "page_number", None) is not None
        })
        if not pages:
            continue
        doc_type = doc.doc_type or "unknown"
        # DI returns one document per contiguous span; trust its page range.
        segments.append(Segment(
            doc_type=doc_type,
            page_start=pages[0],
            page_end=pages[-1],
            model_id=MODEL_BY_TYPE.get(doc_type, MODEL_BY_TYPE["unknown"]),
        ))
    # Order by starting page just in case.
    segments.sort(key=lambda s: s.page_start)
    return segments
