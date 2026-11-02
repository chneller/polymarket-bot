"""Tests for Polymarket fee model (https://docs.polymarket.com/trading/fees)."""
import pytest

from src import fees


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("TRADING_FEE_RATE", "FEE_RATE_CRYPTO"):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize(
    "price,expected",
    [(0.01, 0.07), (0.10, 0.63), (0.30, 1.47), (0.50, 1.75), (0.70, 1.47), (0.90, 0.63), (0.99, 0.07)],
)
def test_crypto_fee_table_100_shares(price, expected):
    # Docs crypto table, 100 shares, feeRate 0.07
    assert fees.taker_fee(100, price, 0.07) == pytest.approx(expected, abs=0.005)


@pytest.mark.parametrize("rate,expected", [(0.05, 1.25), (0.04, 1.00)])
def test_other_category_peaks(rate, expected):
    assert fees.taker_fee(100, 0.5, rate) == pytest.approx(expected)


def test_half_price_is_3_5_pct_of_notional_for_crypto():
    assert fees.fee_fraction_of_notional(0.5, 0.07) == pytest.approx(0.035)


def test_symmetric_in_price():
    assert fees.taker_fee(100, 0.3, 0.07) == fees.taker_fee(100, 0.7, 0.07)


def test_rounding_and_edges():
    assert fees.taker_fee(0.0001, 0.01, 0.07) == 0.0  # rounds below 0.00001
    assert fees.taker_fee(10, 0.0, 0.07) == 0.0
    assert fees.taker_fee(10, 1.0, 0.07) == 0.0
    assert fees.taker_fee(-1, 0.5, 0.07) == 0.0


def test_category_rates():
    assert fees.category_fee_rate("crypto") == 0.07
    assert fees.category_fee_rate("geopolitics") == 0.0
    assert fees.category_fee_rate("politics_fees") == 0.04
    assert fees.category_fee_rate("unknown") == 0.05


def test_category_env_override(monkeypatch):
    monkeypatch.setenv("FEE_RATE_CRYPTO", "0.1")
    assert fees.category_fee_rate("crypto") == 0.1


def test_market_fee_params_prefers_schedule():
    m = {"feesEnabled": True, "feeType": "crypto_fees",
         "feeSchedule": {"rate": 0.04, "exponent": 1, "takerOnly": True, "rebateRate": 0.25}}
    assert fees.market_fee_params(m) == (0.04, 1.0, True)


def test_market_fee_params_disabled_and_fallback():
    assert fees.market_fee_params({"feesEnabled": False})[0] == 0.0
    assert fees.market_fee_params({"feeType": "crypto_fees"})[0] == 0.07
    assert fees.market_fee_params({})[0] == fees.category_fee_rate()


def test_legacy_flat_override(monkeypatch):
    monkeypatch.setenv("TRADING_FEE_RATE", "0.01")
    assert fees.estimate_taker_fee(100, 0.5) == pytest.approx(0.5)


def test_estimate_default_uses_formula():
    assert fees.estimate_taker_fee(100, 0.5) == pytest.approx(1.75)
