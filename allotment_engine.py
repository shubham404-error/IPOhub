from dataclasses import dataclass
from math import floor, ceil
from typing import Optional


@dataclass(frozen=True)
class CategoryPlan:
    key: str
    label: str
    min_amount: float
    min_lots: int
    probability: float
    source: str


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def application_probability(app_subscription: Optional[float], share_subscription: Optional[float]):
    """Estimate minimum-application allotment probability.

    Application-wise subscription is preferred because retail/NII allotment
    depends on the number of applicants, not simply the number of shares bid.
    If unavailable, share-wise subscription is used as a clearly-labelled
    fallback proxy.
    """
    app = _num(app_subscription)
    shares = _num(share_subscription)
    if app is not None and app > 0:
        return min(1.0, 1.0 / app), "application subscription"
    if shares is not None and shares > 0:
        return min(1.0, 1.0 / shares), "share subscription proxy"
    return None, "unavailable"


def build_category_plans(row, lot_size: int, price: float):
    lot_value = lot_size * price
    plans = []

    retail_p, retail_source = application_probability(
        row.get("retail_app_subscription"), row.get("retail")
    )
    if retail_p is not None and lot_value <= 200000:
        plans.append(CategoryPlan(
            "retail", "Retail", lot_value, 1, retail_p, retail_source
        ))

    # Mainboard NII rules: sNII is above ₹2L and up to ₹10L;
    # bNII is above ₹10L. We calculate the smallest valid bid in lots.
    s_lots = floor(200000 / lot_value) + 1
    s_amount = s_lots * lot_value
    b_lots = floor(1000000 / lot_value) + 1
    b_amount = b_lots * lot_value

    s_p, s_source = application_probability(
        row.get("snii_app_subscription"), row.get("snii")
    )
    b_p, b_source = application_probability(
        row.get("bnii_app_subscription"), row.get("bnii")
    )

    if s_p is not None and s_amount <= 1000000:
        plans.append(CategoryPlan("snii", "Small HNI", s_amount, s_lots, s_p, s_source))
    if b_p is not None:
        plans.append(CategoryPlan("bnii", "Big HNI", b_amount, b_lots, b_p, b_source))

    return plans


def optimise(plans, capital, accounts):
    """Enumerate account allocations to maximize probability of >=1 allotment.

    Each eligible account receives at most one minimum-size application.
    This matches the objective of maximizing independent allotment tickets,
    rather than maximizing the amount bid in a single account.
    """
    capital = max(0.0, float(capital))
    accounts = max(0, int(accounts))
    best = None

    # Exact enumeration is tiny for the normal use case (1-10 accounts).
    def walk(i, remaining_accounts, remaining_capital, counts):
        nonlocal best
        if i == len(plans):
            used_accounts = sum(counts.values())
            used_capital = sum(
                counts[p.key] * p.min_amount for p in plans
            )
            if used_accounts == 0:
                chance = 0.0
            else:
                miss = 1.0
                for p in plans:
                    miss *= (1.0 - p.probability) ** counts[p.key]
                chance = 1.0 - miss

            # Prefer higher chance, then lower capital, then more used accounts.
            score = (chance, -used_capital, used_accounts)
            if best is None or score > best["score"]:
                best = {
                    "score": score,
                    "chance": chance,
                    "used_capital": used_capital,
                    "unused_capital": capital - used_capital,
                    "used_accounts": used_accounts,
                    "counts": dict(counts),
                }
            return

        p = plans[i]
        max_by_accounts = remaining_accounts
        max_by_capital = int(remaining_capital // p.min_amount) if p.min_amount else 0
        max_n = min(max_by_accounts, max_by_capital)
        for n in range(max_n + 1):
            counts[p.key] = n
            walk(
                i + 1,
                remaining_accounts - n,
                remaining_capital - n * p.min_amount,
                counts,
            )
        counts.pop(p.key, None)

    walk(0, accounts, capital, {})
    return best
