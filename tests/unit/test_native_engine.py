"""
Tests for the bs-p native engine bridge (src/native/pmkernel.py).

Validates that native C results match pure-Python fallbacks within tolerance,
and that the ctypes struct layout is correct.
"""

import ctypes
import math

import numpy as np
import pytest

from src.native.pmkernel import NATIVE_AVAILABLE

requires_native = pytest.mark.skipif(
    not NATIVE_AVAILABLE, reason="native bs-p library not built (run scripts/build_native.sh)"
)


@requires_native
def test_native_available():
    from src.native.pmkernel import NATIVE_AVAILABLE
    assert NATIVE_AVAILABLE is True, "Native library not loaded — run scripts/build_native.sh"


def test_greek_out_struct_layout():
    from src.native.pmkernel import GreekOut
    assert ctypes.sizeof(GreekOut) == 16
    assert GreekOut.delta_x.offset == 0
    assert GreekOut.gamma_x.offset == 8


class TestSigmoidLogit:
    def test_sigmoid_zero(self):
        from src.native.pmkernel import sigmoid
        assert abs(sigmoid(0.0) - 0.5) < 1e-12

    def test_logit_half(self):
        from src.native.pmkernel import logit
        assert abs(logit(0.5)) < 1e-12

    def test_roundtrip(self):
        from src.native.pmkernel import sigmoid, logit
        for p in [0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99]:
            # Pade tanh approximation loses ~1e-7 precision at tails
            assert abs(sigmoid(logit(p)) - p) < 1e-6, f"roundtrip failed at p={p}"

    def test_batch(self):
        from src.native.pmkernel import sigmoid_batch, logit_batch, sigmoid
        x = np.array([-3.0, -1.0, 0.0, 1.0, 3.0])
        p = sigmoid_batch(x)
        assert len(p) == 5
        for i in range(5):
            assert abs(p[i] - sigmoid(x[i])) < 1e-12

        x_back = logit_batch(p)
        np.testing.assert_allclose(x_back, x, atol=1e-9)


class TestGreeks:
    def test_at_midpoint(self):
        from src.native.pmkernel import greeks_from_logit
        d, g = greeks_from_logit(0.0)
        assert abs(d - 0.25) < 1e-9
        assert abs(g) < 1e-9

    def test_batch_matches_scalar(self):
        from src.native.pmkernel import greeks_batch, greeks_from_logit
        x = np.array([-2.0, -1.0, 0.0, 1.0, 2.0])
        delta, gamma = greeks_batch(x)
        for i in range(5):
            d, g = greeks_from_logit(x[i])
            assert abs(delta[i] - d) < 1e-12
            assert abs(gamma[i] - g) < 1e-12


class TestCalculateQuotes:
    def test_symmetric_at_zero_inventory(self):
        from src.native.pmkernel import calculate_quotes
        q = calculate_quotes(x_t=0.0, q_t=0.0, sigma_b=0.5, gamma=1.0, tau=0.05, k=2.0)
        assert q.bid_p < 0.5
        assert q.ask_p > 0.5
        assert abs(q.bid_p + q.ask_p - 1.0) < 1e-6, "quotes should be symmetric around 0.5 at zero inventory"

    def test_inventory_shifts_quotes(self):
        from src.native.pmkernel import calculate_quotes
        q_no_inv = calculate_quotes(x_t=0.0, q_t=0.0, sigma_b=0.5, gamma=1.0, tau=0.05, k=2.0)
        q_long = calculate_quotes(x_t=0.0, q_t=5.0, sigma_b=0.5, gamma=1.0, tau=0.05, k=2.0)
        assert q_long.bid_p < q_no_inv.bid_p, "long inventory should lower bid"
        assert q_long.ask_p < q_no_inv.ask_p, "long inventory should lower ask"


class TestAdaptiveKelly:
    def test_zero_inventory(self):
        from src.native.pmkernel import adaptive_kelly_clip
        r = adaptive_kelly_clip(belief_p=0.6, market_p=0.5, q_t=0.0, gamma=1.0,
                                risk_limit=10.0, max_clip=5.0)
        assert r.taker_clip > 0
        assert r.maker_clip > 0
        assert abs(r.maker_clip - 0.5 * r.taker_clip) < 1e-9

    def test_inventory_reduces_size(self):
        from src.native.pmkernel import adaptive_kelly_clip
        r0 = adaptive_kelly_clip(belief_p=0.6, market_p=0.5, q_t=0.0, gamma=1.0,
                                 risk_limit=10.0, max_clip=5.0)
        r5 = adaptive_kelly_clip(belief_p=0.6, market_p=0.5, q_t=5.0, gamma=1.0,
                                 risk_limit=10.0, max_clip=5.0)
        assert abs(r5.taker_clip) < abs(r0.taker_clip), "inventory should reduce Kelly size"

    def test_no_edge_no_bet(self):
        from src.native.pmkernel import adaptive_kelly_clip
        r = adaptive_kelly_clip(belief_p=0.5, market_p=0.5, q_t=0.0, gamma=1.0,
                                risk_limit=10.0, max_clip=5.0)
        assert abs(r.taker_clip) < 1e-9
        assert abs(r.maker_clip) < 1e-9

    def test_native_vs_fallback_parity(self):
        """Verify native engine matches pure-Python fallback."""
        from src.native import pmkernel
        lib_backup = pmkernel._lib
        try:
            # Force fallback
            pmkernel._lib = None
            r_py = pmkernel.adaptive_kelly_clip(0.65, 0.50, 2.0, 0.8, 10.0, 5.0)
            # Restore native
            pmkernel._lib = lib_backup
            r_c = pmkernel.adaptive_kelly_clip(0.65, 0.50, 2.0, 0.8, 10.0, 5.0)
            assert abs(r_py.taker_clip - r_c.taker_clip) < 1e-9
            assert abs(r_py.maker_clip - r_c.maker_clip) < 1e-9
        finally:
            pmkernel._lib = lib_backup


class TestOrderBookMicrostructure:
    def test_balanced_book(self):
        from src.native.pmkernel import order_book_microstructure
        r = order_book_microstructure(bid_p=0.48, ask_p=0.52, bid_vol=100.0, ask_vol=100.0)
        assert abs(r.obi) < 1e-9, "balanced book should have OBI ~0"
        assert abs(r.vwm_p - 0.50) < 1e-6

    def test_bid_heavy(self):
        from src.native.pmkernel import order_book_microstructure
        r = order_book_microstructure(bid_p=0.48, ask_p=0.52, bid_vol=200.0, ask_vol=50.0)
        assert r.obi > 0.5
        assert r.pressure > 0


class TestKellyIntegration:
    @requires_native
    def test_kelly_size_with_inventory(self):
        from src.sizing.kelly import kelly_size
        r0 = kelly_size(estimated_prob=0.6, market_price=0.5, bankroll=1000.0, inventory_q=0.0)
        r5 = kelly_size(estimated_prob=0.6, market_price=0.5, bankroll=1000.0, inventory_q=50.0)
        assert r0.bet_size_usd > 0
        assert r5.bet_size_usd <= r0.bet_size_usd, "inventory should reduce bet size"
        assert r0.native_sized is True
        assert r5.native_sized is True

    def test_kelly_backwards_compatible(self):
        """Existing callers that don't pass inventory_q still work."""
        from src.sizing.kelly import kelly_size
        r = kelly_size(estimated_prob=0.6, market_price=0.5, bankroll=1000.0)
        assert r.bet_size_usd > 0
        assert r.inventory_q == 0.0
