"""FastAPI app: upload a merged loan-application PDF, get per-document extraction."""
from __future__ import annotations

import io
import logging
import time
import uuid
from typing import Literal

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pypdf import PdfReader, PdfWriter

from .config import settings
from .cu_client import CUClient
from .di_client import DIClient
from .pricing import estimate_cost_usd
from .splitter import classify_page, segments_from_page_types
from .splitter_classifier import segments_from_classifier_result
from .telemetry import (
    configure as configure_telemetry,
    emit_cu_call_processed,
    emit_pages_processed,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("idp.api")

# Wire OTel -> App Insights at import time so even startup logs are captured.
configure_telemetry()
app = FastAPI(title="IDP Demo — Loan Application Review")

# Three strategies are supported on /process. "compare" runs heuristic+classifier
# back-to-back (the original 2-way savings story). "cu" runs Content Understanding.
SplitStrategy = Literal["heuristic", "classifier", "cu"]

# CU prebuilt analyzer routing. Mirrors splitter.MODEL_BY_TYPE but points to
# Content Understanding analyzer ids instead of DI prebuilt model ids.
# The ids below are confirmed available on the existing Foundry account.
CU_ANALYZER_BY_TYPE: dict[str, str] = {
    "paystub":         "prebuilt-payStub.us",
    "bank_statement": "prebuilt-bankStatement.us",
    "w2":              "prebuilt-tax.us.w2",
    "drivers_license": "prebuilt-idDocument",
    "passport":        "prebuilt-idDocument.passport",
    # Generic fallback for unrecognised pages. CU also exposes prebuilt-layout.
    "unknown":         "prebuilt-layout",
}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    classifier_configured = bool(settings.classifier_id)
    classifier_label = settings.classifier_id or "not configured"
    classifier_dot = "ok" if classifier_configured else "muted"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>IDP Demo — Loan Application Review</title>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<style>
  :root {{
    --bg:#f6f7fb; --card:#fff; --ink:#1a1f2c; --muted:#6b7280; --line:#e6e8ef;
    --brand:#0b5fff; --brand-soft:#e8efff;
    --good:#0a8a4f; --good-soft:#e6f6ee;
    --warn:#a35a00; --warn-soft:#fff3df;
    --bad:#b3261e;  --bad-soft:#fde7e6;
    --chip:#eef0f5;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.5 -apple-system,Segoe UI,Roboto,Inter,sans-serif;}}
  header{{padding:18px 24px;background:var(--card);border-bottom:1px solid var(--line);
         display:flex;align-items:center;gap:14px;}}
  header h1{{margin:0;font-size:18px}}
  header .meta{{margin-left:auto;color:var(--muted);font-size:12px}}
  .dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}}
  .dot.ok{{background:var(--good)}} .dot.muted{{background:#bbb}}
  main{{max-width:1180px;margin:0 auto;padding:24px}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:18px 20px;margin-bottom:18px;box-shadow:0 1px 2px rgba(20,30,60,.04)}}
  .card h2{{margin:0 0 12px;font-size:15px;letter-spacing:.02em;text-transform:uppercase;color:var(--muted)}}
  .row{{display:flex;flex-wrap:wrap;gap:14px;align-items:center}}
  label.field{{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)}}
  label.field input,label.field select{{padding:8px 10px;border:1px solid var(--line);border-radius:8px;
    font-size:14px;background:#fff;color:var(--ink);min-width:200px}}
  .modes{{display:flex;gap:8px;flex-wrap:wrap}}
  .modes label{{padding:8px 12px;border:1px solid var(--line);border-radius:999px;cursor:pointer;
    font-size:13px;background:#fff;display:inline-flex;align-items:center;gap:6px}}
  .modes input{{accent-color:var(--brand)}}
  .modes label.sel{{background:var(--brand-soft);border-color:var(--brand);color:var(--brand);font-weight:600}}
  .file-wrap{{display:flex;gap:8px;align-items:center}}
  .btn{{background:var(--brand);color:#fff;border:0;padding:10px 18px;border-radius:8px;
       font-weight:600;cursor:pointer;font-size:14px}}
  .btn:disabled{{opacity:.5;cursor:not-allowed}}
  .btn.ghost{{background:#fff;color:var(--ink);border:1px solid var(--line)}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
  @media (max-width:900px){{.grid{{grid-template-columns:1fr}}}}
  .kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
  @media (max-width:700px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
  .kpi{{background:#fafbff;border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
  .kpi .lbl{{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}}
  .kpi .val{{font-size:22px;font-weight:700;margin-top:4px}}
  .kpi .delta{{font-size:12px;margin-top:2px}}
  .delta.good{{color:var(--good)}} .delta.bad{{color:var(--bad)}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th,td{{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}}
  th{{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600}}
  tbody tr:hover{{background:#fafbff}}
  .chip{{display:inline-block;padding:2px 8px;border-radius:999px;background:var(--chip);
        font-size:12px;margin-right:4px;margin-bottom:4px}}
  .chip.t-paystub{{background:#e8efff;color:#0b3aab}}
  .chip.t-bank_statement{{background:#e6f6ee;color:#0a6a3f}}
  .chip.t-w2{{background:#fff3df;color:#7a4400}}
  .chip.t-passport{{background:#f1e6fd;color:#5a259c}}
  .chip.t-drivers_license{{background:#fde7e6;color:#a02019}}
  .chip.t-unknown{{background:#eee;color:#555}}
  .pill{{display:inline-block;padding:1px 8px;border-radius:6px;font-size:11px;font-weight:600}}
  .pill.h{{background:var(--warn-soft);color:var(--warn)}}
  .pill.c{{background:var(--brand-soft);color:var(--brand)}}
  .conf{{display:inline-block;width:36px;text-align:right;font-variant-numeric:tabular-nums}}
  .conf.lo{{color:var(--bad)}} .conf.mid{{color:var(--warn)}} .conf.hi{{color:var(--good)}}
  .seg-list{{display:flex;flex-direction:column;gap:6px}}
  .seg-row{{display:flex;align-items:center;gap:8px;padding:6px 8px;border:1px solid var(--line);border-radius:8px;background:#fff}}
  .seg-row .pages{{color:var(--muted);font-size:12px;min-width:60px}}
  .empty{{color:var(--muted);font-size:13px;text-align:center;padding:30px}}
  details{{background:#fafbff;border:1px solid var(--line);border-radius:8px;padding:8px 12px}}
  details summary{{cursor:pointer;font-size:12px;color:var(--muted)}}
  pre{{margin:8px 0 0;font-size:12px;background:#0e1424;color:#dfe5ff;padding:12px;border-radius:8px;
       overflow:auto;max-height:380px}}
  .spinner{{display:inline-block;width:16px;height:16px;border:2px solid var(--line);border-top-color:var(--brand);
           border-radius:50%;animation:spin 1s linear infinite;vertical-align:middle;margin-right:8px}}
  @keyframes spin{{to{{transform:rotate(360deg)}}}}
  .err{{background:var(--bad-soft);color:var(--bad);padding:12px;border-radius:8px;border:1px solid #f3c4c1}}
  .strategy-h{{border-left:3px solid var(--warn)}}
  .strategy-c{{border-left:3px solid var(--brand)}}
</style>
</head>
<body>
<header>
  <h1>IDP Demo — Loan Application Review</h1>
  <span class="meta">
    <span class="dot {classifier_dot}"></span>Classifier: {classifier_label}
  </span>
  <span class="meta"><a href="/healthz">/healthz</a> · <a href="/docs">/docs</a></span>
</header>
<main>
  <section class="card">
    <h2>Process a loan PDF</h2>
    <form id="f" class="row">
      <label class="field">Tenant ID
        <input name="tenantId" value="demo-tenant"/>
      </label>
      <div class="modes" role="radiogroup">
        <label class="sel"><input type="radio" name="mode" value="heuristic" checked/> Heuristic (DI)</label>
        <label><input type="radio" name="mode" value="classifier"/> Custom classifier (DI)</label>
        <label><input type="radio" name="mode" value="cu"/> Content Understanding</label>
        <label><input type="radio" name="mode" value="compare"/> Compare DI both</label>
      </div>
      <label class="field" style="flex:1">PDF file
        <input type="file" name="file" accept="application/pdf" required/>
      </label>
      <button class="btn" id="go" type="submit">Process</button>
    </form>
  </section>

  <div id="status"></div>
  <div id="results"></div>
</main>

<script>
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));

// Highlight selected mode pill
$$('.modes input').forEach(r => r.addEventListener('change', () => {{
  $$('.modes label').forEach(l => l.classList.toggle('sel', l.querySelector('input').checked));
}}));

const fmtMs = ms => ms < 1000 ? `${{Math.round(ms)}} ms` : `${{(ms/1000).toFixed(1)}} s`;
const fmtUsd = v => `$${{(v ?? 0).toFixed(3)}}`;
const confClass = c => c == null ? '' : (c < 0.6 ? 'lo' : c < 0.85 ? 'mid' : 'hi');
const escape = s => String(s ?? '').replace(/[&<>"']/g, m => (
  {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]));

function chip(t) {{
  return `<span class="chip t-${{escape(t)}}">${{escape(t)}}</span>`;
}}

function segmentsTable(segments) {{
  if (!segments?.length) return '<div class="empty">No segments</div>';
  const rows = segments.map(s => `
    <tr>
      <td>${{chip(s.doc_type)}}</td>
      <td><code>${{escape(s.model_id)}}</code></td>
      <td>p${{s.page_range[0]}}${{s.page_range[1]!==s.page_range[0]?'–'+s.page_range[1]:''}}
        <span style="color:var(--muted)">(${{s.pages}}p)</span></td>
      <td>${{fmtMs(s.duration_ms)}}</td>
      <td>${{fmtUsd(s.cost_estimate_usd)}}</td>
      <td>${{s.documents?.length ?? 0}}</td>
    </tr>`).join('');
  return `<table>
    <thead><tr><th>Doc type</th><th>Model</th><th>Pages</th>
      <th>Duration</th><th>Cost</th><th>Fields</th></tr></thead>
    <tbody>${{rows}}</tbody></table>`;
}}

function fieldsBlock(segments) {{
  const docs = segments.flatMap(s => (s.documents ?? []).map(d => ({{...d, _seg: s.doc_type}})));
  if (!docs.length) return '';
  return docs.map(d => {{
    const fields = Object.entries(d.fields ?? {{}});
    if (!fields.length) return '';
    const rows = fields.map(([k, v]) => `
      <tr>
        <td><b>${{escape(k)}}</b></td>
        <td>${{escape(v.value ?? '—')}}</td>
        <td><span class="conf ${{confClass(v.confidence)}}">${{
          v.confidence != null ? v.confidence.toFixed(2) : '—'}}</span></td>
      </tr>`).join('');
    return `<div style="margin-top:14px">
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">
        Extracted from ${{chip(d._seg)}} <code>${{escape(d.doc_type)}}</code>
      </div>
      <table>
        <thead><tr><th>Field</th><th>Value</th><th>Conf</th></tr></thead>
        <tbody>${{rows}}</tbody></table></div>`;
  }}).join('');
}}

function singleResult(r) {{
  return `
    <section class="card">
      <h2>Run summary — ${{escape(r.splitStrategy)}}</h2>
      <div class="kpis">
        <div class="kpi"><div class="lbl">Total pages</div><div class="val">${{r.totalPages}}</div></div>
        <div class="kpi"><div class="lbl">Billed pages</div><div class="val">${{r.billing.billedPages}}</div></div>
        <div class="kpi"><div class="lbl">Duration</div><div class="val">${{fmtMs(r.totalDurationMs)}}</div></div>
        <div class="kpi"><div class="lbl">Cost (est)</div><div class="val">${{fmtUsd(r.billing.totalCostEstimateUsd)}}</div></div>
      </div>
    </section>
    <section class="card">
      <h2>Segments</h2>
      ${{segmentsTable(r.segments)}}
      ${{fieldsBlock(r.segments)}}
    </section>
    <section class="card">
      <h2>Models used</h2>
      <table>
        <thead><tr><th>Model</th><th>Calls</th><th>Pages</th></tr></thead>
        <tbody>${{r.modelsUsed.map(m => `
          <tr><td><code>${{escape(m.model_id)}}</code></td><td>${{m.calls}}</td><td>${{m.pages}}</td></tr>`).join('')}}
        </tbody>
      </table>
    </section>
    <details><summary>Raw JSON</summary><pre>${{escape(JSON.stringify(r, null, 2))}}</pre></details>`;
}}

function compareResult(d) {{
  const cmp = d.comparison, h = d.heuristic, c = d.classifier;
  const dCost = cmp.totalCostUsd.savingsUsd;
  const dPct = cmp.totalCostUsd.savingsPct;
  const dDur = h.totalDurationMs - c.totalDurationMs;
  const dDurPct = h.totalDurationMs ? (dDur / h.totalDurationMs * 100) : 0;
  const sign = v => v > 0 ? '−' : '+'; // savings shown as negative cost
  return `
    <section class="card">
      <h2>Comparison summary</h2>
      <div class="kpis">
        <div class="kpi">
          <div class="lbl">Cost — heuristic</div>
          <div class="val">${{fmtUsd(cmp.totalCostUsd.heuristic)}}</div>
        </div>
        <div class="kpi">
          <div class="lbl">Cost — classifier</div>
          <div class="val">${{fmtUsd(cmp.totalCostUsd.classifier)}}</div>
          <div class="delta ${{dCost>0?'good':'bad'}}">
            ${{sign(dCost)}}${{fmtUsd(Math.abs(dCost))}} (${{dPct.toFixed(1)}}%) vs heuristic
          </div>
        </div>
        <div class="kpi">
          <div class="lbl">Duration — heuristic</div>
          <div class="val">${{fmtMs(h.totalDurationMs)}}</div>
        </div>
        <div class="kpi">
          <div class="lbl">Duration — classifier</div>
          <div class="val">${{fmtMs(c.totalDurationMs)}}</div>
          <div class="delta ${{dDur>0?'good':'bad'}}">
            ${{sign(dDur)}}${{fmtMs(Math.abs(dDur))}} (${{dDurPct.toFixed(1)}}%) vs heuristic
          </div>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>Detected segments side-by-side</h2>
      <div class="grid">
        <div>
          <div style="margin-bottom:8px"><span class="pill h">HEURISTIC</span>
            <span style="color:var(--muted);font-size:12px;margin-left:6px">${{h.segments.length}} segments</span></div>
          <div class="seg-list">${{h.segments.map(s => `
            <div class="seg-row strategy-h">
              ${{chip(s.doc_type)}}
              <span class="pages">p${{s.page_range[0]}}${{s.page_range[1]!==s.page_range[0]?'–'+s.page_range[1]:''}}</span>
              <code style="font-size:11px;color:var(--muted)">${{escape(s.model_id)}}</code>
              <span style="margin-left:auto;color:var(--muted);font-size:12px">${{fmtUsd(s.cost_estimate_usd)}}</span>
            </div>`).join('')}}</div>
        </div>
        <div>
          <div style="margin-bottom:8px"><span class="pill c">CLASSIFIER</span>
            <span style="color:var(--muted);font-size:12px;margin-left:6px">${{c.segments.length}} segments</span></div>
          <div class="seg-list">${{c.segments.map(s => `
            <div class="seg-row strategy-c">
              ${{chip(s.doc_type)}}
              <span class="pages">p${{s.page_range[0]}}${{s.page_range[1]!==s.page_range[0]?'–'+s.page_range[1]:''}}</span>
              <code style="font-size:11px;color:var(--muted)">${{escape(s.model_id)}}</code>
              <span style="margin-left:auto;color:var(--muted);font-size:12px">${{fmtUsd(s.cost_estimate_usd)}}</span>
            </div>`).join('')}}</div>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>Extracted fields</h2>
      <div class="grid">
        <div><div style="margin-bottom:6px"><span class="pill h">HEURISTIC</span></div>${{fieldsBlock(h.segments) || '<div class="empty">No structured fields</div>'}}</div>
        <div><div style="margin-bottom:6px"><span class="pill c">CLASSIFIER</span></div>${{fieldsBlock(c.segments) || '<div class="empty">No structured fields</div>'}}</div>
      </div>
    </section>

    <details><summary>Raw JSON</summary><pre>${{escape(JSON.stringify(d, null, 2))}}</pre></details>
  `;
}}

$('#f').addEventListener('submit', async (e) => {{
  e.preventDefault();
  const form = e.target;
  const file = form.file.files[0];
  if (!file) return;
  const mode = form.mode.value;
  const tenant = form.tenantId.value || 'demo-tenant';
  const fd = new FormData();
  fd.append('file', file);
  $('#status').innerHTML = `<div class="card"><span class="spinner"></span>Processing <b>${{escape(file.name)}}</b> in <b>${{escape(mode)}}</b> mode… (DI calls can take 10–60s)</div>`;
  $('#results').innerHTML = '';
  $('#go').disabled = true;
  try {{
    const res = await fetch(`/process?mode=${{encodeURIComponent(mode)}}`, {{
      method:'POST', body: fd, headers: {{'x-tenant-id': tenant}}
    }});
    const data = await res.json();
    $('#status').innerHTML = '';
    if (!res.ok) {{
      $('#results').innerHTML = `<div class="card"><div class="err">${{escape(data.detail || res.statusText)}}</div></div>`;
      return;
    }}
    $('#results').innerHTML = data.comparison ? compareResult(data) : singleResult(data);
  }} catch (err) {{
    $('#status').innerHTML = '';
    $('#results').innerHTML = `<div class="card"><div class="err">${{escape(err.message)}}</div></div>`;
  }} finally {{
    $('#go').disabled = false;
  }}
}});
</script>
</body>
</html>"""


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "classifierConfigured": bool(settings.classifier_id)}


@app.post("/process")
async def process(
    request: Request,
    file: UploadFile = File(...),
    x_tenant_id: str | None = Header(default=None, alias="x-tenant-id"),
    mode: str = Query(default="heuristic"),
) -> JSONResponse:
    """Main entry point. Accepts a merged loan-application PDF and returns
    structured per-document extraction.

    Inputs:
      - file:        multipart upload (PDF)
      - x-tenant-id: HTTP header identifying the SaaS tenant (for cost allocation)
      - mode:        "heuristic" | "classifier" | "compare" (query string or form field)
    """
    # Reject non-PDF uploads early. octet-stream is allowed because some
    # browsers / clients don't sniff the type.
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=415, detail="PDF required")

    # Read multipart form once so we can pull both `tenantId` and `mode` fields
    # from it (the HTML UI submits both as form fields).
    form = await request.form() if request.headers.get("content-type", "").startswith("multipart/") else {}
    # Resolve tenant in priority order: header > form field > "anonymous".
    # The HTML form posts the tenant under the field name "tenantId" (see UI markup).
    tenant_id = (
        x_tenant_id
        or (form.get("tenantId") if form else None)  # type: ignore[union-attr]
        or "anonymous"
    )
    # The HTML form posts mode as a form field; query param is the API path.
    chosen_mode = (form.get("mode") if form else None) or mode  # type: ignore[union-attr]
    chosen_mode = str(chosen_mode).lower()

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    # Lazily build the DI client (returns 503 with a friendly message if misconfigured).
    di = _get_di()

    # Compare mode runs BOTH strategies sequentially and returns side-by-side
    # billing/segment data so the UI can show the savings story.
    if chosen_mode == "compare":
        h = _run_pipeline("heuristic",  di, pdf_bytes, tenant_id, file.filename)
        c = _run_pipeline("classifier", di, pdf_bytes, tenant_id, file.filename)
        return JSONResponse({
            "tenantId": tenant_id,
            "filename": file.filename,
            "comparison": _summarize_compare(h, c),
            "heuristic": h,
            "classifier": c,
        })

    if chosen_mode not in ("heuristic", "classifier", "cu"):
        raise HTTPException(
            status_code=400,
            detail="mode must be heuristic, classifier, cu, or compare",
        )

    # Single-strategy path.
    out = _run_pipeline(chosen_mode, di, pdf_bytes, tenant_id, file.filename)  # type: ignore[arg-type]
    return JSONResponse(out)


# ---------- core pipeline ----------

def _get_di() -> DIClient:
    try:
        return DIClient()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Document Intelligence not configured: {exc}. "
                "Set DI_ENDPOINT (and DI_KEY or use managed identity) and restart."
            ),
        ) from exc


def _run_pipeline(
    strategy: SplitStrategy,
    di: DIClient,
    pdf_bytes: bytes,
    tenant_id: str,
    filename: str | None,
) -> dict:
    """Two-stage IDP pipeline: SPLIT (boundaries + types) then EXTRACT (per segment).

    Stage 1 — Split pass:
        heuristic   -> prebuilt-layout over whole doc + keyword classifier
        classifier  -> custom classifier (single managed call, cheaper meter)
        cu          -> heuristic split (DI prebuilt-layout) then CU prebuilts for extraction

    Stage 2 — Per-segment extraction:
        slice the PDF down to each segment, then call the model picked by
        MODEL_BY_TYPE (prebuilt-tax.us.w2 for W2s, prebuilt-idDocument for
        IDs, prebuilt-layout otherwise).

    Every DI call emits one `di.pages.processed` trace; CU calls emit
    `cu.calls.processed`. All rows in a single request share one correlationId
    so they're easy to stitch together in App Insights.
    """
    # cu strategy has its own orchestration (mixes DI for split, CU for extract);
    # delegate to a dedicated function to keep the DI-only pipeline readable.
    if strategy == "cu":
        return _run_cu_pipeline(di, pdf_bytes, tenant_id, filename)

    # correlationId threads all telemetry rows for this request together.
    correlation_id = str(uuid.uuid4())
    log.info("pipeline start strategy=%s tenant=%s corr=%s", strategy, tenant_id, correlation_id)
    overall_start = time.perf_counter()

    # ---- Split pass ----
    if strategy == "heuristic":
        # One DI call returns layout (text + structure) for every page.
        split_model = "prebuilt-layout"
        layout_result, split_ms = di.analyze(model_id=split_model, content=pdf_bytes)
        page_texts = di.page_text(layout_result)
        total_pages = len(page_texts)
        # Keyword-score each page, then collapse runs of same-typed pages into segments.
        page_types = [classify_page(t) for t in page_texts]
        segments = segments_from_page_types(page_types)
    else:  # classifier
        # Custom classifier required — fail fast with an actionable hint.
        if not settings.classifier_id:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Classifier mode requires CLASSIFIER_ID. Train a custom classifier in DI Studio "
                    "(see app/sample/generate_training_set.py), then `azd env set CLASSIFIER_ID <id> && azd deploy`."
                ),
            )
        split_model = settings.classifier_id  # logical model name for telemetry
        # Classifier result doesn't expose total page count directly;
        # read the PDF locally with pypdf (fast, no DI billing impact).
        total_pages = len(PdfReader(io.BytesIO(pdf_bytes)).pages)
        cls_result, split_ms = di.classify(classifier_id=settings.classifier_id, content=pdf_bytes)
        segments = segments_from_classifier_result(cls_result)

    # Pricing key differs from the model name: a custom classifier id like
    # "idp-loan-docs-v1" maps to the "classifier" SKU ($3/1k), not its raw id.
    split_pricing_key = "classifier" if strategy == "classifier" else "prebuilt-layout"
    split_cost = estimate_cost_usd(split_pricing_key, total_pages)
    # Telemetry row #1 for this request: the split pass.
    emit_pages_processed(
        tenant_id=tenant_id,
        model=split_model,
        pages=total_pages,
        duration_ms=split_ms,
        extra={"correlationId": correlation_id, "stage": "split", "splitStrategy": strategy},
    )

    log.info("segments tenant=%s strategy=%s segments=%s",
             tenant_id, strategy,
             [(s.doc_type, s.page_start, s.page_end) for s in segments])

    # ---- Per-segment extraction ----
    # For each Segment, slice out just its pages, send to the model chosen by
    # MODEL_BY_TYPE, and accumulate cost. One telemetry row per segment.
    # NOTE: billed_pages = split-pass pages + sum(segment pages); the split
    # pass and per-segment analyze are billed on separate meters by Azure DI.
    per_doc_results: list[dict] = []
    total_cost = split_cost
    billed_pages = total_pages
    reader = PdfReader(io.BytesIO(pdf_bytes))
    for seg in segments:
        # Build a small PDF containing only this segment's pages.
        seg_bytes = _extract_pages(reader, seg.page_start, seg.page_end)
        result, dur_ms = di.analyze(model_id=seg.model_id, content=seg_bytes)
        seg_pages = seg.page_end - seg.page_start + 1
        seg_cost = estimate_cost_usd(seg.model_id, seg_pages)
        total_cost += seg_cost
        billed_pages += seg_pages
        emit_pages_processed(
            tenant_id=tenant_id,
            model=seg.model_id,
            pages=seg_pages,
            duration_ms=dur_ms,
            extra={
                "correlationId": correlation_id,
                "docType": seg.doc_type,
                "pageStart": seg.page_start,
                "pageEnd": seg.page_end,
                "splitStrategy": strategy,
            },
        )
        per_doc_results.append({
            "doc_type": seg.doc_type,
            "model_id": seg.model_id,
            "page_range": [seg.page_start, seg.page_end],
            "pages": seg_pages,
            "duration_ms": round(dur_ms, 2),
            "cost_estimate_usd": seg_cost,
            "documents": di.summarize_fields(result),
        })

    overall_ms = (time.perf_counter() - overall_start) * 1000.0

    models_used: dict[str, dict[str, int]] = {}
    def _bump(model: str, pages: int) -> None:
        slot = models_used.setdefault(model, {"calls": 0, "pages": 0})
        slot["calls"] += 1
        slot["pages"] += pages
    _bump(split_model, total_pages)
    for seg in segments:
        _bump(seg.model_id, seg.page_end - seg.page_start + 1)
    models_used_list = [
        {"model_id": m, "calls": v["calls"], "pages": v["pages"]}
        for m, v in sorted(models_used.items())
    ]

    return {
        "correlationId": correlation_id,
        "tenantId": tenant_id,
        "filename": filename,
        "splitStrategy": strategy,
        "totalPages": total_pages,
        "totalDurationMs": round(overall_ms, 2),
        "modelsUsed": models_used_list,
        "billing": {
            "billedPages": billed_pages,
            "splitPassPages": total_pages,
            "splitPassModel": split_model,
            "splitPassPricingKey": split_pricing_key,
            "splitPassCostEstimateUsd": round(split_cost, 6),
            "totalCostEstimateUsd": round(total_cost, 6),
            "note": "Approximate. Split pass + per-segment analyze are billed separately.",
        },
        "segments": per_doc_results,
    }


def _summarize_compare(h: dict, c: dict) -> dict:
    h_cost = h["billing"]["totalCostEstimateUsd"]
    c_cost = c["billing"]["totalCostEstimateUsd"]
    return {
        "billedPages":       {"heuristic": h["billing"]["billedPages"], "classifier": c["billing"]["billedPages"]},
        "totalCostUsd":      {"heuristic": h_cost, "classifier": c_cost,
                              "savingsUsd": round(h_cost - c_cost, 6),
                              "savingsPct": round((h_cost - c_cost) / h_cost * 100, 1) if h_cost else 0.0},
        "totalDurationMs":   {"heuristic": h["totalDurationMs"], "classifier": c["totalDurationMs"]},
        "segmentCount":      {"heuristic": len(h["segments"]),  "classifier": len(c["segments"])},
        "segmentDocTypes":   {"heuristic": [s["doc_type"] for s in h["segments"]],
                              "classifier": [s["doc_type"] for s in c["segments"]]},
    }


def _extract_pages(reader: PdfReader, start_1based: int, end_1based: int) -> bytes:
    """Return a new PDF (as bytes) containing only pages [start..end] (1-based).

    Used to send each segment to DI individually so we only pay for the right
    model per page range (e.g., prebuilt-tax.us.w2 only for the W2 page).
    """
    writer = PdfWriter()
    for i in range(start_1based - 1, end_1based):
        writer.add_page(reader.pages[i])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------- Content Understanding pipeline ----------

def _get_cu() -> CUClient:
    """Lazy-build the CU client. Returns 503 with a friendly hint on misconfig."""
    try:
        return CUClient()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Content Understanding not configured: {exc}. "
                "Set CU_ENDPOINT (or reuse DI_ENDPOINT on the same Foundry resource) and restart."
            ),
        ) from exc


def _run_cu_pipeline(
    di: DIClient,
    pdf_bytes: bytes,
    tenant_id: str,
    filename: str | None,
) -> dict:
    """CU strategy: heuristic split (DI prebuilt-layout) + per-segment CU prebuilt extraction.

    The split pass still uses DI's prebuilt-layout (cheapest reliable way to get
    per-page text for the keyword classifier, and emits a `di.pages.processed`
    row for cost transparency). Per-segment extraction then routes each segment
    to a Content Understanding prebuilt analyzer (e.g., prebuilt-payStub.us)
    via CUClient and emits one `cu.calls.processed` row per segment.

    Result shape mirrors the DI pipelines so the existing UI renders it
    unchanged. `splitStrategy` is reported as "cu" so KQL can group by it.
    """
    cu = _get_cu()
    correlation_id = str(uuid.uuid4())
    log.info("cu pipeline start tenant=%s corr=%s", tenant_id, correlation_id)
    overall_start = time.perf_counter()

    # ---- Split pass (DI prebuilt-layout, same as heuristic mode) ----
    split_model = "prebuilt-layout"
    layout_result, split_ms = di.analyze(model_id=split_model, content=pdf_bytes)
    page_texts = di.page_text(layout_result)
    total_pages = len(page_texts)
    page_types = [classify_page(t) for t in page_texts]
    segments = segments_from_page_types(page_types)

    split_pricing_key = "prebuilt-layout"
    split_cost = estimate_cost_usd(split_pricing_key, total_pages)
    # Telemetry row #1: the DI split pass (di.* event so it shows up alongside
    # heuristic-mode rows in the cost-allocation KQL).
    emit_pages_processed(
        tenant_id=tenant_id,
        model=split_model,
        pages=total_pages,
        duration_ms=split_ms,
        extra={"correlationId": correlation_id, "stage": "split", "splitStrategy": "cu"},
    )

    log.info("cu segments tenant=%s segments=%s",
             tenant_id, [(s.doc_type, s.page_start, s.page_end) for s in segments])

    # ---- Per-segment extraction via CU prebuilts ----
    per_doc_results: list[dict] = []
    total_cost = split_cost
    billed_pages = total_pages
    reader = PdfReader(io.BytesIO(pdf_bytes))
    for seg in segments:
        # Map detected doc_type -> CU analyzer id. Falls back to prebuilt-layout
        # for unknown segments so we still return *something* per page range.
        analyzer_id = CU_ANALYZER_BY_TYPE.get(seg.doc_type, CU_ANALYZER_BY_TYPE["unknown"])
        seg_bytes = _extract_pages(reader, seg.page_start, seg.page_end)
        result, dur_ms = cu.analyze(analyzer_id=analyzer_id, content=seg_bytes)
        seg_pages = seg.page_end - seg.page_start + 1
        # All current routes are CU prebuilts -> "cu.prebuilt" pricing key.
        seg_cost = estimate_cost_usd("cu.prebuilt", seg_pages)
        total_cost += seg_cost
        billed_pages += seg_pages
        emit_cu_call_processed(
            tenant_id=tenant_id,
            analyzer_id=analyzer_id,
            pricing_key="cu.prebuilt",
            pages=seg_pages,
            duration_ms=dur_ms,
            extra={
                "correlationId": correlation_id,
                "docType": seg.doc_type,
                "pageStart": seg.page_start,
                "pageEnd": seg.page_end,
                "splitStrategy": "cu",
            },
        )
        per_doc_results.append({
            "doc_type": seg.doc_type,
            "model_id": analyzer_id,        # mirror DI shape; UI renders it as <code>
            "page_range": [seg.page_start, seg.page_end],
            "pages": seg_pages,
            "duration_ms": round(dur_ms, 2),
            "cost_estimate_usd": seg_cost,
            "documents": cu.summarize_fields(result),
        })

    overall_ms = (time.perf_counter() - overall_start) * 1000.0

    # Aggregate per-model usage for the "Models used" panel in the UI.
    models_used: dict[str, dict[str, int]] = {}
    def _bump(model: str, pages: int) -> None:
        slot = models_used.setdefault(model, {"calls": 0, "pages": 0})
        slot["calls"] += 1
        slot["pages"] += pages
    _bump(split_model, total_pages)
    for seg in segments:
        analyzer_id = CU_ANALYZER_BY_TYPE.get(seg.doc_type, CU_ANALYZER_BY_TYPE["unknown"])
        _bump(analyzer_id, seg.page_end - seg.page_start + 1)
    models_used_list = [
        {"model_id": m, "calls": v["calls"], "pages": v["pages"]}
        for m, v in sorted(models_used.items())
    ]

    return {
        "correlationId": correlation_id,
        "tenantId": tenant_id,
        "filename": filename,
        "splitStrategy": "cu",
        "totalPages": total_pages,
        "totalDurationMs": round(overall_ms, 2),
        "modelsUsed": models_used_list,
        "billing": {
            "billedPages": billed_pages,
            "splitPassPages": total_pages,
            "splitPassModel": split_model,
            "splitPassPricingKey": split_pricing_key,
            "splitPassCostEstimateUsd": round(split_cost, 6),
            "totalCostEstimateUsd": round(total_cost, 6),
            "note": "Approximate. DI split pass + per-segment CU analyze are billed on separate meters.",
        },
        "segments": per_doc_results,
    }
