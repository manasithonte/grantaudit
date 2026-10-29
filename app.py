"""GrantAudit - Streamlit prototype UI.  Run: streamlit run app.py

Flow: 1. choose documents (sample scenario or your own) -> 2. Evaluate -> 3. results in tabs
(Overview, Issues, Calculation, Review & approve, Documents).

All sample data is SYNTHETIC. Works fully offline; AI is used only if an API key is
set (ANTHROPIC_API_KEY or GEMINI_API_KEY, e.g. in .env) and the sidebar toggle is on.
"""
from __future__ import annotations

import hashlib
import html
import io
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from grantaudit import llm
from grantaudit.classifier import suggest_categories
from grantaudit.config import FA_CATEGORY, REMOVED_CATEGORY
from grantaudit.parsers import parse_award_llm, parse_award_text, parse_ledger_csv, pdf_to_text
from grantaudit.reconciler import apply_review, audit, make_decision
from grantaudit.schema import AwardTerms, CannotAssess

LEDGER_COLS = ["txn_id", "date", "vendor", "description", "category", "amount", "subaward_id"]
SAMPLES = Path(__file__).parent / "samples"
SAMPLE, UPLOAD = "Use a sample scenario", "Upload my own documents"

# Synthetic scenarios. "maths" is the hand-computed answer key shown on screen.
SCENARIOS = {
    "Clean Pass": {
        "award": "1_representative_pass/award_agreement.txt",
        "ledger": "1_representative_pass/ledger_representative.csv",
        "tests": "A correctly kept ledger: every cost is in the right category, within its cap and inside the award period.",
        "expect": "PASS, no issues.",
        "maths": "Total direct $92,840.00 − equipment $12,500.00 − participant support $4,000.00 − subaward above "
                 "$50k ($55,000 − $50,000 = $5,000.00) = **MTDC $71,340.00**. × 26% = **$18,548.40** computed, "
                 "which equals the F&A charged. Draw $111,388.40 = $92,840.00 + $18,548.40.",
    },
    "Misclassified": {
        "award": "2_misclassified_review/award_agreement.txt",
        "ledger": "2_misclassified_review/ledger_misclassified.csv",
        "tests": "Three problem rows added to the clean ledger: a hotel booked as Equipment (T009), a $1,850 laptop booked as "
                 "Equipment (T010) and wine charged to Materials & Supplies (T011).",
        "expect": "4 issues. Then fix them in the Review & approve tab.",
        "maths": "Before review: MTDC $71,340.00 + $85.00 wine = **$71,425.00** (T009/T010 sit in excluded Equipment); "
                 "× 26% = $18,570.50 vs $18,548.40 charged → drift **−$22.10**. After moving T009 → Travel, "
                 "T010 → Supplies and removing T011: MTDC = $71,340 + $420 + $1,850 = **$73,610.00**; × 26% = "
                 "$19,138.60 → F&A under-charged by **$590.20**, and the $85.00 wine had already been drawn (draw mismatch **+$85.00**).",
    },
    "Over-Budget": {
        "award": "3_over_budget/award_agreement.txt",
        "ledger": "3_over_budget/ledger_over_budget.csv",
        "tests": "Travel spending above its cap, a purchase after the award ended, F&A charged on the wrong base, "
                 "and a drawdown that doesn't match the books.",
        "expect": "4 issues: Travel over cap (warning); outside period, F&A drift, draw mismatch (critical).",
        "maths": "Travel $640 + $3,200 + $4,900 = $8,740 vs $8,000 cap → **+$740.00**. MTDC = $101,240 − $12,500 − $4,000 − "
                 "$5,000 = **$79,740.00**; × 26% = $20,732.40 vs $25,282.40 charged → **+$4,550.00** "
                 "(= 26% × the $12,500 equipment + $5,000 subaward excess wrongly included). T010 dated 2027-01-15 → "
                 "outside period **$300.00**. Draw $130,000.00 − ($101,240 + $25,282.40) = **+$3,477.60**.",
    },
    "Invalid Input": {
        "award": "6_invalid_award_truncated/award_truncated.txt",
        "ledger": "4_invalid_ledger_bad_rows/ledger_invalid.csv",
        "tests": "A truncated award (cut off before the F&A rate) and a ledger with a non-numeric amount, an impossible date "
                 "and a duplicate transaction ID.",
        "expect": "Cannot assess, with each problem listed. No partial numbers and no crash.",
        "maths": "No calculation is attempted: auditing incomplete or invalid data would produce misleading totals.",
    },
}

ISSUE_TITLES = {
    "CAP_EXCEEDED_LINE": "Budget line over its cap",
    "REBUDGET_OVER_THRESHOLD": "Re-budgeting above 10% needs prior approval",
    "TOTAL_DIRECT_EXCEEDED": "Total direct costs over the award cap",
    "FA_CALCULATION_DRIFT": "Indirect costs (F&A) charged incorrectly",
    "MISCLASSIFICATION": "Likely miscategorised: looks like travel",
    "EQUIPMENT_BELOW_THRESHOLD": "Below the equipment threshold: likely supplies",
    "UNALLOWABLE_COST": "Unallowable cost",
    "OUTSIDE_PERIOD": "Cost outside the award period",
    "DRAW_MISMATCH": "Drawdown doesn't match the ledger",
    "UNKNOWN_CATEGORY": "Category not in the approved budget",
}

st.set_page_config(page_title="GrantAudit", page_icon="🧾", layout="wide")
llm.load_dotenv()  # optional API keys from .env in the project folder
ss = st.session_state
ss.setdefault("review_log", [])
ss.setdefault("upload_version", 0)


# --------------------------------------------------------------------------- helpers

@st.cache_data(show_spinner="Reading the award agreement...", max_entries=64)
def extract_terms(text: str, use_ai: bool, ai_id: str = ""):  # ai_id (provider/model) is part of the cache key
    return parse_award_llm(text) if use_ai else parse_award_text(text)


@st.cache_data(show_spinner="Suggesting categories...", max_entries=64)
def cached_suggestions(rows_json: str, allowed: tuple, use_ai: bool, minimise: bool, ai_id: str = ""):
    from grantaudit.schema import TransactionRow
    rows = [TransactionRow.model_validate(r) for r in json.loads(rows_json)]
    return [r.model_dump(mode="json") for r in suggest_categories(rows, list(allowed), use_llm=use_ai, minimise=minimise)]


def input_signature(award_text: str, ledger_csv) -> str:
    return hashlib.sha1(f"{award_text}\0{ledger_csv}".encode()).hexdigest()[:10]


def load_sample(name: str) -> tuple[str, str]:
    sc = SCENARIOS[name]
    return (SAMPLES / sc["award"]).read_text(), (SAMPLES / sc["ledger"]).read_text()


def money(d) -> str:
    return "-" if d is None else (f"-${abs(d):,.2f}" if d < 0 else f"${d:,.2f}")


def pct(rate) -> str:
    return f"{(rate * 100).normalize():f}%"


def badge(text: str, color: str) -> str:
    return (f"<span style='background:{color};color:white;padding:2px 9px;border-radius:10px;"
            f"font-weight:600;font-size:0.8em'>{html.escape(text)}</span>")


def highlighted(text: str, terms: AwardTerms) -> str:
    spans = sorted(
        [(s, k) for k, s in terms.spans.items()] + [(c.span, f"cap: {c.name}") for c in terms.categories.values() if c.span],
        key=lambda x: x[0].start,
    )
    out, pos = [], 0
    for sp, label in spans:
        if sp.start < pos:
            continue
        out.append(html.escape(text[pos:sp.start]))
        out.append(f"<mark title='{html.escape(label)}' style='background:#ffe58f'>{html.escape(text[sp.start:sp.end])}</mark>")
        pos = sp.end
    out.append(html.escape(text[pos:]))
    return "<pre style='white-space:pre-wrap;font-size:0.78em;line-height:1.35;margin:0'>" + "".join(out) + "</pre>"


def breakdown_markdown(d) -> str:
    lines = ["| Step | Amount |", "|---|---:|",
             f"| Total direct costs (everything except F&A{'; reviewer-removed rows excluded' if d.removed_total else ''}) "
             f"| {money(d.total_direct)} |"]
    for cat, amt in d.mtdc_exclusions.items():
        lines.append(f"| − {cat} (not part of the F&A base) | −{money(amt)} |")
    for sid, x in d.subaward_detail.items():
        if x["total"] > x["counted"]:
            lines.append(f"| − Subaward {sid}: {money(x['total'])} paid, only the first {money(x['counted'])} counts "
                         f"| −{money(x['total'] - x['counted'])} |")
    lines.append(f"| **= MTDC (the F&A base)** | **{money(d.mtdc)}** |")
    lines.append(f"| × F&A rate {pct(d.fa_rate)} = **F&A that should be charged** | **{money(d.computed_fa)}** |")
    if d.charged_fa is not None:
        diff = d.charged_fa - d.computed_fa
        lines.append(f"| F&A actually charged | {money(d.charged_fa)} |")
        lines.append(f"| Difference (charged − should be) | {money(diff)} {'✅' if abs(diff) <= 1 else '❌'} |")
    if d.reported_draw is not None:
        ledger_total = d.total_direct + (d.charged_fa or 0)
        diff = d.reported_draw - ledger_total
        lines.append(f"| Direct costs + F&A charged | {money(ledger_total)} |")
        lines.append(f"| Money drawn from the funder (reported) | {money(d.reported_draw)} |")
        lines.append(f"| Difference (drawn − ledger) | {money(diff)} {'✅' if abs(diff) <= 1 else '❌'} |")
    return "\n".join(lines)


# --------------------------------------------------------------------------- sidebar: settings

with st.sidebar:
    st.header("Settings")
    AI_ID = f"{llm.provider()}/{llm.model_name()}" if llm.available() else ""
    use_ai = st.toggle("Use AI to read documents & suggest categories", value=False, disabled=not llm.available(),
                       help="Needs ANTHROPIC_API_KEY or GEMINI_API_KEY (e.g. in .env). The audit maths never uses AI.")
    minimise = st.toggle("Minimise ledger data sent to AI", value=True, disabled=not use_ai,
                         help="Category suggestions get only masked descriptions and an amount band.")
    if not llm.available():
        st.caption("No API key found: **offline mode** (rule-based reading). Nothing leaves this machine.")
    elif not use_ai:
        st.caption(f"AI available ({llm.provider_label()}) but off. **Offline mode**: nothing leaves this machine.")
    else:
        st.warning(f"**AI on: {llm.provider_label()}** (`{llm.model_name()}`). The full award text"
                   + (" and masked ledger descriptions" if minimise else " and full ledger rows")
                   + " will be sent. Don't use real grant data without institutional approval.")
    st.divider()
    st.markdown("**How it works**\n\n1. Choose documents\n2. Click **Evaluate**\n3. Check **Issues**, then fix "
                "categories in **Review & approve**. Totals recalculate and every change is logged.")
    st.caption("Rules are a simplified model of 2 CFR 200: verify against the award terms and current regulation. "
               "Sample data is synthetic.")
    if st.button("Start over", use_container_width=True):
        for k in ("evaluated_sig", "review_log", "reviewer"):
            ss.pop(k, None)
        ss.upload_version += 1
        st.rerun()

# --------------------------------------------------------------------------- header

st.title("🧾 GrantAudit")
st.markdown("Checks a grant's **spending ledger** against its **award agreement** and flags costs likely to be "
            "disallowed at audit.")

# --------------------------------------------------------------------------- step 1: choose documents

st.subheader("① Choose documents")
mode = st.radio("Source", [SAMPLE, UPLOAD], horizontal=True, label_visibility="collapsed", key="mode")
award_text, ledger_csv, scenario = "", None, None

if mode == SAMPLE:
    scenario = st.selectbox("Scenario (synthetic data)", list(SCENARIOS), key="scenario")
    sc = SCENARIOS[scenario]
    award_text, ledger_csv = load_sample(scenario)
    st.markdown(f"**What it tests:** {sc['tests']}  \n**Expected:** {sc['expect']}")
    with st.expander("See or download the sample files"):
        d1, d2 = st.columns(2)
        d1.download_button(f"⬇ {Path(sc['award']).name}", award_text, Path(sc["award"]).name, use_container_width=True)
        d2.download_button(f"⬇ {Path(sc['ledger']).name}", ledger_csv, Path(sc["ledger"]).name, use_container_width=True)
        st.caption(f"`samples/{sc['award']}` · `samples/{sc['ledger']}`")
    has_award = True
else:
    c1, c2 = st.columns(2)
    with c1:
        up = st.file_uploader("Award agreement (.txt or text-based .pdf)", type=["txt", "pdf"],
                              key=f"award_upload_{ss.upload_version}")
        if up is not None:
            try:
                raw = up.getvalue()
                award_text = pdf_to_text(raw) if up.name.lower().endswith(".pdf") else raw.decode("utf-8", "replace")
            except Exception as e:
                st.error(f"Could not read {up.name} ({e}). Paste the text instead.")
        else:
            with st.expander("No file? Paste the award text"):
                award_text = st.text_area("Award text", "", height=160, key=f"award_paste_{ss.upload_version}",
                                          label_visibility="collapsed")
        has_award = up is not None or bool(award_text.strip())
    with c2:
        up_csv = st.file_uploader("Spending ledger (.csv)", type=["csv"], key=f"ledger_upload_{ss.upload_version}")
        if up_csv is not None:
            ledger_csv = up_csv.getvalue().decode("utf-8-sig", "replace")
        st.caption("Columns: `txn_id, date, vendor, description, category, amount` (+ optional `subaward_id`).")

ready = has_award and ledger_csv is not None
input_sig = input_signature(award_text, ledger_csv)
evaluated = ready and ss.get("evaluated_sig") == input_sig

# --------------------------------------------------------------------------- step 2: evaluate

st.subheader("② Evaluate")
b1, b2 = st.columns([1, 3])
if b1.button("Evaluate", type="primary", use_container_width=True, disabled=not ready):
    if ss.get("evaluated_sig") != input_sig:
        ss.review_log = []  # new documents -> fresh review session
    ss.evaluated_sig = input_sig
    st.rerun()
with b2:
    if not ready:
        missing = [x for x, ok in [("the award agreement", has_award), ("the ledger CSV", ledger_csv is not None)] if not ok]
        st.caption("Add " + " and ".join(missing) + " to enable Evaluate.")
    elif ss.get("evaluated_sig") not in (None, input_sig) and not evaluated:
        st.caption("⚠️ Documents changed since the last evaluation. Click **Evaluate** to see updated results.")
    elif not evaluated:
        st.caption("Ready. Click **Evaluate** to run the audit"
                   + (f" (AI: {llm.provider_label()})." if use_ai else " (offline mode)."))
    else:
        st.caption("✅ Evaluated. Results are below and update live as you review."
                   + (f" {len(ss.review_log)} review decision(s) applied." if ss.review_log else ""))

if not evaluated:
    st.stop()

# --------------------------------------------------------------------------- step 3: results

st.subheader("③ Results")
terms = extract_terms(award_text, use_ai, AI_ID)
rows, summary, ledger_errors = parse_ledger_csv(ledger_csv)

if isinstance(terms, CannotAssess) or ledger_errors:
    d = audit(terms, rows, summary, ledger_errors=ledger_errors)
    st.warning(d.message)
    st.markdown("**Fix these problems and evaluate again:**\n" + "\n".join(f"- {e}" for e in d.errors))
    if scenario:
        st.caption(f"Expected for this scenario: {SCENARIOS[scenario]['expect']}")
    st.stop()

banner = st.empty()
tabs = st.tabs(["Overview", "Issues", "Calculation", "Review & approve", "Documents"])
t_over, t_issues, t_calc, t_review, t_docs = tabs

# Documents tab is built first because ledger edits made there feed the audit.
with t_docs:
    src = f"AI · {llm.provider_label()}" if terms.extraction_source == "llm" else "Rule-based (regex)"
    st.markdown(f"**Award terms read from the agreement** {badge(src, '#6f42c1' if src.startswith('AI') else '#3b7dd8')}",
                unsafe_allow_html=True)
    st.dataframe(pd.DataFrame(
        [{"Term": "Grant ID", "Value": terms.grant_id},
         {"Term": "Award period", "Value": f"{terms.period_start} to {terms.period_end}"},
         {"Term": "F&A rate", "Value": f"{pct(terms.fa_rate)} of MTDC"},
         {"Term": "Total direct cap", "Value": money(terms.total_direct_cap)}]
        + [{"Term": f"Cap: {c.name}", "Value": money(c.cap)} for c in terms.categories.values()]),
        hide_index=True, use_container_width=True)
    if terms.needs_review:
        st.warning("**Check these values in the agreement:**\n\n" + "\n".join(f"- {n}" for n in terms.needs_review))
    with st.expander("Award text with the extracted values highlighted"):
        st.markdown(highlighted(award_text, terms), unsafe_allow_html=True)

    st.markdown("**Ledger** (editable: changes re-run the audit)")
    meta = [l for l in ledger_csv.splitlines() if l.strip().startswith("#")]
    df = pd.DataFrame([{
        "txn_id": r.txn_id, "date": r.date.isoformat(), "vendor": r.vendor, "description": r.description,
        "category": r.claimed_category, "amount": str(r.amount), "subaward_id": r.subaward_id or "",
    } for r in rows], columns=LEDGER_COLS)
    edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True, hide_index=True,
                               key=f"ledger_editor_{input_sig}")
    if not edited_df.equals(df):
        buf = io.StringIO()
        edited_df.fillna("").to_csv(buf, index=False)
        rows, summary, ledger_errors = parse_ledger_csv("\n".join(meta) + "\n" + buf.getvalue())
        if ledger_errors:
            st.error("Your edits made the ledger invalid:\n\n" + "\n".join(f"- {e}" for e in ledger_errors))
    if summary.reported_cumulative_draw is not None:
        st.caption(f"Reported cumulative drawdown (file header): {money(summary.reported_cumulative_draw)}")

# ---- run the audit ----------------------------------------------------------
if rows and not ledger_errors:
    rows = [type(rows[0]).model_validate(r) for r in cached_suggestions(
        json.dumps([r.model_dump(mode="json") for r in rows]), tuple(terms.categories), use_ai, minimise, AI_ID)]
valid_ids = {r.txn_id for r in rows}
ss.review_log = [x for x in ss.review_log if x.txn_id in valid_ids]
current_rows, log = apply_review(rows, ss.review_log) if rows else (rows, ss.review_log)
dossier = audit(terms, current_rows, summary, review_log=log, ledger_errors=ledger_errors)
crit = [v for v in dossier.violations if v.severity.value == "CRITICAL"]
warn = [v for v in dossier.violations if v.severity.value == "WARNING"]

with banner.container():
    after = f" (after {len(dossier.review_log)} review decision(s))" if dossier.review_log else ""
    if dossier.status == "PASS":
        st.success(f"✅ **PASS**{after}: no issues found under the simplified rule set.")
    elif dossier.status == "CANNOT_ASSESS":
        st.warning(f"**Cannot assess**: {dossier.message} See the **Documents** tab.")
    else:
        st.error(f"❌ **{len(dossier.violations)} issue(s) found**{after}: {len(crit)} critical, {len(warn)} warning. "
                 "See **Issues**, then fix categories in **Review & approve**.")
    if terms.needs_review:
        st.caption(f"⚠️ Provisional: {len(terms.needs_review)} award value(s) need checking (see **Documents**).")

if dossier.status == "CANNOT_ASSESS":
    for t in (t_over, t_issues, t_calc, t_review):
        with t:
            st.info("Fix the ledger in the **Documents** tab to see results.")
    st.stop()

# ---- Overview ---------------------------------------------------------------
with t_over:
    m = st.columns(4)
    m[0].metric("Total direct costs", money(dossier.total_direct))
    m[1].metric("F&A base (MTDC)", money(dossier.mtdc))
    m[2].metric("F&A that should be charged", money(dossier.computed_fa))
    drift = None if dossier.charged_fa is None else dossier.charged_fa - dossier.computed_fa
    m[3].metric("F&A actually charged", money(dossier.charged_fa),
                delta=None if drift is None else f"{money(drift)} difference", delta_color="off")
    st.markdown("**Budget by category**")
    st.dataframe(pd.DataFrame([{
        "Category": k, "Approved": money(v.cap), "Spent": money(v.spent), "Remaining": money(v.remaining),
        "Used": None if not v.cap else float(v.spent / v.cap),
    } for k, v in dossier.category_summaries.items()]), hide_index=True, use_container_width=True,
        column_config={"Used": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})
    if scenario:
        st.caption(f"Scenario **{scenario}**. Expected: {SCENARIOS[scenario]['expect']}")

# ---- Issues -----------------------------------------------------------------
with t_issues:
    if not dossier.violations:
        st.success("No issues found.")
    for heading, group, colour in (("Critical", crit, "#d9534f"), ("Warnings", warn, "#e0a800")):
        if not group:
            continue
        st.markdown(f"**{heading} ({len(group)})**")
        for v in group:
            with st.container(border=True):
                a, b = st.columns([4, 1])
                a.markdown(f"{badge(v.severity.value, colour)} &nbsp;**{ISSUE_TITLES[v.type.value]}**",
                           unsafe_allow_html=True)
                b.markdown(f"<div style='text-align:right;font-weight:600'>{money(v.delta)}</div>", unsafe_allow_html=True)
                st.markdown(v.message)
                st.caption(f"Transactions: {', '.join(v.affected_txn_ids) or 'whole ledger'} · {v.rule_ref} · `{v.type.value}`")

# ---- Calculation ------------------------------------------------------------
with t_calc:
    st.markdown("How the F&A and drawdown figures were worked out, step by step:")
    st.markdown(breakdown_markdown(dossier))
    if dossier.removed_total:
        st.caption(f"{money(dossier.removed_total)} was removed from the claim by a reviewer and is not counted above.")
    st.caption("The F&A base (MTDC) leaves out equipment, participant support, rental, tuition remission, scholarships "
               "and patient care, and counts only the first $50,000 of each subaward (simplified 2 CFR 200.1).")
    if scenario:
        with st.expander("Hand-worked answer key for this scenario"):
            st.markdown(SCENARIOS[scenario]["maths"])

# ---- Review & approve -------------------------------------------------------
with t_review:
    flagged = {t for v in dossier.violations for t in v.affected_txn_ids}
    st.markdown("1. Pick a **new category** for any transaction you want to change (or click *Pre-fill with "
                "suggestions*).\n2. Write a **reason** for each change and enter your name.\n3. Click **Approve & "
                "re-evaluate**. The results update and the change is logged.")
    show_all = st.toggle("Show all transactions (not only flagged ones)", value=False)
    review_rows = [r for r in current_rows if show_all or r.txn_id in flagged]
    if use_ai and current_rows and not any(r.suggestion_source == "ai" for r in current_rows):
        st.warning("AI suggestions were unavailable; showing rule-based suggestions instead.")
    if not review_rows:
        st.success("Nothing flagged for review.")
    else:
        prefill_key = f"prefill_{input_sig}_{len(ss.review_log)}"
        prefill = ss.get(prefill_key, False)
        options = [""] + list(terms.categories) + [FA_CATEGORY, REMOVED_CATEGORY]

        def proposed(r):
            return r.suggested_category if prefill and r.suggested_category not in (None, r.effective_category) else ""

        rdf = pd.DataFrame([{
            "txn_id": r.txn_id, "description": r.description, "amount": float(r.amount),
            "current": r.effective_category, "suggested": r.suggested_category or "",
            "source": {"ai": "AI", "keyword": "rules"}.get(r.suggestion_source or "", ""),
            "confidence": r.suggestion_confidence, "new_category": proposed(r), "reason": "",
        } for r in review_rows])
        redited = st.data_editor(
            rdf, hide_index=True, use_container_width=True, key=f"review_{prefill_key}_{prefill}",
            disabled=["txn_id", "description", "amount", "current", "suggested", "source", "confidence"],
            column_config={
                "amount": st.column_config.NumberColumn(format="$%.2f"),
                "confidence": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f"),
                "new_category": st.column_config.SelectboxColumn("new category", options=options, help="Leave blank to keep"),
                "reason": st.column_config.TextColumn(help="Required for every change"),
            },
        )
        c1, c2, c3 = st.columns([1, 1, 1])
        if c1.button("Pre-fill with suggestions", use_container_width=True,
                     help="Copies suggested categories into 'new category'. You still write each reason."):
            ss[prefill_key] = True
            st.rerun()
        reviewer = c2.text_input("Your name", value=ss.get("reviewer", ""), placeholder="Reviewer name or email",
                                 label_visibility="collapsed")
        ss.reviewer = reviewer
        if c3.button("Approve & re-evaluate", type="primary", use_container_width=True):
            changes = redited[(redited["new_category"].fillna("") != "") & (redited["new_category"] != redited["current"])]
            try:
                decisions = [make_decision(current_rows, c.txn_id, c.new_category, c.reason or "", reviewer)
                             for c in changes.itertuples()]
            except ValueError as e:
                st.error(str(e))
            else:
                if not decisions:
                    st.warning("No category changes selected.")
                else:
                    ss.review_log = ss.review_log + decisions
                    st.rerun()

    st.markdown(f"**Audit log ({len(dossier.review_log)})**")
    if dossier.review_log:
        log_df = pd.DataFrame([x.model_dump(mode="json") for x in dossier.review_log])
        st.dataframe(log_df, hide_index=True, use_container_width=True)
        d1, d2, d3, d4 = st.columns(4)
        d1.download_button("Log (CSV)", log_df.to_csv(index=False), "grantaudit_review_log.csv", "text/csv",
                           use_container_width=True)
        d2.download_button("Log (JSON)", json.dumps([x.model_dump(mode="json") for x in dossier.review_log], indent=2),
                           "grantaudit_review_log.json", "application/json", use_container_width=True)
        d3.download_button("Full report (JSON)", dossier.model_dump_json(indent=2), "grantaudit_dossier.json",
                           "application/json", use_container_width=True)
        if d4.button("Undo all decisions", use_container_width=True):
            ss.review_log = []
            st.rerun()
    else:
        st.caption("No review decisions yet.")
