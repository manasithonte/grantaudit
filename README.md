# GrantAudit

GrantAudit checks a research grant's **spending ledger** (CSV) against its **award agreement** (text or PDF). It flags costs likely to be disallowed at audit, with exact dollar amounts, and lets a human reviewer correct categories. The audit recalculates after each correction, and every decision is logged.


**No paid service or account is needed.** Everything runs offline. 

---

## Quick start

Requires **Python 3.10 or newer** (`python3 --version`). If your default `python3` is older, use a specific version such as `python3.12`.

```bash
python3.12 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py                   # opens http://localhost:8501
```

In the app, leave **Use a sample scenario** selected, choose **Misclassified**, and click **Evaluate**.

## Dependencies

| Package | Used for |
|---|---|
| `pydantic` ≥ 2.5 | Data models and validation |
| `streamlit` ≥ 1.40 | Web app |
| `pytest` ≥ 7 | Tests |
| `pypdf` ≥ 4 | Reading text-based PDF awards (optional; `.txt` works without it) |

The optional AI providers are called with Python's standard library (`urllib`), so there are no SDKs to install.

## Running it

| What | Command |
|---|---|
| Web app | `streamlit run app.py` |
| Command line | `python -m grantaudit <award.txt or .pdf> <ledger.csv> [--json] [--ai]` (offline unless `--ai`) |
| Tests (offline, under 1 second) | `python -m pytest -q` |

## Using the app

```
① Choose documents   (•) Use a sample scenario   ( ) Upload my own documents
② Evaluate           [ Evaluate ]
③ Results            status banner, then tabs:
                     Overview · Issues · Calculation · Review & approve · Documents
```

1. **Choose documents.** Pick a sample scenario (each explains what it tests and what to expect), or upload your own award (`.txt` or text-based `.pdf`) and ledger (`.csv`).
2. **Click Evaluate.** Nothing runs until you do. If you change the documents later, the results are hidden until you evaluate again.
3. **Read the results:**
   - **Overview**: key figures and the budget by category.
   - **Issues**: critical issues and warnings in plain English, with the dollar amount, the transactions involved and the regulation reference.
   - **Calculation**: step by step from total direct costs to the F&A base (MTDC) to the F&A that should be charged, then compared with what was charged and with the drawdown.
   - **Documents**: the extracted award terms with their source text highlighted, and the ledger.
4. **Review & approve:**
   - pick a new category for a flagged transaction (or click **Pre-fill with suggestions**);
   - write a reason and enter your name;
   - click **Approve & re-evaluate**.

   The results update, and the decision goes into the audit log, which you can export as CSV or JSON.

The sidebar has the AI settings and **Start over**.

## Sample data and expected outputs

All files are synthetic and are in [`samples/`](samples/README.md), one folder per case. Cases 1–5 use the same synthetic award: `USC-NSF-2026-0142`, 2026-01-01 to 2026-12-31, F&A 26% of MTDC, total direct cap $343,000. Cases 6–8 use a truncated, an empty and a prompt-injected version of it.

| App scenario / folder | Expected result |
|---|---|
| **Clean Pass**: `1_representative_pass` (award also provided as a PDF) | **PASS**. Total direct $92,840.00 · MTDC $71,340.00 · F&A $18,548.40, which equals the amount charged |
| **Misclassified**: `2_misclassified_review` | **4 issues**: F&A charged incorrectly −$22.10, wine (unallowable) $85.00, hotel booked as Equipment, $1,850 laptop booked as Equipment |
| ↳ after review (hotel → Travel, laptop → Materials & Supplies, wine → Removed) | **2 issues**: MTDC $73,610.00, F&A under-charged **−$590.20**, drawdown mismatch **+$85.00** |
| **Over-Budget**: `3_over_budget` | **4 issues**: Travel over cap +$740.00 (warning); purchase dated 2027-01-15 outside the period $300.00, F&A over-charged +$4,550.00, drawdown mismatch +$3,477.60 (critical) |
| **Invalid Input**: `4_invalid_input` | **Cannot assess**: F&A rate missing from the award; ledger line 4 amount not a number, line 5 invalid date, line 6 duplicate ID |


Command-line output from real runs (no API key):

```text
$ python -m grantaudit samples/2_misclassified_review/award_agreement.txt samples/2_misclassified_review/ledger_misclassified.csv
VIOLATIONS_FOUND: 4 violation(s): 2 critical, 2 warning.
  total direct 95,195.00  MTDC 71,425.00  computed F&A 18,570.50  charged F&A 18,548.40
  [CRITICAL] FA_CALCULATION_DRIFT delta=-22.10 txns=T008 (2 CFR 200.414 & 200.1 MTDC (simplified))
  [CRITICAL] UNALLOWABLE_COST delta=85.00 txns=T011 (2 CFR 200.423 / 200.438 (simplified))
  [WARNING] MISCLASSIFICATION delta=420.00 txns=T009 (2 CFR 200.403(d) consistent treatment (simplified))
  [WARNING] EQUIPMENT_BELOW_THRESHOLD delta=1,850.00 txns=T010 (2 CFR 200.1 Equipment / 200.453 (simplified))

$ python -m grantaudit samples/1_representative_pass/award_agreement.pdf samples/1_representative_pass/ledger_representative.csv
PASS: All transactions within award terms (simplified rule set).
  total direct 92,840.00  MTDC 71,340.00  computed F&A 18,548.40  charged F&A 18,548.40

$ python -m grantaudit samples/4_invalid_ledger_bad_rows/award_agreement.txt samples/4_invalid_ledger_bad_rows/ledger_invalid.csv
CANNOT_ASSESS: Cannot assess: the ledger has 3 problem(s). Fix them and re-upload.
  - Line 4 (T002): amount 'four thousand five hundred' is not a number.
  - Line 5 (T003): date '2026-02-30' is not a valid date (expected YYYY-MM-DD).
  - Line 6 (T001): duplicate txn_id T001 (first seen on line 3).
```

The hand-worked calculations for every case are in the docstring of [`tests/test_grantaudit.py`](tests/test_grantaudit.py) and in each scenario's **Calculation** tab.

## Input formats

**Award agreement:** `.txt` or a text-based `.pdf` (not scanned). The rule-based reader looks for:
- an award number, e.g. `Award Number: USC-NSF-2026-0142`;
- the period, e.g. "beginning January 1, 2026 and ending December 31, 2026";
- the F&A rate, e.g. "26% of modified total direct costs";
- a budget table with `$` amounts, including a "Total Direct Costs" row.

If any required term is missing, the result is **Cannot assess**, naming what's missing.

**Ledger:** a CSV file.

```text
# reported_cumulative_draw: 111388.40      <- optional: total drawn from the funder
# reported_fa_charged: 18548.40             <- optional: used only if there are no "Indirect (F&A)" rows
txn_id,date,vendor,description,category,amount,subaward_id
T001,2026-01-31,USC Payroll,PI salary Jan,Personnel,15000.00,
T006,2026-03-01,UCLA,Subaward invoice 1,Subawards,55000.00,UCLA-01
T008,2026-03-31,USC,F&A Q1,Indirect (F&A),18548.40,
```

- **Required columns:** `txn_id, date, vendor, description, category, amount`. `subaward_id` is optional.
- **Validation:** missing columns, non-numeric amounts, invalid dates, empty fields and duplicate IDs all give **Cannot assess**, with an error for each affected line.

## Optional AI

AI is **off by default** and only does two things:
1. reads the award terms;
2. suggests categories.

The audit itself is always plain code.

| Provider | Key | Default model |
|---|---|---|
| Gemini (Google) | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | `gemini-3.5-flash-lite` |


**Setting it up:**
1. Run `cp .env.example .env`, then put your key in `.env`. The file is git-ignored. Keys exported in your shell take precedence over it.
2. Optional overrides: `GRANTAUDIT_MODEL` (model), `GRANTAUDIT_PROVIDER=gemini` (if both keys are set; Claude is the default).
3. In the app, turn on **Use AI** in the sidebar. A notice states exactly what will be sent.

**Safeguards:**
- Every AI value must come with a word-for-word quote that is found in the document and contains the value. Values that fail are discarded, and the rule-based reader's value is used instead.
- AI values that disagree with the rule-based reader are flagged, and the result is marked *Provisional*.
- **Minimise ledger data** (on by default) sends only descriptions with vendor names and digits masked, plus an amount band (no vendor field, no exact amount), for category suggestions. The Review tab shows exactly what is sent. It does not detect personal names typed into descriptions.
- Suggestions are never applied without a reviewer's approval and reason.
- If the AI call fails, the app falls back to rule-based reading and says so.

The Gemini path was tested live on the synthetic samples. 

## How it works

```text
award .txt/.pdf ─► read terms (rules, or AI checked against quotes) ─┐
ledger .csv ─────► validate ─────────────────────────────────────────┴─► suggest categories ─► audit (10 rules) ─► review ─► re-audit ─► audit log
                                                                          (advisory only)       (exact decimals)    (reason    (same      (CSV/JSON)
                                                                                                                     required)  code)
```

| File | Purpose |
|---|---|
| `grantaudit/parsers.py` | Reads the award (rules or AI, with source positions) and validates the ledger |
| `grantaudit/classifier.py` | Category suggestions (keywords or AI); never changes the data |
| `grantaudit/reconciler.py` | `audit()`, `make_decision()`, `apply_review()`: deterministic rules and arithmetic |
| `grantaudit/config.py` | Every regulatory constant in one place, marked "simplified; verify" |
| `grantaudit/schema.py` | Data models |
| `grantaudit/llm.py` | Optional Gemini/Claude client and `.env` loading |
| `app.py` | Streamlit app |
| `tests/test_grantaudit.py` | 49 offline tests with hand-computed answer keys |

## Rules (simplified model of 2 CFR 200)

| Issue | Severity | Trigger |
|---|---|---|
| Budget line over its cap | Warning | Category spending above its cap |
| Re-budgeting above 10% | Critical | Total overages above 10% of the budget, for awards above the simplified acquisition threshold |
| Total direct costs over cap | Critical | Total direct costs above the award cap |
| F&A charged incorrectly | Critical | F&A charged differs from MTDC × rate by more than $1 |
| Likely miscategorised (travel) | Warning | Travel wording under a category other than Travel |
| Below the equipment threshold | Warning | An Equipment item under $10,000 |
| Unallowable cost | Critical | Alcohol or entertainment keywords |
| Outside the award period | Critical | Dated before the start or after the end |
| Drawdown doesn't match the ledger | Critical | Reported drawdown differs from direct costs + F&A charged by more than $1 |
| Category not in the budget | Warning | A category that isn't in the award |

**MTDC (the F&A base)** = direct costs, minus equipment, participant support, rental, tuition remission, scholarships and patient care, minus the part of each subaward above $50,000.

**Values used** (2024 Uniform Guidance, where they changed):
- equipment threshold $10,000;
- subaward amount counted in MTDC: the first $50,000.

**Simplified acquisition threshold:** defaults to **$250,000**. It is set by the FAR and adjusted for inflation, so check which value applies to the award. It can be set per award or with `GRANTAUDIT_SAT`. All constants are in `grantaudit/config.py`.

## Implemented, mocked, deferred

- **Implemented:** rule-based and AI award reading with source links; ledger validation; 10 rules with exact decimal arithmetic; calculation breakdown; human review with reasons and an audit log; live re-evaluation; export; web app, CLI and tests.
- **Synthetic or mocked:** all sample data is synthetic. Tests use fake AI responses and block the network. The live Gemini call was tested on synthetic data only; the Claude path was not run live.
- **Deferred:** OCR for scanned PDFs, multi-year budgets and amendments, cost sharing, agency-specific rules, login and saved data, a separate approver role, and direct accounting-system integration.

## Limitations

- **Keyword checks are blunt.** "Party" also matches "third party", and alcohol bought through a caterer would be missed. Treat flags as prompts for review.
- **Award-specific terms aren't modelled:** e.g. off-campus rates, rate changes during the award, or prior-approval waivers.
- **The re-budgeting rule uses the total *direct* budget**, because the budget table has no F&A line.
- **Review decisions are kept only for the browser session.** Export the log to keep it.
- **Edits made in the Documents tab's ledger table are not logged.** Use Review & approve for category changes.



