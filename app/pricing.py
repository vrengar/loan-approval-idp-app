"""Azure Document Intelligence unit prices used by this app.

Source of truth (verified 2026-04-30):
    https://azure.microsoft.com/pricing/details/document-intelligence/
Tier: S0 Pay-as-you-go (Web), USD per 1,000 pages.

Mapping of meters this app actually invokes:
    prebuilt-read         -> "Read" meter           $1.50/1k pages (0-1M/mo;
                                                    drops to $0.60/1k beyond 1M)
    prebuilt-layout       -> "Layout / Prebuilt"    $10/1k pages
    prebuilt-idDocument   -> "Prebuilt"             $10/1k pages
    prebuilt-tax.us.w2    -> "Prebuilt"             $10/1k pages
    classifier            -> "Custom classification" $3/1k pages
    custom (extraction)   -> "Custom extraction"    $30/1k pages

Not modeled (not used by this app today):
    Add-On features ............ $6/1k     (high-res OCR, formula, barcode, KVP, language)
    Query Fields ............... $10/1k
    Custom generative extraction $30/1k
    Training ................... $3/hr
    Batch / Commitment Tier discounts
    Free tier .................. first 500 pages/month free (S0)

Caveats:
    - These are list prices. EA/MCA/CSP discounts make actual invoice lower.
    - This is an in-app *estimate* for live per-tenant attribution and the
      heuristic-vs-classifier comparison view. Authoritative dollars come from
      Azure Cost Management 8-24h after the call (meter category
      "Azure AI Document Intelligence").
"""

# USD per 1,000 pages (S0 PAYG list price)
UNIT_PRICE_PER_1K_PAGES = {
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

    # Custom classification — used to detect doc boundaries + types in classifier mode
    "classifier":            3.00,

    # Custom extraction — used when you swap in a trained custom model
    # (paystub, bank statement, etc.). Not invoked by current MODEL_BY_TYPE mapping.
    "custom":               30.00,
}


def estimate_cost_usd(model_id: str, pages: int) -> float:
    """Return an approximate USD cost for `pages` processed by `model_id`.

    Falls back to the prebuilt/layout rate ($10/1k) for unknown model ids
    (e.g., custom model resource ids that don't match the table above).
    """
    price = UNIT_PRICE_PER_1K_PAGES.get(model_id, UNIT_PRICE_PER_1K_PAGES["prebuilt-layout"])
    return round((pages / 1000.0) * price, 6)
