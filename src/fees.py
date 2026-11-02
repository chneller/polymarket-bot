"""Polymarket trading-fee model.

Source: https://docs.polymarket.com/trading/fees (verified 2026-11-02)

    fee (USDC) = shares * feeRate * (p * (1 - p)) ** exponent      (exponent = 1 today)

* Only takers pay; makers are never charged (``takerOnly``).
* Fees are rounded to 5 decimals (min 0.00001 USDC).
* Per-market parameters come from the Gamma market object:
  ``feesEnabled`` and ``feeSchedule = {rate, exponent, takerOnly, rebateRate}``.
* When a market carries no schedule, a per-category fallback is used
  (``FEE_RATE_<CATEGORY>`` env vars override the defaults below).
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional, Tuple

# Documented taker fee rates by category (docs table, Nov 2026).
DEFAULT_CATEGORY_FEE_RATES = {
    "crypto": 0.07,
    "sports": 0.05,
    "finance": 0.04,
    "politics": 0.04,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "other": 0.05,
    "mentions": 0.04,
    "tech": 0.04,
    "geopolitics": 0.0,
    "combos": 0.05,
}

DEFAULT_CATEGORY = os.getenv("FEE_DEFAULT_CATEGORY", "crypto").strip().lower() or "crypto"


def category_fee_rate(category: Optional[str] = None) -> float:
    """Fallback taker fee rate for a market category (env-overridable)."""
    cat = (category or DEFAULT_CATEGORY).strip().lower().replace("_fees", "")
    env = os.getenv(f"FEE_RATE_{cat.upper()}")
    if env is not None:
        try:
            return max(float(env), 0.0)
        except ValueError:
            pass
    return DEFAULT_CATEGORY_FEE_RATES.get(cat, DEFAULT_CATEGORY_FEE_RATES["other"])


def taker_fee(shares: float, price: float, fee_rate: float, exponent: float = 1.0) -> float:
    """Taker fee in USDC for ``shares`` traded at ``price`` (docs formula, rounded to 5 dp)."""
    if shares <= 0 or fee_rate <= 0 or not 0.0 < price < 1.0:
        return 0.0
    return round(shares * fee_rate * (price * (1.0 - price)) ** exponent, 5)


def fee_per_share(price: float, fee_rate: float, exponent: float = 1.0) -> float:
    """Unrounded taker fee per share in USDC (price units)."""
    if fee_rate <= 0 or not 0.0 < price < 1.0:
        return 0.0
    return fee_rate * (price * (1.0 - price)) ** exponent


def fee_fraction_of_notional(price: float, fee_rate: float, exponent: float = 1.0) -> float:
    """Taker fee as a fraction of notional (shares * price)."""
    if not 0.0 < price < 1.0:
        return 0.0
    return fee_per_share(price, fee_rate, exponent) / price


def market_fee_params(market: Optional[Mapping[str, Any]]) -> Tuple[float, float, bool]:
    """Return (fee_rate, exponent, taker_only) for a Gamma market dict.

    Prefers the market's own ``feeSchedule``; ``feesEnabled == False`` means zero;
    otherwise falls back to the category rate (``feeType``/``category``).
    """
    market = market or {}
    if market.get("feesEnabled") is False:
        return 0.0, 1.0, True
    sched = market.get("feeSchedule") or market.get("fee_schedule")
    if isinstance(sched, Mapping) and sched.get("rate") is not None:
        try:
            return (
                max(float(sched["rate"]), 0.0),
                float(sched.get("exponent", 1) or 1),
                bool(sched.get("takerOnly", sched.get("taker_only", True))),
            )
        except (TypeError, ValueError):
            pass
    category = market.get("feeType") or market.get("category")
    return category_fee_rate(str(category) if category else None), 1.0, True


def legacy_flat_rate() -> Optional[float]:
    """Deprecated: explicit TRADING_FEE_RATE env (flat fraction of notional), if set."""
    raw = os.getenv("TRADING_FEE_RATE")
    if raw is None or raw.strip() == "":
        return None
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return None


def estimate_taker_fee(shares: float, price: float, fee_rate: Optional[float] = None,
                       exponent: float = 1.0) -> float:
    """Fee estimate used by strategies; honours the deprecated flat override if set."""
    flat = legacy_flat_rate()
    if flat is not None:
        return round(shares * price * flat, 5)
    rate = category_fee_rate() if fee_rate is None else fee_rate
    return taker_fee(shares, price, rate, exponent)
