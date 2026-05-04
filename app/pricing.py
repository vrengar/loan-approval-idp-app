"""Azure Document Intelligence + Content Understanding unit prices used by this app.

Source of truth (verified 2026-04-30):
    https://azure.microsoft.com/pricing/details/document-intelligence/
    https://azure.microsoft.com/pricing/details/content-understanding/
Tier: S0 Pay-as-you-go (Web), USD per 1,000 pages unless noted.

Mapping of meters this app actually invokes:
    prebuilt-read         -> "Read" meter           $1.50/1k pages (0-1M/mo;
                                                    drops to $0.60/1k beyond 1M)
    prebuilt-layout       -> "Layout / Prebuilt"    $10/1k pages
    prebuilt-idDocument   -> "Prebuilt"             $10/1k pages
    prebuilt-tax.us.w2    -> "Prebuilt"             $10/1k pages
    classifier            -> "Custom classification" $3/1k pages
    custom (extraction)   -> "Custom extraction"    $30/1k pages

    cu.prebuilt           -> CU prebuilt analyzer  ~$30/1k pages (pro tier estimate)
    cu.custom             -> CU custom analyzer    ~$40/1k pages (pro + LLM grounding)
    cu.classifier         -> CU classification      ~$3/1k pages

Not modeled (not used by this app today):
    Add-On features ............ $6/1k     (high-res OCR, formula, barcode, KVP, language)
    Query Fields ............... $10/1k
    Custom generative extraction $30/1k
    Training ................... $3/hr
    Batch / Commitment Tier discounts
    Free tier .................. first 500 pages/month free (S0)

Caveats:
    - These are list prices. EA/MCA/CSP discounts make actual invoice lower.
    - CU prices are per-page **estimates** for the demo; verify against the
      Content Understanding pricing page before using in customer chargeback.
    - This is an in-app *estimate* for live per-tenant attribution and the
      heuristic-vs-classifier-vs-cu comparison view. Authoritative dollars come
      from Azure Cost Management 8-24h after the call.
"""

# Bumped whenever any number in UNIT_PRICE_PER_1K_PAGES changes. Stamped on
# every emitted telemetry row so historical cost queries are reproducible.
PRICE_VERSION = "2026-05-04"

# ---------------------------------------------------------------------------
# Unit price table — USD per 1,000 pages, S0 Pay-As-You-Go list price.
# Keep this in sync with loadtest/cost-allocation.kql (the KQL `unitPrice`
# datatable mirrors this dict for off-line analytics).
# ---------------------------------------------------------------------------
UNIT_PRICE_PER_1K_PAGES = {
    # ---- Document Intelligence (DI) ----
    # Read OCR — used only if you call prebuilt-read directly (not in default path)
    "prebuilt-read":         1.50,

    # Layout / generic prebuilt meter — used by heuristic splitter for the initial
    # full-document layout pass and as fallback for paystub/bank_statement/unknown
    "prebuilt-layout":      10.00,

    # Prebuilt domain models — same $10/1k meter as Layout
    "prebuilt-idDocument":  10.00,
    "prebuilt-tax.us.w2":   10.00,
    "prebuilt-tax.us.1098": 10.00,
    "prebuilt-tax.us.1099": 10.00,
    "prebuilt-payStub.us":      10.00,
    "prebuilt-bankStatement.us": 10.00,

    # Custom classification — used to detect doc boundaries + types in classifier mode.
    # This is the headline savings driver: ~70% cheaper than prebuilt-layout for the split pass.
    "classifier":            3.00,

    # Custom extraction — used when you swap in a trained custom model
    # (paystub, bank statement, etc.). Not invoked by current MODEL_BY_TYPE mapping.
    "custom":               30.00,

    # ---- Content Understanding (CU) — estimates, refine before chargeback ----
    # CU prebuilt analyzers (prebuilt-payStub.us, prebuilt-bankStatement.us,
    # prebuilt-tax.us.w2, prebuilt-idDocument, prebuilt-mortgage.us.*).
    "cu.prebuilt":          30.00,
    # Custom analyzer with extract + generate fields (LLM-grounded derived values).
    "cu.custom":            40.00,
    # CU classification analyzer (used if we author a CU router analyzer).
    "cu.classifier":         3.00,
}


def estimate_cost_usd(model_id: str, pages: int) -> float:
    """Return an approximate USD cost for `pages` processed by `model_id`.

    Called in two places:
      1. Inline in main.py so the JSON response includes per-segment cost.
      2. Inside telemetry.emit_pages_processed so App Insights traces carry
         `estimatedCostUsd` as a queryable customDimension for chargeback.

    Falls back to the prebuilt/layout rate ($10/1k) for unknown model ids
    (e.g., custom model resource ids that don't match the table above).
    """
    price = UNIT_PRICE_PER_1K_PAGES.get(model_id, UNIT_PRICE_PER_1K_PAGES["prebuilt-layout"])
    return round((pages / 1000.0) * price, 6)



def estimate_cost_usd(model_id: str, pages: int) -> float:
    """Return an approximate USD cost for `pages` processed by `model_id`.

    Called in two places:
      1. Inline in main.py so the JSON response includes per-segment cost.
      2. Inside telemetry.emit_pages_processed so App Insights traces carry
         `estimatedCostUsd` as a queryable customDimension for chargeback.

    Falls back to the prebuilt/layout rate ($10/1k) for unknown model ids
    (e.g., custom model resource ids that don't match the table above).
    """
    price = UNIT_PRICE_PER_1K_PAGES.get(model_id, UNIT_PRICE_PER_1K_PAGES["prebuilt-layout"])
    return round((pages / 1000.0) * price, 6)
