# IDP Demo — Loan Application Review with Azure Document Intelligence

A Python (FastAPI) demo that processes **merged loan-application PDFs** (paystub + bank statement + W-2) using **Azure AI Document Intelligence (DI)** on an **Azure AI Services** account. Demonstrates:

- Multi-document PDF splitting (heuristic **and** trained DI custom classifier `idp-loan-docs-v1`)
- Prebuilt model routing (W-2, layout, ID document)
- Confidence scoring & per-field telemetry
- Per-tenant page-count + cost telemetry for SaaS cost allocation
- Side-by-side compare mode (heuristic vs classifier) with KPI + savings UI
- Azure Load Testing scenario for throughput/cost projection

## Use cases covered in the demo
- Data extraction from paystubs, bank statements, W-2s, tax returns
- Document classification + routing of merged PDFs
- Multi-document boundary detection inside a single PDF
- Per-tenant usage and cost attribution for SaaS chargeback / billing

> Full technical spec: [`docs/SPEC.md`](docs/SPEC.md). Classifier walkthrough: [`docs/custom-classifier.md`](docs/custom-classifier.md).

---

## Architecture (at a glance)

```
Client ──POST /process (x-tenant-id, PDF)──▶ FastAPI (ca-api-demo)
                                              │
                                              ├─▶ Azure AI Services (DI) — split + extract
                                              ├─▶ Application Insights — di.pages.processed
                                              └─▶ User-Assigned Managed Identity (no keys)
                                                    ├─ Cognitive Services User → AI account
                                                    └─ AcrPull → Container Registry
```

### Two-stage billing model

Each request bills DI **twice** for traceable per-segment extraction:

| Stage | Heuristic mode | Classifier mode |
|---|---|---|
| **1 — split** | one `prebuilt-layout` call over the full PDF | one `idp-loan-docs-v1` classifier call over the full PDF |
| **2 — extract** | one call per segment, model chosen by doc type | one call per segment, model chosen by classifier label |

`billedPages = totalPages (stage 1) + Σ segmentPages (stage 2)`. Compare mode runs both pipelines and reports the dollar difference.

---

## 1. Local quickstart

```powershell
python -m venv .venv
. .venv/Scripts/Activate.ps1
pip install -r requirements.txt

# Generate the demo PDF (paystub + bank statement + W-2 merged)
python -m app.sample.generate_sample_pdf

# Set env vars (or copy .env.example -> .env)
$env:DI_ENDPOINT = "https://<your-ai-services>.cognitiveservices.azure.com/"
# Auth: leave DI_KEY unset to use Azure AD (DefaultAzureCredential / `az login`).
# Only set DI_KEY for local key-based testing; production uses Managed Identity.
# $env:DI_KEY = "<key>"
$env:TENANT_ID_HEADER = "x-tenant-id"   # SaaS tenant header
$env:CLASSIFIER_ID    = "idp-loan-docs-v1"  # optional: enables classifier split mode

uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 — upload `samples/loan_application_demo.pdf`.

## 2. Deploy to Azure

Targets the subscription you select with `az account set`. Resource Group defaults to `IDP-rg` (override in `infra/main.parameters.json`).

```powershell
az login
az account set --subscription <your-subscription-id>
azd up   # uses infra/main.bicep + azure.yaml
```

What gets deployed:
- Azure AI Services account (S0) — exposes the Document Intelligence APIs
- Azure Container Registry + Container App (the Python API), pulled via User-Assigned Managed Identity
- Log Analytics + Application Insights (telemetry)
- Storage account (training data + uploaded PDFs), Managed Identity only (no shared keys)
- User-Assigned Managed Identity with `Cognitive Services User` on the AI account and `AcrPull` on the registry
- All resources tagged: `app=idp-demo`, `costcenter=loan-ops`, `env=demo`

Runtime auth: the Container App reads `DI_ENDPOINT` + `AZURE_CLIENT_ID` and authenticates to DI with `DefaultAzureCredential` — no keys in config.

## 3. Load testing & cost projection

See [`loadtest/README.md`](loadtest/README.md). Two paths:

- **Simple**: Azure Load Testing service runs the JMX, then Cost Management filtered by tag `app=idp-demo` shows actual spend.
- **Advanced**: Each DI call emits an App Insights `traces` record `di.pages.processed` with `tenantId`, `model`, `pageCount`, `estimatedCostUsd`, `durationMs`, `splitStrategy`, `stage`, `correlationId`. KQL in [`loadtest/cost-allocation.kql`](loadtest/cost-allocation.kql) rolls these up per tenant / strategy / model.

## 4. Project structure

```
app/
  main.py                  # FastAPI app + UI (compare mode)
  di_client.py             # DI SDK wrapper (Managed Identity by default)
  splitter.py              # heuristic boundary detection + model routing
  splitter_classifier.py   # DI custom classifier split path
  telemetry.py             # App Insights `di.pages.processed` events
  pricing.py               # DI unit prices for cost allocation (S0 list)
  sample/generate_sample_pdf.py
infra/
  main.bicep               # subscription-scoped IaC
  modules/resources.bicep  # RG-scoped resources (AIServices DI, ACA, MI, ACR, AppI, LAW, SA)
  main.parameters.json
scripts/
  train_classifier.ps1     # builds + deploys the `idp-loan-docs-v1` classifier
samples/
  loan_application_demo.pdf
  training/                # 5 doc types x 8 PDFs for classifier training
loadtest/
  loadtest.jmx
  loadtest-config.yaml
  cost-allocation.kql      # per-tenant rollup (prices mirror app/pricing.py)
docs/
  custom-classifier.md
azure.yaml                 # azd config
Dockerfile
requirements.txt
```

---

## 5. API surface

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/`         | Single-page UI (upload, KPI cards, side-by-side compare, fields tables, raw JSON) |
| `GET`  | `/healthz`  | Liveness — `{ status: "ok", classifierConfigured: bool }` |
| `POST` | `/process`  | Multipart `file`; query `mode = heuristic \| classifier \| compare`; header `x-tenant-id` |

Abbreviated response:

```jsonc
{
  "correlationId": "…",
  "tenantId": "acme-corp",
  "strategy": "classifier",
  "totalPages": 7,
  "billedPages": 14,
  "estimatedCostUsd": 0.091,
  "segments": [
    { "docType": "paystub", "pageStart": 1, "pageEnd": 2, "model": "prebuilt-layout",
      "pageCount": 2, "estimatedCostUsd": 0.020, "durationMs": 4231,
      "fields": [ { "name": "GrossPay", "value": "…", "confidence": 0.96 } ] }
  ],
  "compare": { "heuristic": { "...": "..." }, "classifier": { "...": "..." }, "savingsUsd": 0.049, "savingsPct": 35.0 }
}
```

---

## 6. Telemetry contract

Every DI call emits an App Insights `traces` row (`message == "di.pages.processed"`) with these `customDimensions`:

| Field | Description |
|---|---|
| `tenantId` | from `x-tenant-id` header (`unknown` if absent) |
| `correlationId` | per-request UUID — joins stage-1 + stage-2 calls |
| `splitStrategy` | `heuristic` \| `classifier` |
| `stage` | `split` (stage 1) or empty (stage 2 segment) |
| `model` | DI model id (`prebuilt-layout`, `prebuilt-tax.us.w2`, `prebuilt-idDocument`, `idp-loan-docs-v1`, …) |
| `docType` | `paystub`, `bank_statement`, `w2`, `passport`, `drivers_license`, `unknown` |
| `pageStart` / `pageEnd` / `pageCount` | segment range + pages billed |
| `estimatedCostUsd` | `pages/1000 × app/pricing.py` rate |
| `durationMs` | DI call wall-time |

Reference rollup: [`loadtest/cost-allocation.kql`](loadtest/cost-allocation.kql).

---

## 7. Pricing (DI S0 list, USD per 1k pages)

Source of truth: [`app/pricing.py`](app/pricing.py). KQL prices in `loadtest/cost-allocation.kql` mirror this table — they must be updated together.

| Model id | $ / 1k pages |
|---|---|
| `prebuilt-read` | 1.50 |
| `prebuilt-layout` | 10.00 |
| `prebuilt-idDocument` | 10.00 |
| `prebuilt-tax.us.w2` / `1098` / `1099` | 10.00 |
| `custom` | 30.00 |
| `classifier` | 3.00 |

Caveats: list price only; commitment / batch / regional discounts, free tier, and add-ons (Query Fields, Generative) are not modeled.

---

## 8. Operational considerations

- **Telemetry lag:** ~30–90 s App Insights, 8–24 h Cost Management.
- **Reconciliation:** monthly compare app-emitted `estimatedCostUsd` (KQL) vs Cost Management invoice for the AI account; drift > a few % indicates traffic outside the instrumented path (training, retries, another consumer).
- **Retention:** LAW defaults to 30 days; for billing-grade audit, configure LAW *Data Export* on `AppTraces` → Storage container.
- **Diagnostic settings:** recommended `AllMetrics` + `RequestResponse` on the AI account → same LAW for platform-side reconciliation (`AzureMetrics.TotalCalls`).
- **Multi-tenancy:** shared AI account; per-tenant breakdown comes from `tenantId` in app telemetry, not from Cost Management. Per-tenant AI accounts are an option at higher cost floor.

---

## 9. Roadmap (not implemented)

| Level | Idea | Expected impact |
|---|---|---|
| 1 | Reuse stage-1 `prebuilt-layout` result for stage-2 layout-routed segments | ~36 % cost reduction (heuristic mode, demo PDF) |
| 2 | Use `prebuilt-read` ($1.50/1k) instead of `prebuilt-layout` for stage-1 split | Cheaper splitter when layout structure not needed |
| 3 | `asyncio.gather()` per-segment extraction calls | Latency reduction, no cost change |
| 4 | LAW Data Export of `AppTraces` to Storage | Billing audit retention |
| 5 | AI account diagnostic settings → LAW | Detect drift between app and platform metrics |
| 6 | Workbook + Azure Dashboard for per-tenant cost / latency / drift | Single pane of glass for ops |

---

## 10. Tags

All resources are tagged for cost allocation & inventory:

| Tag | Value |
|---|---|
| `app` | `idp-demo` |
| `costcenter` | `loan-ops` |
| `env` | `demo` |
