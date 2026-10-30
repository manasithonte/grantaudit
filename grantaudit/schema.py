"""Pydantic data model for GrantAudit."""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .config import CENTS, SIMPLIFIED_ACQUISITION_THRESHOLD


def money(value) -> Decimal:
    """Convert to Decimal quantized to cents (never via float)."""
    if isinstance(value, float):
        value = repr(value)
    return Decimal(str(value)).quantize(CENTS, rounding=ROUND_HALF_UP)


class Span(BaseModel):
    start: int
    end: int
    text: str


class CategoryCap(BaseModel):
    name: str
    cap: Decimal
    mtdc_excluded: bool = False
    span: Optional[Span] = None

    @field_validator("cap", mode="before")
    @classmethod
    def _cents(cls, v):
        return money(v)


class AwardTerms(BaseModel):
    grant_id: str
    period_start: date
    period_end: date
    categories: dict[str, CategoryCap]
    fa_rate: Decimal  # as a fraction, e.g. 0.26
    total_direct_cap: Decimal
    # Gates the 10% re-budgeting rule (2 CFR 200.308(f)(5)). Defaults to the
    # $250,000 baseline (config.py); pass e.g. Decimal("350000") for awards
    # subject to the inflation-adjusted FAR threshold. See config.py.
    simplified_acquisition_threshold: Decimal = Field(
        default=SIMPLIFIED_ACQUISITION_THRESHOLD,
        ge=0,
        description="SAT for this award ($250k baseline; $350k after the FAR inflation adjustment) - verify per award",
    )
    spans: dict[str, Span] = Field(default_factory=dict)
    extraction_source: Literal["llm", "regex"] = "regex"
    needs_review: list[str] = Field(default_factory=list)


class CannotAssess(BaseModel):
    """Returned instead of a result when inputs are too incomplete to audit."""
    status: Literal["CANNOT_ASSESS"] = "CANNOT_ASSESS"
    message: str
    errors: list[str] = Field(default_factory=list)


class TransactionRow(BaseModel):
    txn_id: str
    date: date
    vendor: str
    description: str
    claimed_category: str
    amount: Decimal
    subaward_id: Optional[str] = None
    suggested_category: Optional[str] = None
    suggestion_confidence: Optional[float] = None
    suggestion_source: Optional[Literal["ai", "keyword"]] = None
    suggestion_reason: Optional[str] = None  # shown to the reviewer; never used by the audit
    review_override_category: Optional[str] = None

    @field_validator("amount", mode="before")
    @classmethod
    def _cents(cls, v):
        return money(v)

    @property
    def effective_category(self) -> str:
        return self.review_override_category or self.claimed_category


class LedgerSummary(BaseModel):
    reported_cumulative_draw: Optional[Decimal] = None
    reported_fa_charged: Optional[Decimal] = None


class ViolationType(str, Enum):
    CAP_EXCEEDED_LINE = "CAP_EXCEEDED_LINE"
    REBUDGET_OVER_THRESHOLD = "REBUDGET_OVER_THRESHOLD"
    TOTAL_DIRECT_EXCEEDED = "TOTAL_DIRECT_EXCEEDED"
    FA_CALCULATION_DRIFT = "FA_CALCULATION_DRIFT"
    MISCLASSIFICATION = "MISCLASSIFICATION"
    EQUIPMENT_BELOW_THRESHOLD = "EQUIPMENT_BELOW_THRESHOLD"
    UNALLOWABLE_COST = "UNALLOWABLE_COST"
    OUTSIDE_PERIOD = "OUTSIDE_PERIOD"
    DRAW_MISMATCH = "DRAW_MISMATCH"
    UNKNOWN_CATEGORY = "UNKNOWN_CATEGORY"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"


class Violation(BaseModel):
    type: ViolationType
    severity: Severity
    message: str
    delta: Decimal
    affected_txn_ids: list[str] = Field(default_factory=list)
    rule_ref: str


class ReviewDecision(BaseModel):
    txn_id: str
    old_category: str
    new_category: str
    reason: str
    reviewer: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now().replace(microsecond=0))


class CategorySummary(BaseModel):
    cap: Optional[Decimal]
    spent: Decimal
    remaining: Optional[Decimal]


class AuditDossier(BaseModel):
    status: Literal["PASS", "VIOLATIONS_FOUND", "CANNOT_ASSESS"]
    message: str
    total_direct: Decimal = Decimal("0.00")
    mtdc: Decimal = Decimal("0.00")
    computed_fa: Decimal = Decimal("0.00")
    charged_fa: Optional[Decimal] = None
    violations: list[Violation] = Field(default_factory=list)
    category_summaries: dict[str, CategorySummary] = Field(default_factory=dict)
    review_log: list[ReviewDecision] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    # Calculation breakdown (for "show the maths"): total_direct - exclusions - subaward_excess = mtdc
    fa_rate: Optional[Decimal] = None
    mtdc_exclusions: dict[str, Decimal] = Field(default_factory=dict)
    subaward_detail: dict[str, dict[str, Decimal]] = Field(default_factory=dict)
    subaward_excess: Decimal = Decimal("0.00")
    removed_total: Decimal = Decimal("0.00")
    reported_draw: Optional[Decimal] = None

    def by_type(self, vtype: str) -> list[Violation]:
        return [v for v in self.violations if v.type.value == vtype]
