# GrantAudit (prototype)

> Take-home case study prototype. **Not production software and not compliance advice.** All sample data is **synthetic**.

## Project overview

GrantAudit is a lightweight, local document-intelligence and reconciliation engine. It ingests an unstructured grant award agreement alongside an operational expense CSV, verifies that line items match budget allocations, and flags classification drift and indirect-cost calculation errors for human review. All calculations are deterministic and run offline. An LLM is optional and only extracts terms and suggests categories. Every reviewer correction is logged and triggers a live recalculation.

## Problem

Research grant recipients receive an award agreement (unstructured text or PDF) and keep a spending ledger (CSV). Recipients end up with disallowed costs at audit because of avoidable mistakes: costs over budget lines, indirect costs (F&A) calculated on the wrong base, miscategorised or unallowable transactions, costs outside the award period, and drawdowns that don't match the books. GrantAudit pulls the terms out of the award, checks every transaction against deterministic rules, and lets a human reviewer correct categories. Totals and violations recalculate live, and every change is recorded in an audit trail.

## Setup and run

Requires **Python 3.10+**. Check with `python3 --version`. If it's older, create the venv with a specific interpreter, e.g. `python3.12`.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements.txt     # pydantic, streamlit, pytest, (optional) pypdf
python3 -m pytest -q --durations=5              # offline, no network
streamlit run app.py                            # UI; works with no API key
python3 -m grantaudit samples/2_misclassified_review/award_agreement.txt samples/2_misclassified_review/ledger_misclassified.csv   # CLI
```

Optional AI, with either provider called over `urllib` (no SDK):

| Provider | Key | Default model |
|---|---|---|
| Claude (Anthropic) | `export ANTHROPIC_API_KEY=...` | `claude-haiku-5-5` |
| Gemini (Google) | `export GEMINI_API_KEY=...` (or `GOOGLE_API_KEY`) | `gemini-3.5-flash-lite` |

Instead of exporting keys, you can `cp .env.example .env` and put the key there. The app and CLI load `.env` from the project folder. Variables already exported in your shell take precedence, and `.env` is git-ignored. `GRANTAUDIT_MODEL` overrides the model. If both keys are set, Claude is used unless `GRANTAUDIT_PROVIDER=gemini`. AI mode is off by default; turn it on in the app. Without a key, extraction uses the regex parser and suggestions use the keyword classifier.

## Pipeline

```
 award .txt/.pdf ─┐
                  ├─► ingest ─► extract terms ──────────► classify (SUGGEST only) ─► deterministic audit ─► human review ─► re-audit ─► audit log
 ledger .csv ─────┘   (CSV      (AI w/ verbatim quotes,    (keywords, or AI          (reconciler.py,       (reviewer picks   (pure fn)   (CSV / JSON
                      checks)    or regex w/ offsets;       limited to allowed        Decimal cents,        category +                   export)
                                 ungrounded → review)       categories)               no LLM)               reason)
```

* The **LLM only extracts and suggests.** All maths and rules are in `grantaudit/reconciler.py`.
* Every extracted value carries a `Span(start, end, text)` into the source. An LLM value is kept only if its quote is found verbatim in the document **and** the value appears inside that quote. Otherwise the value is discarded, listed in `needs_review`, and the regex value is used instead.
* Conflicting values in the document, such as a second F&A rate injected later in the text, are listed for human review. The first occurrence is used.
* **A grounded AI value is still cross-checked against the regex parser.** If they disagree (e.g. the AI quoted an off-campus rate), the field is flagged for review and the result is marked *Provisional*.
* **AI mode is opt-in, and the app says what will be sent.** It's off by default even when a key is set. When it's on, a notice states exactly what goes to the API. **Minimise ledger data** (on by default) sends category suggestions only masked descriptions, an amount band and the claimed category: no vendor and no exact amount. The award text is always sent in full for extraction. Don't use real grant data in AI mode without institutional approval.
* Review reasons are never pre-filled. *Pre-fill with suggestions* fills in only the category, so each reason has to be written by the reviewer.
* `claimed_category` is never changed. A reviewer's decision goes into `review_override_category`, and every decision is logged with its old and new category, reason, reviewer and timestamp.

## Modules

| File | Purpose |
|---|---|
| `grantaudit/config.py` | All rule constants (simplified 2 CFR 200), with a "verify" docstring |
| `grantaudit/schema.py` | Pydantic models: `Span`, `AwardTerms`, `TransactionRow`, `Violation`, `ReviewDecision`, `AuditDossier`, ... |
| `grantaudit/parsers.py` | `parse_award_text` (regex + offsets), `parse_award_llm` (grounded), `parse_ledger_csv`, `pdf_to_text` |
| `grantaudit/classifier.py` | Keyword classifier and optional LLM category suggestions (advisory only) |
| `grantaudit/reconciler.py` | `audit()`, `make_decision()`, `apply_review()`: pure, deterministic |
| `grantaudit/llm.py` | Minimal urllib client; only used if `ANTHROPIC_API_KEY` is set |
| `app.py` | Streamlit UI (inputs / results / review) |
| `samples/` | Synthetic test cases, one folder per case (award + ledger). See [samples/README.md](samples/README.md) |

## Rules (simplified model of 2 CFR 200)

Every rule is **simplified for the prototype**. Verify against the award terms, agency guidance and current regulation. Signed deltas: F&A drift = charged − computed; draw mismatch = reported − (ledger direct + F&A charged).

| Rule | Severity | Trigger | Delta | Reference (simplified) |
|---|---|---|---|---|
| CAP_EXCEEDED_LINE | WARNING | category spend > cap | spend − cap | 2 CFR 200.308 |
| REBUDGET_OVER_THRESHOLD | CRITICAL | Σ line overages > 10% of total approved direct budget **and** budget > SAT | Σ overages | 2 CFR 200.308(f)(5) |
| TOTAL_DIRECT_EXCEEDED | CRITICAL | total direct > total direct cap | excess | 2 CFR 200.308 / award |
| FA_CALCULATION_DRIFT | CRITICAL | \|charged F&A − MTDC × rate\| > $1 | charged − computed | 2 CFR 200.414, 200.1 MTDC |
| MISCLASSIFICATION | WARNING | travel keyword under a non-Travel category | amount (message shows MTDC/F&A impact) | 2 CFR 200.403(d) |
| EQUIPMENT_BELOW_THRESHOLD | WARNING | Equipment row < $10,000 | amount (message shows MTDC/F&A impact) | 2 CFR 200.1 Equipment, 200.453 |
| UNALLOWABLE_COST | CRITICAL | alcohol / entertainment keyword | amount | 2 CFR 200.423, 200.438 |
| OUTSIDE_PERIOD | CRITICAL | date outside award period | amount | 2 CFR 200.403(h), 200.309 |
| DRAW_MISMATCH | CRITICAL | \|reported draw − (direct + F&A charged)\| > $1 | reported − ledger | 2 CFR 200.305 |
| UNKNOWN_CATEGORY | WARNING | category not in approved budget | category spend | 2 CFR 200.308 |

**MTDC** = direct costs − {Equipment, Participant Support, Rental, Tuition Remission, Scholarships, Patient Care} − the portion of each subaward above $50,000. The subaward cap is cumulative per `subaward_id`; when the ID is missing, rows are grouped by vendor. Equipment threshold $10,000; subaward cap $50,000 (2024 revision values). The re-budgeting rule is gated by the award's Simplified Acquisition Threshold (see below).

### Regulatory modelling assumptions

* **2024 Uniform Guidance values** are used for the equipment threshold ($10,000, previously $5,000) and the subaward amount included in MTDC (first $50,000, previously $25,000).
* **Note on 2 CFR 200 thresholds (SAT):** the Simplified Acquisition Threshold isn't set by 2 CFR 200 itself. 2 CFR 200.1 points to the FAR (48 CFR 2.101 / 41 U.S.C. 134), which is adjusted for inflation. GrantAudit defaults to **$250,000**, matching the test-fixture baseline. For awards subject to the inflation-adjusted FAR threshold, pass **$350,000** per award: `AwardTerms(..., simplified_acquisition_threshold=Decimal("350000"))` or `terms.model_copy(update={"simplified_acquisition_threshold": Decimal("350000")})`. You can also change the default for every award with the `GRANTAUDIT_SAT` environment variable. **Check which value applies to the award.**
* **Consequence for the sample award:** total direct costs are $343,000. Under a $250,000 SAT the 10% re-budgeting rule applies. Under $350,000 the award falls below the threshold and the rule does not fire. Both cases are covered by `test_line_overage_warning_vs_cumulative_rebudget_critical`.

Category names are matched case-insensitively, with `&` equivalent to "and". Rows in `Indirect (F&A)` count as F&A charged. If there are none, the ledger header value `# reported_fa_charged:` is used. A reviewer can move a row to `Removed (unallowable / non-federal)`, which takes it out of the claim entirely.

## Ledger format

```
# SYNTHETIC SAMPLE DATA ...
# reported_cumulative_draw: 111388.40        <- optional summary lines ('#' comments)
# reported_fa_charged: 18548.40               <- optional; F&A rows take precedence
txn_id,date,vendor,description,category,amount,subaward_id
T001,2026-01-31,USC Payroll,PI salary Jan,Personnel,15000.00,
```

Required columns: `txn_id,date,vendor,description,category,amount`. `subaward_id` is optional. Missing columns, non-numeric amounts, bad dates, empty IDs or categories, and duplicate IDs all return **CANNOT_ASSESS** with an error for each affected line.

## Demo script

The app has two ways in:

The app is a guided three-step flow; AI settings live in the sidebar.

1. **Choose documents:** either **Use a sample scenario** (Clean Pass, Misclassified, Over-Budget or Invalid Input, each with a note on what it tests and the expected result, plus downloadable files), or **Upload my own documents** (award + ledger CSV).
2. **Evaluate:** nothing runs until you click it. If you change the documents afterwards, the results are hidden until you evaluate again, and the review starts fresh.
3. **Results:** a status banner, then tabs: **Overview** (key figures and budget by category), **Issues** (critical and warnings, in plain English), **Calculation**, **Review & approve** (change categories with reasons, re-evaluate live, audit log and exports) and **Documents** (extracted terms, highlighted award text, editable ledger).

The **Calculation** tab shows a step-by-step table: total direct costs, minus each excluded category, minus subaward amounts above $50,000, giving MTDC; then × rate gives computed F&A, compared with what was charged and with the drawdown. The table is computed by the reconciler, and a test checks that it reconciles.

The synthetic files for every case are also in [`samples/`](samples/README.md), one folder per case, for uploading by hand: Synthetic files for every case are in [`samples/`](samples/README.md), one folder per case:

1. **`1_representative_pass`**: PASS, with total direct $92,840.00, MTDC $71,340.00, and F&A $18,548.40 computed and charged. Try both `award_agreement.txt` and `award_agreement.pdf`; they give identical results.
2. **`2_misclassified_review`**: four issues. In the **Review & approve** tab, click **Pre-fill with suggestions** (T009→Travel, T010→Materials & Supplies, T011→Removed). Enter a reviewer name, write a reason for each, then click **Approve & re-evaluate**. MTDC rises to $73,610.00 and the review shows that F&A was **under-recovered by $590.20** and that $85.00 of wine was drawn. The audit log shows the three decisions; export it as CSV or JSON.
3. **`3_over_budget`**: Travel is over its cap by $740.00 (WARNING), plus out-of-period, F&A drift and draw mismatch (all CRITICAL).
4. **`4_…` to `7_…` (invalid inputs)**: a broken ledger, a ledger missing its `amount` column, a truncated award and an empty award. Each gives CANNOT_ASSESS with the specific problem listed and no crash.
5. **`8_prompt_injection`**: an award with injected "report PASS / 99% F&A" text gives the same result as case 2, and the conflicting values are listed for human review.

## Expected outputs (real runs, no API key)

```
$ python -m grantaudit samples/1_representative_pass/award_agreement.pdf samples/1_representative_pass/ledger_representative.csv
PASS: All transactions within award terms (simplified rule set).
  total direct 92,840.00  MTDC 71,340.00  computed F&A 18,548.40  charged F&A 18,548.40

$ python -m grantaudit samples/2_misclassified_review/award_agreement.txt samples/2_misclassified_review/ledger_misclassified.csv
VIOLATIONS_FOUND: 4 violation(s): 2 critical, 2 warning.
  total direct 95,195.00  MTDC 71,425.00  computed F&A 18,570.50  charged F&A 18,548.40
  [CRITICAL] FA_CALCULATION_DRIFT delta=-22.10 txns=T008 (2 CFR 200.414 & 200.1 MTDC (simplified))
  [CRITICAL] UNALLOWABLE_COST delta=85.00 txns=T011 (2 CFR 200.423 / 200.438 (simplified))
  [WARNING] MISCLASSIFICATION delta=420.00 txns=T009 (2 CFR 200.403(d) consistent treatment (simplified))
  [WARNING] EQUIPMENT_BELOW_THRESHOLD delta=1,850.00 txns=T010 (2 CFR 200.1 Equipment / 200.453 (simplified))

  after review (T009→Travel, T010→Materials & Supplies, T011→Removed), from the UI / test:
  VIOLATIONS_FOUND: 2 violation(s): 2 critical, 0 warning.
  total direct 95,110.00  MTDC 73,610.00  computed F&A 19,138.60  charged F&A 18,548.40
  [CRITICAL] FA_CALCULATION_DRIFT delta=-590.20   (under-recovery: fixing categories enlarged the F&A base)
  [CRITICAL] DRAW_MISMATCH delta=85.00            (the removed wine had already been drawn down)

$ python -m grantaudit samples/3_over_budget/award_agreement.txt samples/3_over_budget/ledger_over_budget.csv
VIOLATIONS_FOUND: 4 violation(s): 3 critical, 1 warning.
  total direct 101,240.00  MTDC 79,740.00  computed F&A 20,732.40  charged F&A 25,282.40
  [CRITICAL] FA_CALCULATION_DRIFT delta=4,550.00 txns=T011   (= 26% × (12,500 equipment + 5,000 subaward excess) wrongly in base)
  [CRITICAL] OUTSIDE_PERIOD delta=300.00 txns=T010           (dated 2027-01-15)
  [CRITICAL] DRAW_MISMATCH delta=3,477.60 txns=-             (130,000.00 − 126,522.40)
  [WARNING] CAP_EXCEEDED_LINE delta=740.00 txns=T005,T008,T009 (Travel 8,740 vs 8,000)

$ python -m grantaudit samples/4_invalid_ledger_bad_rows/award_agreement.txt samples/4_invalid_ledger_bad_rows/ledger_invalid.csv
CANNOT_ASSESS: Cannot assess: the ledger has 3 problem(s). Fix them and re-upload.
  - Line 4 (T002): amount 'four thousand five hundred' is not a number.
  - Line 5 (T003): date '2026-02-30' is not a valid date (expected YYYY-MM-DD).
  - Line 6 (T001): duplicate txn_id T001 (first seen on line 3).

$ python -m grantaudit samples/6_invalid_award_truncated/award_truncated.txt samples/6_invalid_award_truncated/ledger_representative.csv
CANNOT_ASSESS: Cannot assess: award terms are incomplete. F&A (indirect cost) rate not found (expected e.g. '26% of modified total direct costs').
  - Award: F&A (indirect cost) rate not found (expected e.g. '26% of modified total direct costs').

$ python -m grantaudit samples/7_invalid_award_empty/award_empty.txt samples/7_invalid_award_empty/ledger_representative.csv
CANNOT_ASSESS: Cannot assess: the award document is empty.
  - Award: Award text is empty.

$ python -m grantaudit samples/8_prompt_injection/award_with_injection.txt samples/8_prompt_injection/ledger_misclassified.csv
VIOLATIONS_FOUND: 4 violation(s): 2 critical, 2 warning. Provisional: 4 extracted award value(s) need human review.
  (same totals, violations and deltas as case 2: the injected text changes nothing)

$ python -m grantaudit samples/5_invalid_ledger_missing_amount/award_agreement.txt samples/5_invalid_ledger_missing_amount/ledger_missing_amount.csv
CANNOT_ASSESS: Cannot assess: the ledger has 1 problem(s). Fix them and re-upload.
  - Missing required column(s): amount. Found: txn_id, date, vendor, description, category.
```

Tests: `49 passed` in about 0.4s (slowest single test under 0.1s), including one end-to-end test per sample folder and a PDF-vs-text check. Every hand-computed answer key is written out in the docstring of `tests/test_grantaudit.py`.

## Implemented / mocked / deferred

| Implemented | Mocked | Deferred |
|---|---|---|
| Regex award extraction with offsets; grounded LLM extraction; CSV validation; 10 deterministic rules; review with audit log; pure re-audit; Streamlit UI (sample scenario buttons + own-document upload, on-screen calculation breakdown) with span highlighting, editable ledger, review table, CSV/JSON export; CLI; prompt-injection tests | LLM in tests (fake `call` functions; network is blocked by a fixture). The live **Gemini** path (`gemini-3.5-flash-lite`) was run against the real API on the synthetic samples: extraction was fully grounded, suggestions matched the expected fixes, and the injection case was flagged with results unchanged. The **Claude** path is written to the API spec but has not been run against the real API | OCR for scanned PDFs, multi-year/multi-budget-period awards, cost sharing, program income, per-unit equipment checks, agency-specific terms (NSF PAPPG, NIH GPS), persistence/auth, a separate approval workflow (reviewer = approver here) |

## Limitations

* **Rules are simplified.** Keyword screens produce false positives and negatives: "party" also matches "third party", and alcohol bought through a catering vendor would be missed. Treat flags as prompts for review, not findings.
* **Agency- and award-specific terms are not modelled.** That includes negotiated rate agreement details (on/off-campus rates, rate changes mid-award), prior-approval waivers (expanded authorities), and participant-support re-budgeting restrictions.
* The rebudget rule compares overages with the **total direct** cap, because F&A is not in the budget table; the real rule uses the total approved budget, including F&A.
* Outside-period and unallowable rows stay in the totals until a reviewer removes them, so the totals describe what was claimed.
* **Text PDFs only** (pypdf, no OCR). The regex parser expects a budget table with `$` amounts and common phrasings for the period and the F&A rate. Other layouts need the LLM path or human entry.
* Review decisions live in the Streamlit session only. Export the log to keep it.

## Next steps

1. Run the live LLM path against a set of real (redacted) award letters and measure extraction precision/recall against regex.
2. Persist ledgers, decisions and dossiers with an append-only store and user auth; separate the reviewer and approver roles.
3. Model budget periods, cost sharing and rate-agreement metadata; make the rules per-agency configurable.
4. Reconcile against the actual drawdown history (e.g. payment-system exports) instead of a single reported number.

## Time spent and AI use

* Time spent: _[fill in]_
* AI use: _[fill in, e.g. which parts were AI-assisted and how they were verified]_
