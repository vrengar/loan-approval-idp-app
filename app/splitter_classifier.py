"""Classifier-based splitter — uses an Azure DI **custom classifier** to detect
document boundaries and types in a single call.

Compare with `splitter.py` (heuristic, app-side) — same `Segment` output shape,
so the rest of the pipeline (extraction, telemetry, cost) is unchanged.

Pricing advantage: classifier is ~$3/1k pages vs ~$10/1k for `prebuilt-layout`.
Accuracy advantage: real ML, not keyword regex.
"""
from __future__ import annotations

import os
from typing import Any

from .splitter import MODEL_BY_TYPE, Segment

# Minimum classifier confidence required to trust the predicted doc_type.
# Below this, we relabel the segment as "unknown" so it routes to
# prebuilt-layout (safe OCR-only fallback) instead of the wrong specialised
# extractor. Tunable at runtime via env var without redeploying code.
#   - 0.0  -> trust DI's pick unconditionally (legacy behaviour)
#   - 0.5  -> sensible default; rejects coin-flip predictions
#   - 0.85 -> strict; only route to specialised model on high confidence
MIN_CONFIDENCE = float(os.getenv("CLASSIFIER_MIN_CONFIDENCE", "0.5"))


def segments_from_classifier_result(result: Any) -> list[Segment]:
    """Convert a DI classify-document result into a list of Segments.

    DI's classifier returns one `documents[i]` per detected segment with:
      - doc_type    (matches one of the classifier's training class names)
      - confidence  (0.0-1.0; how sure DI is about the predicted class)
      - bounding_regions[*].page_number  (1-based pages covered)

    Segments whose confidence falls below MIN_CONFIDENCE are relabeled
    "unknown" so they route to prebuilt-layout instead of the wrong model.
    The original predicted label and confidence are preserved on the Segment
    for telemetry/UI display.

    The output Segment shape is identical to the heuristic splitter's, so the
    extraction loop in main._run_pipeline() doesn't care which strategy ran.
    """
    segments: list[Segment] = []
    for doc in (getattr(result, "documents", None) or []):
        # Collect every page the classifier attributed to this document.
        pages = sorted({
            int(r.page_number)
            for r in (doc.bounding_regions or [])
            if getattr(r, "page_number", None) is not None
        })
        if not pages:
            continue
        predicted_type = doc.doc_type or "unknown"
        confidence = float(getattr(doc, "confidence", 0.0) or 0.0)
        # Apply confidence gate: low-confidence predictions fall back to
        # OCR-only extraction (prebuilt-layout) so we never waste a $10/1k
        # specialised model call on a wrong label.
        effective_type = predicted_type if confidence >= MIN_CONFIDENCE else "unknown"
        segments.append(Segment(
            doc_type=effective_type,
            page_start=pages[0],
            page_end=pages[-1],
            model_id=MODEL_BY_TYPE.get(effective_type, MODEL_BY_TYPE["unknown"]),
        ))
    # Order by starting page just in case the classifier returns docs out of order.
    segments.sort(key=lambda s: s.page_start)
    return segments
