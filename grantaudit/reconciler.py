"""Deterministic compliance audit. Pure functions; no LLM, no I/O.

Rules are a simplified model of 2 CFR 200; see config.py.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Optional, Union

from .classifier import has_keyword, unallowable_match
from .config import (
    EQUIPMENT_CATEGORY,
    EQUIPMENT_THRESHOLD,
    FA_CATEGORY,
    MONEY_TOLERANCE,
    MTDC_EXCLUDED,
    REBUDGET_THRESHOLD_PCT,
    REMOVED_CATEGORY,
    RULE_REFS,
    SUBAWARD_CATEGORY,
    SUBAWARD_MTDC_CAP,
    TRAVEL_CATEGORY,
    TRAVEL_KEYWORDS,
)
from .schema import (
    AuditDossier,
    AwardTerms,
    CannotAssess,
    CategorySummary,
    LedgerSummary,
    ReviewDecision,
    Severity,
    TransactionRow,
    Violation,
    ViolationType,
    money,
)

ZERO = Decimal("0.00")
_EXCLUDED_NORM = {c.lower() for c in MTDC_EXCLUDED}


def _norm(name: str) -> str:
    return " ".join(name.lower().replace("&", " and ").split())


def _fmt(d: Decimal) -> str:
    sign = "-" if d < 0 else ""
    return f"{sign}${abs(d):,.2f}"


def _pct(rate: Decimal) -> str:
    return f"{(rate * 100).normalize():f}%"


def _v(vtype: str, severity: Severity, message: str, delta, txns=()) -> Violation:
    return Violation(
        type=ViolationType(vtype),
        severity=severity,
        message=message,
        delta=money(delta),
        affected_txn_ids=list(txns),
        rule_ref=RULE_REFS[vtype],
    )


def _cannot_assess(message: str, errors: list[str], review_log) -> AuditDossier:
    return AuditDossier(status="CANNOT_ASSESS", message=message, errors=errors, review_log=list(review_log or []))


def audit(
    terms: Union[AwardTerms, CannotAssess],
    rows: list[TransactionRow],
    summary: Optional[LedgerSummary] = None,
    review_log: Optional[list[ReviewDecision]] = None,
    ledger_errors: Optional[list[str]] = None,
) -> AuditDossier:
    review_log = list(review_log or [])
    summary = summary or LedgerSummary()
    if isinstance(terms, CannotAssess):
        errors = [f"Award: {e}" for e in terms.errors] + [f"Ledger: {e}" for e in ledger_errors or []]
        message = terms.message + (f" The ledger also has {len(ledger_errors)} problem(s)." if ledger_errors else "")
        return _cannot_assess(message, errors, review_log)
    if ledger_errors:
        return _cannot_assess(
            f"Cannot assess: the ledger has {len(ledger_errors)} problem(s). Fix them and re-upload.", ledger_errors, review_log
        )
    if not rows:
        return _cannot_assess("Cannot assess: the ledger has no transactions.", ["Ledger has no rows."], review_log)

    # Map normalised names -> award category names so 'Materials and Supplies' == 'Materials & Supplies'.
    award_names = {_norm(n): n for n in terms.categories}
    fa_norm, removed_norm = _norm(FA_CATEGORY), _norm(REMOVED_CATEGORY)

    def canonical(cat: str) -> str:
        return award_names.get(_norm(cat), cat)

    def is_excluded(cat: str) -> bool:
        c = terms.categories.get(cat)
        return (c.mtdc_excluded if c else False) or cat.lower() in _EXCLUDED_NORM

    direct, fa_rows = [], []
    removed_total = ZERO
    for r in rows:
        n = _norm(r.effective_category)
        if n == removed_norm:
            removed_total += r.amount
            continue  # removed from the claim by a reviewer
        (fa_rows if n == fa_norm else direct).append(r)
    cat_of = {r.txn_id: canonical(r.effective_category) for r in direct}

    violations: list[Violation] = []
    rate = terms.fa_rate

    # --- spend by category -------------------------------------------------
    spent: dict[str, Decimal] = defaultdict(lambda: ZERO)
    txns_by_cat: dict[str, list[str]] = defaultdict(list)
    for r in direct:
        spent[cat_of[r.txn_id]] += r.amount
        txns_by_cat[cat_of[r.txn_id]].append(r.txn_id)
    total_direct = money(sum((r.amount for r in direct), ZERO))

    # (a) line caps
    total_overage = ZERO
    for name, c in terms.categories.items():
        over = spent[name] - c.cap
        if over > 0:
            total_overage += over
            violations.append(_v(
                "CAP_EXCEEDED_LINE", Severity.WARNING,
                f"{name}: spent {_fmt(spent[name])} against a cap of {_fmt(c.cap)}; over by {_fmt(over)}.",
                over, txns_by_cat[name],
            ))

    # (b) cumulative re-budgeting
    if total_overage > 0 and terms.total_direct_cap > 0:
        pct = total_overage / terms.total_direct_cap * 100
        if pct > REBUDGET_THRESHOLD_PCT and terms.total_direct_cap > terms.simplified_acquisition_threshold:
            violations.append(_v(
                "REBUDGET_OVER_THRESHOLD", Severity.CRITICAL,
                f"Cumulative line overages of {_fmt(total_overage)} are {pct:.2f}% of the approved budget "
                f"({_fmt(terms.total_direct_cap)}), above the {REBUDGET_THRESHOLD_PCT}% prior-approval threshold.",
                total_overage,
                [t for name in terms.categories if spent[name] > terms.categories[name].cap for t in txns_by_cat[name]],
            ))

    # (c) total direct cap
    if total_direct > terms.total_direct_cap:
        violations.append(_v(
            "TOTAL_DIRECT_EXCEEDED", Severity.CRITICAL,
            f"Total direct costs {_fmt(total_direct)} exceed the award cap of {_fmt(terms.total_direct_cap)}.",
            total_direct - terms.total_direct_cap,
        ))

    # (d) MTDC and F&A
    mtdc = ZERO
    sub_used: dict[str, Decimal] = defaultdict(lambda: ZERO)
    sub_total: dict[str, Decimal] = defaultdict(lambda: ZERO)
    excluded: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for r in sorted(direct, key=lambda r: (r.date, r.txn_id)):
        cat = cat_of[r.txn_id]
        if is_excluded(cat):
            excluded[cat] += r.amount
            continue
        if _norm(cat) == _norm(SUBAWARD_CATEGORY) or r.subaward_id:
            key = r.subaward_id or f"vendor:{r.vendor.lower()}"
            sub_total[key] += r.amount
            room = max(SUBAWARD_MTDC_CAP - sub_used[key], ZERO)
            counted = min(r.amount, room)
            sub_used[key] += counted
            mtdc += counted
        else:
            mtdc += r.amount
    mtdc = money(mtdc)
    computed_fa = money(mtdc * rate)

    if fa_rows:
        charged_fa: Optional[Decimal] = money(sum((r.amount for r in fa_rows), ZERO))
    else:
        charged_fa = summary.reported_fa_charged
    if charged_fa is not None:
        drift = charged_fa - computed_fa
        if abs(drift) > MONEY_TOLERANCE:
            direction = "over-charged" if drift > 0 else "under-recovered"
            violations.append(_v(
                "FA_CALCULATION_DRIFT", Severity.CRITICAL,
                f"F&A charged {_fmt(charged_fa)} but {_pct(rate)} x MTDC {_fmt(mtdc)} = {_fmt(computed_fa)}; "
                f"{direction} by {_fmt(abs(drift))}.",
                drift, [r.txn_id for r in fa_rows],
            ))

    # (f) travel keywords under a non-Travel category
    travel_flagged = set()
    for r in direct:
        cat = cat_of[r.txn_id]
        kw = has_keyword(f"{r.vendor} {r.description}", TRAVEL_KEYWORDS)
        if kw and _norm(cat) != _norm(TRAVEL_CATEGORY):
            travel_flagged.add(r.txn_id)
            mtdc_delta = r.amount if is_excluded(cat) and not is_excluded(canonical(TRAVEL_CATEGORY)) else ZERO
            violations.append(_v(
                "MISCLASSIFICATION", Severity.WARNING,
                f"{r.txn_id} '{r.description}' looks like travel ('{kw}') but is claimed as {cat}. "
                f"Reclassifying to Travel changes MTDC by +{_fmt(mtdc_delta)} and F&A by +{_fmt(money(mtdc_delta * rate))}.",
                r.amount, [r.txn_id],
            ))

    # (e) equipment below threshold
    for r in direct:
        if _norm(cat_of[r.txn_id]) == _norm(EQUIPMENT_CATEGORY) and r.amount < EQUIPMENT_THRESHOLD and r.txn_id not in travel_flagged:
            violations.append(_v(
                "EQUIPMENT_BELOW_THRESHOLD", Severity.WARNING,
                f"{r.txn_id} '{r.description}' ({_fmt(r.amount)}) is below the {_fmt(EQUIPMENT_THRESHOLD)} equipment threshold: "
                f"likely supplies; affects MTDC by +{_fmt(r.amount)} (F&A +{_fmt(money(r.amount * rate))}).",
                r.amount, [r.txn_id],
            ))

    # (g) unallowable costs
    for r in direct + fa_rows:
        hit = unallowable_match(f"{r.vendor} {r.description}")
        if hit:
            violations.append(_v(
                "UNALLOWABLE_COST", Severity.CRITICAL,
                f"{r.txn_id} '{r.description}' ({_fmt(r.amount)}) matches unallowable {hit[0]} keyword '{hit[1]}'. "
                f"Remove it from the federal claim.",
                r.amount, [r.txn_id],
            ))

    # (h) outside award period
    for r in direct + fa_rows:
        if r.date < terms.period_start or r.date > terms.period_end:
            violations.append(_v(
                "OUTSIDE_PERIOD", Severity.CRITICAL,
                f"{r.txn_id} dated {r.date.isoformat()} ({_fmt(r.amount)}) is outside the award period "
                f"{terms.period_start.isoformat()} to {terms.period_end.isoformat()}.",
                r.amount, [r.txn_id],
            ))

    # (i) drawdown reconciliation
    if summary.reported_cumulative_draw is not None:
        ledger_total = total_direct + (charged_fa or ZERO)
        diff = summary.reported_cumulative_draw - ledger_total
        if abs(diff) > MONEY_TOLERANCE:
            violations.append(_v(
                "DRAW_MISMATCH", Severity.CRITICAL,
                f"Reported cumulative draw {_fmt(summary.reported_cumulative_draw)} vs ledger direct + F&A charged "
                f"{_fmt(ledger_total)}: difference {_fmt(diff)}.",
                diff,
            ))

    # (j) categories not in the award
    for cat, amount in spent.items():
        if cat not in terms.categories:
            violations.append(_v(
                "UNKNOWN_CATEGORY", Severity.WARNING,
                f"Category '{cat}' ({_fmt(amount)}) is not in the approved budget.",
                amount, txns_by_cat[cat],
            ))

    summaries = {
        name: CategorySummary(cap=c.cap, spent=money(spent[name]), remaining=money(c.cap - spent[name]))
        for name, c in terms.categories.items()
    }
    for cat, amount in spent.items():
        if cat not in summaries:
            summaries[cat] = CategorySummary(cap=None, spent=money(amount), remaining=None)

    sev_order = {Severity.CRITICAL: 0, Severity.WARNING: 1}
    violations.sort(key=lambda v: sev_order[v.severity])
    n_crit = sum(v.severity == Severity.CRITICAL for v in violations)
    if violations:
        status = "VIOLATIONS_FOUND"
        message = f"{len(violations)} violation(s): {n_crit} critical, {len(violations) - n_crit} warning."
    else:
        status = "PASS"
        message = "All transactions within award terms (simplified rule set)."
    if charged_fa is None:
        message += " No F&A charged in ledger; drift check skipped."
    if terms.needs_review:
        message += f" Provisional: {len(terms.needs_review)} extracted award value(s) need human review."

    return AuditDossier(
        status=status,
        message=message,
        total_direct=total_direct,
        mtdc=mtdc,
        computed_fa=computed_fa,
        charged_fa=charged_fa,
        violations=violations,
        category_summaries=summaries,
        review_log=review_log,
        fa_rate=rate,
        mtdc_exclusions={k: money(v) for k, v in excluded.items()},
        subaward_detail={k: {"total": money(sub_total[k]), "counted": money(sub_used[k])} for k in sub_total},
        subaward_excess=money(sum((sub_total[k] - sub_used[k] for k in sub_total), ZERO)),
        removed_total=money(removed_total),
        reported_draw=summary.reported_cumulative_draw,
    )


# --------------------------------------------------------------------------- #
# Human review
# --------------------------------------------------------------------------- #

def make_decision(rows: list[TransactionRow], txn_id: str, new_category: str, reason: str, reviewer: str) -> ReviewDecision:
    row = next((r for r in rows if r.txn_id == txn_id), None)
    if row is None:
        raise ValueError(f"Unknown txn_id {txn_id}")
    if not reason or not reason.strip():
        raise ValueError(f"A reason is required to recategorise {txn_id}")
    if not reviewer or not reviewer.strip():
        raise ValueError("Reviewer name is required")
    return ReviewDecision(
        txn_id=txn_id, old_category=row.effective_category, new_category=new_category,
        reason=reason.strip(), reviewer=reviewer.strip(),
    )


def apply_review(
    rows: list[TransactionRow], decisions: list[ReviewDecision], log: Optional[list[ReviewDecision]] = None
) -> tuple[list[TransactionRow], list[ReviewDecision]]:
    """Return (new rows with overrides applied, extended audit log). Inputs are not mutated.

    claimed_category is never changed; the reviewer's choice is stored in
    review_override_category so the original claim stays visible.
    """
    by_id = {r.txn_id: r for r in rows}
    new_log = list(log or [])
    for d in decisions:
        if d.txn_id not in by_id:
            raise ValueError(f"Unknown txn_id {d.txn_id}")
        by_id[d.txn_id] = by_id[d.txn_id].model_copy(update={"review_override_category": d.new_category})
        new_log.append(d)
    return [by_id[r.txn_id] for r in rows], new_log
