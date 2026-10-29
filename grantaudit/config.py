"""Rule constants for GrantAudit.

SIMPLIFIED FOR PROTOTYPE; verify against the award terms and current regulation.

These values are a deliberately small, labelled model of 2 CFR 200 (Uniform
Guidance). Real awards carry agency- and award-specific terms (e.g. NSF PAPPG,
NIH GPS), negotiated rate agreements and institutional policies that override or
refine everything here. Nothing in this module is legal or compliance advice.
"""
from decimal import Decimal

# 2 CFR 200.1 "Equipment" / 200.439 - per-unit acquisition cost threshold.
# The 2024 revision raised this from $5,000 to $10,000.
EQUIPMENT_THRESHOLD = Decimal("10000")

# 2 CFR 200.1 "Modified Total Direct Cost (MTDC)" - each subaward counts toward
# MTDC only up to the first $50,000 (2024 revision; was $25,000).
SUBAWARD_MTDC_CAP = Decimal("50000")

# Categories excluded from MTDC (2 CFR 200.1, simplified).
MTDC_EXCLUDED = {
    "Equipment",
    "Participant Support",
    "Rental",
    "Tuition Remission",
    "Scholarships",
    "Patient Care",
}

# Category names that the reconciler treats specially.
SUBAWARD_CATEGORY = "Subawards"
FA_CATEGORY = "Indirect (F&A)"
TRAVEL_CATEGORY = "Travel"
EQUIPMENT_CATEGORY = "Equipment"
SUPPLIES_CATEGORY = "Materials & Supplies"
# A reviewer can move a transaction here to remove it from the claim entirely
# (e.g. an unallowable cost that will be moved to a non-federal account).
REMOVED_CATEGORY = "Removed (unallowable / non-federal)"

# 2 CFR 200.423 (alcohol) and 200.438 (entertainment) - keyword screen only.
UNALLOWABLE_KEYWORDS = {
    "alcohol": ["wine", "beer", "liquor", "bar tab"],
    "entertainment": ["tickets", "concert", "party", "game"],
}

# 2 CFR 200.475 - keywords that suggest a cost is travel.
TRAVEL_KEYWORDS = ["hotel", "lodging", "airfare", "flight", "marriott", "hilton", "uber", "mileage"]

# 2 CFR 200.308(f)(5) (simplified) - cumulative transfers among direct cost
# categories above this % of the total approved budget need prior approval,
# when the Federal share exceeds the Simplified Acquisition Threshold.
REBUDGET_THRESHOLD_PCT = Decimal("10")

# Simplified Acquisition Threshold (SAT). 2 CFR 200.1 does not set a number; it
# points to the FAR (48 CFR 2.101 / 41 U.S.C. 134), which is inflation-adjusted.
# $250,000 is the long-standing baseline and the test-fixture default; a FAR
# inflation adjustment has reportedly raised it to $350,000. The applicable
# value depends on the award's dates and terms - VERIFY before relying on it.
# This is only the DEFAULT: set AwardTerms.simplified_acquisition_threshold per
# award, or override the default with env var GRANTAUDIT_SAT.
import os as _os

SIMPLIFIED_ACQUISITION_THRESHOLD = Decimal(_os.environ.get("GRANTAUDIT_SAT", "250000"))

# Money comparisons: drifts at or below this tolerance are treated as rounding.
MONEY_TOLERANCE = Decimal("1.00")
CENTS = Decimal("0.01")

# Optional LLM settings (only used if ANTHROPIC_API_KEY is set).
DEFAULT_MODEL = "claude-haiku-5-5"            # Anthropic (ANTHROPIC_API_KEY)
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"  # Google (GEMINI_API_KEY); stable, low-cost. Override: GRANTAUDIT_MODEL

RULE_REFS = {
    "CAP_EXCEEDED_LINE": "2 CFR 200.308 (simplified)",
    "REBUDGET_OVER_THRESHOLD": "2 CFR 200.308(f)(5) (simplified)",
    "TOTAL_DIRECT_EXCEEDED": "2 CFR 200.308 / award terms (simplified)",
    "FA_CALCULATION_DRIFT": "2 CFR 200.414 & 200.1 MTDC (simplified)",
    "MISCLASSIFICATION": "2 CFR 200.403(d) consistent treatment (simplified)",
    "EQUIPMENT_BELOW_THRESHOLD": "2 CFR 200.1 Equipment / 200.453 (simplified)",
    "UNALLOWABLE_COST": "2 CFR 200.423 / 200.438 (simplified)",
    "OUTSIDE_PERIOD": "2 CFR 200.403(h) & 200.309 (simplified)",
    "DRAW_MISMATCH": "2 CFR 200.305 (simplified)",
    "UNKNOWN_CATEGORY": "2 CFR 200.308 approved budget (simplified)",
}
