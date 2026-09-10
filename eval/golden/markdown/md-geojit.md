# Equity Research Report Generator (Geojit-style)

A minimal web app that turns a company's **financial context document** (PDF / CSV / TXT)
into a downloadable, auto-filled **PDF research report** that mirrors the layout of a
Geojit-style equity-research note — header + rating box, stock-data panel, key-financials
table, revenue/margin charts, narrative sections, and full P&L / Balance Sheet / Cash Flow /
Ratios tables.

> The template here is a faithful **reconstruction** of the standard Geojit research-report
> layout (the original sample lived in a drive this build couldn't reach). It is driven
> entirely by `app/config.py` + `app/report/pdf_builder.py`, so matching an exact sample is
> a matter of tweaking a few values — see [Fine-tuning to an exact sample](#fine-tuning-to-an-exact-sample).

---

## Quick start (under 2 minutes)

```bash
cd geojit-report-app
python -m pip install -r requirements.txt
python run.py                     # serves http://127.0.0.1:8000
```

Open **http://127.0.0.1:8000**, enter a company name, upload a context document
(`.csv`, `.txt`, or `.pdf`), and click **Generate report** → **Download PDF**.

### Generate the bundled examples from the command line

```bash
python generate_examples.py       # writes examples/*.pdf from samples/input/*
```

---

## AI extraction (optional) vs deterministic (default)

The extractor has two paths and **always runs**:

| Mode | When | What it does |
|------|------|--------------|
| **Claude (LLM)** | `ANTHROPIC_API_KEY` is set | Sends the document to Claude and asks for structured JSON matching the report schema. |
| **Deterministic** | no key, or the LLM call fails | Pure rule-based parsing of tables/text — no network needed. |

```bash
# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
python run.py
```

The UI badge and `GET /health` tell you which mode is active. The LLM path degrades
**gracefully** to the deterministic path on any error, so generation never breaks.

---

## How it works

```
upload ─► readers.py ─► extractor ────────────────► ReportData ─► pdf_builder.py ─► PDF
          (pdf/csv/txt   (llm_extractor OR           (the template   (ReportLab +
           → text+tables) heuristic_extractor)        fields)         matplotlib)
```

1. **Read** the file into normalised text + tables (`app/extract/readers.py`).
2. **Extract** financials, metrics, narrative and charts into a `ReportData` object,
   via Claude or the deterministic rules.
3. **Render** the `ReportData` into a Geojit-style PDF (`app/report/pdf_builder.py`),
   with charts drawn by matplotlib (`app/report/charts.py`).

---

## Where the template fields are defined

| Concern | File | Notes |
|---------|------|-------|
| **The template fields** (the data contract) | **`app/models.py`** | `ReportData` and its parts: `Rating`, `StockDataRow`, `FinancialTable`/`FinancialLine`, `NarrativeSection`, `ChartSpec`. |
| **The layout** (how fields render) | **`app/report/pdf_builder.py`** | Header band, rating strip, two-column block, tables, charts row, narrative, legend, footer. |
| **Theme** (colours, fonts, page size, rating ladder, disclaimer) | **`app/config.py`** | Change hex values here to re-skin the whole report. |
| **Charts** | `app/report/charts.py` | `revenue_trend` (bar), `margin_trend` (line), `shareholding` (pie), generic `bar`. |
| **Deterministic extraction rules** (metric synonyms, period detection) | `app/extract/heuristic_extractor.py` | `METRIC_SYNONYMS` maps source labels → canonical line-items. |
| **LLM prompt / schema** | `app/extract/llm_extractor.py` | `SCHEMA_HINT` is the JSON contract sent to Claude. |

### Adding a new field or company

- **New metric/line-item:** add its synonyms to `METRIC_SYNONYMS` in
  `heuristic_extractor.py` and include it in the relevant `_make_table(...)` list. It flows
  into the PDF automatically.
- **New chart:** append a `ChartSpec(kind=...)` and handle the `kind` in `charts.py`.
- **New company:** nothing to code — just upload its document. Company name comes from the UI.

---

## Acceptance criteria → where it's met

- **Template matches sample (layout + section order)** — `pdf_builder.py` reproduces the
  header/rating/stock-panel/key-financials/charts/narrative/statement-tables/legend order.
- **Required fields populated** — financial tables, metrics, narrative sections, and **two**
  charts (revenue trend + margin trend). See `examples/`.
- **≥ 2 input formats** — CSV, TXT and PDF are all supported (`readers.py`); the two bundled
  examples use **CSV** and **TXT**.
- **Missing fields handled gracefully** — anything not found renders as **"NA"** and is listed
  in an on-report note + the `missing_fields` API field (see the CSV example, which has no
  rating/sector).
- **One-click PDF download** — `POST /generate` → `GET /download/{file}` from the UI.

---

## Examples (generated from `samples/input/`)

| Input | Format | Output | Demonstrates |
|-------|--------|--------|--------------|
| `bharat_consumer_products.csv` | CSV | [`examples/bharat_consumer_products.pdf`](examples/bharat_consumer_products.pdf) | Structured tables; **graceful NA** for missing rating/sector. |
| `meridian_logistics.txt` | TXT | [`examples/meridian_logistics.pdf`](examples/meridian_logistics.pdf) | Free-text extraction; rating/CMP/target parsed; narrative overview. |

---

## Tech used

- **Python 3.11**, **FastAPI** + **Uvicorn** (web app), **Jinja2** (single-page UI)
- **ReportLab** (PDF), **matplotlib** (charts) — both pure-Python, no system libraries
- **pdfplumber** (PDF parsing), stdlib `csv` (CSV/TSV)
- **anthropic** (optional Claude extraction)

## API

| Method | Route | Purpose |
|--------|-------|---------|
| `GET` | `/` | Upload UI |
| `POST` | `/generate` | multipart `company_name` + `file` → JSON summary + `download_url` |
| `GET` | `/download/{filename}` | Download the generated PDF |
| `GET` | `/health` | Liveness + active extraction mode |

## Project structure

```
geojit-report-app/
├── run.py                     # entry point (uvicorn launcher)
├── generate_examples.py       # CLI: build example PDFs from samples/input
├── requirements.txt
├── app/
│   ├── main.py                # FastAPI routes
│   ├── config.py              # THEME: colours, fonts, page, rating ladder
│   ├── models.py              # TEMPLATE FIELDS: ReportData schema
│   ├── extract/
│   │   ├── __init__.py        # orchestrator (LLM → deterministic fallback)
│   │   ├── readers.py         # PDF / CSV / TXT → text + tables
│   │   ├── heuristic_extractor.py   # deterministic rules
│   │   └── llm_extractor.py   # Claude extraction
│   ├── report/
│   │   ├── charts.py          # matplotlib charts
│   │   └── pdf_builder.py     # LAYOUT: Geojit-style ReportLab document
│   └── templates/index.html   # UI
├── samples/input/             # sample context docs (CSV + TXT)
└── examples/                  # generated example PDFs
```

## Fine-tuning to an exact sample

To match a specific Geojit PDF pixel-for-pixel: drop it in `samples/`, then adjust
`PRIMARY`/`NAVY`/`ACCENT` and geometry in `app/config.py`, and section order/spacing in
`app/report/pdf_builder.py`. The data model and extraction don't need to change.

---

*Reconstructed research template for a take-home exercise. Not affiliated with, and not
produced by, Geojit Financial Services Ltd. Generated reports are not investment advice.*
