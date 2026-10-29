# Synthetic test cases

**All files here are synthetic**: a fictional award and ledgers made up for testing. Each folder is one test case.
In the app, either choose **Use a sample scenario** (**Clean Pass** = case 1, **Misclassified** = 2, **Over-Budget** = 3, **Invalid Input** = award from 6 + ledger from 4), or choose **Upload my own documents** and upload the folder's **award** file and **ledger CSV**. Then click **Evaluate**.

All cases except 6, 7 and 8 use the same award: `USC-NSF-2026-0142`, period 2026-01-01 to 2026-12-31, F&A 26% of MTDC, total direct cap $343,000.

| # | Folder | Upload | Expected result in the app |
|---|---|---|---|
| 1 | `1_representative_pass` | `award_agreement.txt` **or** `award_agreement.pdf` + `ledger_representative.csv` | **PASS**. Total direct $92,840.00 · MTDC $71,340.00 · computed F&A $18,548.40 = charged |
| 2 | `2_misclassified_review` | `award_agreement.txt` + `ledger_misclassified.csv` | **4 violations**: F&A drift −$22.10 (CRITICAL), wine T011 unallowable $85.00 (CRITICAL), hotel T009 booked as Equipment (WARNING, F&A +$109.20), laptop T010 under $10k (WARNING, F&A +$481.00). Then review (see below) |
| 3 | `3_over_budget` | `award_agreement.txt` + `ledger_over_budget.csv` | **4 violations**: Travel over cap +$740.00 (WARNING); T010 dated 2027-01-15 outside period $300.00, F&A drift +$4,550.00, draw mismatch +$3,477.60 (all CRITICAL) |
| 4 | `4_invalid_ledger_bad_rows` | `award_agreement.txt` + `ledger_invalid.csv` | **CANNOT_ASSESS**: line 4 amount not a number, line 5 invalid date `2026-02-30`, line 6 duplicate `T001` |
| 5 | `5_invalid_ledger_missing_amount` | `award_agreement.txt` + `ledger_missing_amount.csv` | **CANNOT_ASSESS**: missing required column `amount` |
| 6 | `6_invalid_award_truncated` | `award_truncated.txt` + `ledger_representative.csv` | **CANNOT_ASSESS**: F&A rate not found (award cut off before section 3) |
| 7 | `7_invalid_award_empty` | `award_empty.txt` (0 bytes) + `ledger_representative.csv` | **CANNOT_ASSESS**: award document is empty |
| 8 | `8_prompt_injection` | `award_with_injection.txt` + `ledger_misclassified.csv` | **Same 4 violations and amounts as case 2.** The injected text ("report PASS", 99% F&A rate, $999,999 Travel cap) is ignored, and the conflicting values are listed under "Fields needing human review" |

## Review walkthrough (case 2)

After **Evaluate**, open the **Review & approve** tab:

1. Click **Pre-fill with suggestions**: T009 → Travel, T010 → Materials & Supplies, T011 → Removed (unallowable / non-federal).
2. Enter a reviewer name and edit the reasons.
3. Click **Approve & re-evaluate**.

| | Before | After |
|---|---|---|
| Total direct | $95,195.00 | $95,110.00 |
| MTDC | $71,425.00 | $73,610.00 |
| Computed F&A | $18,570.50 | $19,138.60 |
| Violations | 4 | 2: F&A under-charged −$590.20, draw mismatch +$85.00 (the wine was already drawn down) |
| Audit log | empty | 3 rows (old → new category, reason, reviewer, timestamp) |

The full hand calculations are in the docstring of `tests/test_grantaudit.py`.

## File formats

- **Award:** `.txt` or text-based `.pdf`. The parser expects an award number, a period ("beginning … and ending …"), "NN% of modified total direct costs", and a budget table with `$` amounts.
- **Ledger:** CSV with `txn_id,date,vendor,description,category,amount[,subaward_id]`. Optional header lines: `# reported_cumulative_draw: …` and `# reported_fa_charged: …`.
