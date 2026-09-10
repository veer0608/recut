# Statement & Invoice PDF → Reconciled Rows

[![Get it on Gumroad](https://img.shields.io/badge/Gumroad-Get%20the%20template-ff90e8?style=for-the-badge&logo=gumroad&logoColor=white)](https://veer0608.gumroad.com/l/statement-extractor)

An n8n workflow that turns a bank statement or invoice PDF into structured rows,
and checks the arithmetic on every one — each row comes back reconciled against
the statement's own balance column, or flagged.

![Rows reconcile one by one, then the model's mislabelled row gets caught and flagged](demo.gif)

I built it as something to sell. When I looked at what was already on n8n's
library, document/PDF extraction turned out to be the thinnest-covered corner
there is: 17 paid listings against 177 free ones, and eleven of those seventeen
are resume screeners. Almost nobody is selling accurate structured extraction.
(The numbers I went off are in `../n8n_paid_ai_agents.xlsx` if you're curious.)

## The design rule

**The model never touches a number it could invent.**

The extraction pulls the values out deterministically; the model only says which
column each one goes in; then arithmetic checks the result. Everyone else pipes
the PDF straight into a vision model and hopes — which is exactly why those drift.

```
[1] Form Trigger (PDF upload)
[2] Extract from File          text layer + per-page char count
[3] Code: triage               <80 chars/page ⇒ scanned, else digital
[4] Switch ──── scanned ──► HTTP: OCR ──┐
     └───────── digital ────────────────┤
[5] Code: layout normalisation  ◄── THE MOAT
      strip repeated headers/footers by cross-page frequency
      drop page numbers and address preamble
      stitch wrapped descriptions and page-split rows
      parse (1,234.56) and CR/DR negatives
      infer the statement's date order once, emit ISO
      capture printed opening and closing balances
[6] LLM Chain + Structured Output Parser   (temp 0, batched per page)
      schema-enforced; receives resolved values, assigns roles only
[7] Code: validation & reconciliation  ◄── THE PROOF
      running balance: balance[n-1] ± movement == balance[n]
      opening + Σmovements == closing
      independent check against the PRINTED closing figure
      per-field confidence, not per-row
[8] IF reconciled ─┬─ pass ─► CSV / Sheets
                   └─ fail ─► review queue (page + row number)
[9] Respond: download link + {rows, flagged, confidence}
```

Nodes 5 and 7 are the product. Everything else is stock n8n.

## Files

| File | Purpose |
|---|---|
| `workflow.json` | The shipping copy. OpenAI-backed — what most buyers already have wired. |
| `workflow.test.json` | Headless variant: file read instead of form upload, deterministic stub instead of the LLM. Needs **no credentials**. |
| `workflow.gemini.json` | Headless variant with the real LLM chain on Gemini. Needs a Google Gemini credential (see below). |
| `make_test_variant.py` / `make_gemini_variant.py` | Regenerate the variants from `workflow.json`. Edit the source, never the variants. |
| `make_demo_pdf.py` | Generates `demo_statement.pdf`. Ledger arithmetic exact by construction. |
| `test_demo_parses.py` | Python port of nodes 5 and 7, run against the demo PDF. |

## The demo PDF

Fully fictional — safe to ship publicly. Never put a real statement in a
product. Three deliberate killers are baked in, because they are what break the
cheap templates:

1. A description that **wraps** onto a second line.
2. A transaction **split across a page break** — amounts on page 1, description
   continuing on page 2.
3. A negative in **accounting parentheses**, `(1,250.00)`.

Plus repeated page headers, footers, page numbers, and an address preamble.

**A note on generating the demo — column collision.** The first version of the
generator placed the credit and balance columns too close, and at 8pt they
overlapped in the PDF's text layer, which extracted as `42,5008.2060,820.00` —
two numbers fused into garbage. Only 5 of 16 rows reconciled, and it looked like
a parser bug. It wasn't: overlapping columns are a fault in the *source
document*, and no amount of parsing recovers them cleanly. The fix was in the
generator's geometry (`COL_DEBIT` / `COL_CREDIT` spacing in `make_demo_pdf.py`),
not the workflow. Worth remembering when a real statement extracts as fused
numbers — suspect the PDF's layout before the parser.

## Verified state

**Bottom line:** every stage of the pipeline — digital parse, live OCR, the live
LLM, and reconciliation — has run in real n8n and behaved correctly, including
catching a real model error. The one thing not yet done is reconciling a *real*
statement that has a balance column; the number below is the synthetic demo.

Everything below ran in **real n8n (v2.35.7)**, not a simulation.

| Path | Result |
|---|---|
| Digital PDF → parse → reconcile | 16/16 rows, 100% reconciled |
| Scanned (image-only) PDF → live OCR → reconcile | 16/16 rows, 100%, closing 518,312 — identical to digital |
| Real LLM (Gemini) structures rows → reconcile | model mislabelled 1 row; node 7 **caught it** (see below) |
| No-balance statement (card/invoice) | reports `not_applicable`, flags nothing (flaw #1) |
| Locale: EU / Indian / round-number amounts | parsed correctly (flaw #4) |
| All eight review flaws | fixed and verified (see below) |

Full summary of the digital run:

```
rows = 16   flagged = 0   reconciledPct = 100
statementReconciles = True   printedClosingMatches = True
dateOrder = DMY   dateOrderConfident = True
printedOpening = 784,320   printedClosing = 518,312
```

Reconciliation reports three states per row — `reconciled` (checked and it adds
up), `flagged` (checked and it doesn't), `unverified` (nothing to check it
against). A row is never counted as reconciled unless it was actually verified.

The scanned test rasterised the demo to an **image-only PDF** (zero text layer),
OCR'd it **live** through a vision model, and ran the output through the real
nodes. It used Google Gemini vision as the OCR provider; the shipped OCR node
points at Mistral, so the exact HTTP transport differs, but the
scan → OCR → parse → reconcile path is proven end to end.

**The LLM node, tested against a real model, is the best evidence in this repo.**
Driving the node's exact prompt and schema against live Gemini, the model
structured 15 of 16 rows correctly and got one wrong — it labelled a salary
disbursement a *credit* when the balance clearly falls. Node 7 caught it:

```
reconciled = 15   flagged = 1   reconciledPct = 93.8
row 7 -> balance_mismatch: expected 967,284.50 got 541,684.50
statementReconciles = False   printedClosingMatches = True
```

Note the last two: the model copied every *balance* correctly, so the endpoints
matched (`printedClosingMatches: True`) — but the per-row walk still caught the
movement error in the middle. That is the whole reason the check is row-by-row
and not just opening-vs-closing. The product's core promise — the model slips,
the reconciliation catches it instead of shipping it — is demonstrated, not
asserted.

**One thing remains unproven:** reconciliation against a *real* statement with a
balance column. The 16/16 is the synthetic demo; extraction is proven on a real
86-row card statement, but no real balance-column statement has been reconciled
yet.

## LLM provider

`workflow.json` ships on OpenAI because that is what most people already have
wired up. `workflow.gemini.json` is the same pipeline on Google Gemini, which is
a better choice for long runs on a free tier — Groq's free tier enforces a
tokens-per-day ceiling that appears in no response header, so a batch can die
partway with nothing to show.

Either way, create the credential in the n8n UI first:
**Credentials → Add credential → *your provider* → paste key → Save.** There is
no CLI route around this, because the credential holds a secret.

A note on running a local model instead: it removes the credential step, but
verify the runtime actually works before relying on it. Ollama on the machine
this was built on returned corrupted output for every model and never set the
`done` flag — two unrelated models failed identically, so it was the runtime,
not the weights.

One Node-on-Windows trap if any local HTTP service is wired in: Node 24's
`fetch` resolves `localhost` to IPv6 `::1` first, so a service bound to IPv4
only needs an explicit `127.0.0.1` in its base URL. curl hides this by falling
back to IPv4.

## Flaws found and fixed

A review pass turned up eight flaws; all are addressed. The full-detail ones are
below — the rest, in brief:

| # | Flaw | Fix |
|---|---|---|
| 1 | No-balance statements (cards, invoices) flagged every row → "0% reconciled" | node 7 detects the absent balance column and reports `not_applicable`, not failure |
| 2 | Numbers inside a description captured as phantom transaction amounts | node 5 emits `columnAmounts` (rightmost figures); the LLM picks money from those |
| 3 | Listing overclaimed "every row reconciled" | reworded to the honest three-state model |
| 4 | European `1.234,56` silently parsed to `1.23`; round amounts dropped | node 5 infers the decimal separator from the document (see below) |
| 5 | First row passed as reconciled without being checked when opening was unknown | explicit `unverified` state; never a false pass |
| 6 | Description strip removed only the first occurrence of a repeated amount | `split/join` instead of `replace` |
| 7 | `date_out_of_order` hard-flagged value-date-sorted statements | demoted to an advisory note; statement-level chronological detection |
| 8 | OCR branch silent on failure and wired to a nonexistent variable | robust text extraction + loud error; real base64 contract |

## Silent bugs worth not reintroducing

The two below and #4 above share a trait: they produced *wrong output with no
error*. Those are the expensive ones.

**Dates.** `new Date("14/06/2026")` is Invalid Date, because JS assumes
MM/DD/YYYY. Worse, `new Date("02/06/2026")` succeeds and returns 6 February
instead of 2 June — days ≤ 12 corrupt *silently*, days > 12 throw. Node 5 now
infers the statement's date order once from the whole document (looking for any
component > 12) and emits ISO. When every day is ≤ 12 the document is genuinely
undecidable, so it assumes DMY and exposes `dateOrderConfident: false` rather
than pretending. The LLM is handed a resolved date and told to copy it verbatim.

**Decimal separator.** The amount regex assumed dot-decimal. European
`1.234,56` matched as `1.23` — silent, wrong, and kept going. Node 5 now infers
the decimal separator once from the whole document (a token containing both `.`
and `,` reveals it — whichever is last is the decimal) and parses every amount
against that convention.

**The terminator regex.** `closing\s+bal\b` never matches `CLOSING BALANCE` —
a word boundary cannot sit between `bal` and `ance`. It failed silently and
identically to having no rule at all. It is `bal\w*` for that reason.

## Running the headless test

```bash
cd "C:/Users/veera/claude/n8n-statement-extractor" && python test_demo_parses.py
```

For the n8n run, `N8N_RESTRICT_FILE_ACCESS_TO` must point at this directory —
n8n blocks filesystem reads by default.

Note the Python port is a *weaker* check than the n8n run: it does not validate
dates, which is why it passed a workflow that was corrupting them.
