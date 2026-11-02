"""
Terminal Convergence Strategy

In the final 60 seconds of a 5-minute BTC market, the price MUST converge
to ~0 (NO wins) or ~1 (YES wins). But markets are often slow to fully
converge — a YES outcome that's near-certain may still trade at 90-95¢
instead of 98-99¢.

This strategy buys the underpriced near-certain outcome in the final
window before expiry and holds to resolution.

Characteristics:
    - Very high win rate (>85% when calibrated)
    - Small edge per trade (2-8¢)
    - High frequency (every 5-min market cycle)
    - Requires real-time BTC price to determine which outcome is winning

References:
    - Page & Clemen (2013): Markets are well-calibrated short-term but mispriced near edges
    - Terminal value theorem: binary options must converge to 0 or 1 at expiry
"""

from __future__ import annotations

import time
from typing import List, Dict, Any, Optional
from datetime import datetime

from .base_strategy import BaseStrategy, Signal, SignalType, MarketData
from ..logging_utils import cprint
from ..fees import category_fee_rate
from ..config import (
    TERMINAL_CONVERGENCE_WINDOW_SECONDS,
    TERMINAL_MIN_EDGE_CENTS,
    TERMINAL_CONVERGENCE_1H_ONLY,
    TERMINAL_1H_KEYWORDS,
    TERMINAL_NON_1H_KEYWORDS,
    BTC_5MIN_KEYWORDS,
    ORDER_SIZE_USD,
    MAX_POSITION_USD,
    TRADING_FEE_RATE,
    ENABLE_CRYPTO_EVENT_INFRA,
    clob_gtd_expiration_unix,
)


def _crypto_taker_fee_cents(price: float, fee_rate_bps: Optional[float], fees_enabled: bool) -> float:
    """Estimate taker fee in cents per share using market fee configuration when available."""
    if not fees_enabled:
        return 0.0
    if price <= 0 or price >= 1:
        return 0.0
    if fee_rate_bps is None:
        fee_rate_bps = category_fee_rate() * 10_000
    fee_rate = max(float(fee_rate_bps), 0.0) / 10_000.0
    return 100.0 * fee_rate * price * (1.0 - price)


class TerminalConvergenceStrategy(BaseStrategy):
    """
    Buy underpriced near-certain outcomes in final seconds of 5-min BTC markets.

    Requires a BinanceFeed instance via config["binance_feed"] to determine
    which outcome is likely winning.

    Config options:
        - binance_feed: BinanceFeed instance (required)
        - convergence_window_s: Seconds before expiry to start (default: 60)
        - min_edge_cents: Minimum mispricing in cents (default: 3)
        - order_size_usd: Per-trade size (default: from config)
        - max_position_usd: Max position per market (default: from config)
        - min_certainty: Minimum probability threshold to consider "near-certain" (default: 0.80)
    """

    name = "terminal_convergence"
    description = "Buy underpriced near-certain outcomes in final seconds before 5-min expiry"
    version = "1.0.0"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

        self.binance_feed = self.config.get("binance_feed")
        self.convergence_window_s = self.config.get(
            "convergence_window_s", TERMINAL_CONVERGENCE_WINDOW_SECONDS
        )
        self.min_edge_cents = self.config.get("min_edge_cents", TERMINAL_MIN_EDGE_CENTS)
        self.order_size_usd = self.config.get("order_size_usd", ORDER_SIZE_USD)
        self.max_position_usd = self.config.get("max_position_usd", MAX_POSITION_USD)
        self.min_certainty = self.config.get("min_certainty", 0.80)
        self._1h_only = self.config.get("1h_only", TERMINAL_CONVERGENCE_1H_ONLY)

        # Track positions
        self.positions: Dict[str, float] = {}
        self.last_signal_time: Dict[str, float] = {}
        self.signal_cooldown_s = self.config.get("signal_cooldown_s", 15)
        self._last_best_edge_cents = 0.0
        self._last_nearest_expiry_s = 0.0
        self._last_fill_mode = "GTD"

    def should_trade_market(self, market_data: MarketData) -> bool:
        """Trade crypto 1h Up/Down markets within the convergence window (near expiry)."""
        if not ENABLE_CRYPTO_EVENT_INFRA:
            return False

        text = f"{market_data.question} {market_data.market_slug}".lower()

        # Must be crypto (BTC, ETH, SOL, etc.)
        is_crypto = any(kw in text for kw in ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp"])
        # 1h-only mode: only 1h Up/Down (Binance = resolution source)
        # Match: "1h", "hourly", etc. OR "up or down" when NOT 5m/15m/4h
        # e.g. "Bitcoin Up or Down - March 4, 1PM ET"
        if self._1h_only:
            is_shortterm = any(kw in text for kw in TERMINAL_1H_KEYWORDS) or (
                "up or down" in text and not any(kw in text for kw in TERMINAL_NON_1H_KEYWORDS)
            )
        else:
            is_shortterm = any(kw in text for kw in BTC_5MIN_KEYWORDS)
        if not (is_crypto and is_shortterm):
            return False
        if not market_data.has_real_quotes:
            return False
        if not market_data.accepting_orders or market_data.is_resolved:
            return False

        # Must be within convergence window (seconds before expiry)
        end_ts = getattr(market_data, "end_date_ts", None)
        if end_ts is None:
            return False
        now_ts = getattr(market_data, "timestamp", None)
        now_ts = now_ts.timestamp() if hasattr(now_ts, "timestamp") else (now_ts if isinstance(now_ts, (int, float)) else None)
        sec_to_expiry = (end_ts - now_ts) if now_ts else (end_ts - time.time())
        if sec_to_expiry < 0:
            return False  # already expired
        # 1h markets: use 120s window; 5m/15m: use config
        window = 120 if self._1h_only else self.convergence_window_s
        if sec_to_expiry > window:
            return False  # too far from expiry

        # Check cooldown (use replay timestamp when available for backtest)
        now = now_ts if now_ts else time.time()
        last_t = self.last_signal_time.get(market_data.token_id, 0)
        if now - last_t < self.signal_cooldown_s:
            return False

        return True

    def analyze(self, market_data: List[MarketData]) -> List[Signal]:
        """
        Find near-certain outcomes that are underpriced near expiry.

        Logic:
        1. Use Binance BTC price to estimate which outcome is winning
        2. If the winning outcome's market price is below our estimated fair value
           by more than min_edge_cents → buy it
        3. Use time-to-expiry to modulate confidence (closer = more certain)
        """
        signals = []
        self._last_scan_status = "—"

        if not self.binance_feed:
            return signals

        if not self.binance_feed.connected:
            return signals

        n_eligible = 0
        best_prob = 0.0
        best_edge = -999.0
        nearest_expiry = None
        block_reason = "no markets in window"

        for data in market_data:
            if not self.should_trade_market(data):
                if not data.has_real_quotes:
                    block_reason = "missing real quotes"
                elif not data.accepting_orders:
                    block_reason = "market not accepting orders"
                elif data.is_resolved:
                    block_reason = "market resolved"
                continue
            n_eligible += 1
            if data.end_date_ts:
                expiry_s = max(float(data.end_date_ts) - time.time(), 0.0)
                nearest_expiry = expiry_s if nearest_expiry is None else min(nearest_expiry, expiry_s)

            # Position limit
            current_pos = self.positions.get(data.token_id, 0)
            if current_pos >= self.max_position_usd:
                block_reason = "position limit"
                continue

            # Asset-specific feed (BTC, ETH, SOL, XRP)
            asset = self._get_asset_from_market(data)
            binance = self.binance_feed.get_state(asset)
            if not binance.last_price > 0:
                block_reason = "missing spot price"
                continue

            # Parse what the market is asking and extract the strike price
            strike_info = self._parse_strike(data.question, data.outcome)
            if not strike_info:
                block_reason = "unparseable strike"
                continue

            strike_price, direction, outcome_label = strike_info
            price_to_beat = strike_price  # used in signal reason

            # Determine if the current price makes this outcome near-certain
            spot_price = binance.last_price

            if direction == "up_or_down":
                if self._1h_only:
                    # 1h Up/Down: Polymarket resolves via Binance 1h candle.
                    # Price to Beat = candle open. Use Binance REST kline.
                    candle_open = self.binance_feed.get_1h_candle_open(asset) if hasattr(self.binance_feed, "get_1h_candle_open") else None
                    if candle_open is None or candle_open <= 0:
                        block_reason = "missing 1h candle open"
                        continue
                    price_to_beat = candle_open
                    diff_pct = (spot_price - candle_open) / candle_open * 100
                    # Need clear direction: |diff| >= 0.03% (~$30 on $100k BTC)
                    if abs(diff_pct) < 0.03:
                        block_reason = "move below certainty gate"
                        continue
                    if outcome_label.lower() == "up":
                        estimated_prob = 0.98 if diff_pct > 0 else 0.02
                    else:
                        estimated_prob = 0.98 if diff_pct < 0 else 0.02
                    estimated_prob = max(0.02, min(0.98, estimated_prob))
                else:
                    # 5m/15m: use momentum (Chainlink resolution — less accurate with Binance)
                    move_10s = binance.price_change_pct_10s
                    move_30s = binance.price_change_pct_30s
                    move_60s = binance.price_change_pct_60s
                    pressure = binance.bid_pressure  # 0-1, >0.5 = buying
                    price_move = max(abs(move_10s), abs(move_30s), abs(move_60s))
                    if price_move < 0.02:  # need at least 0.02% real move
                        block_reason = "momentum below threshold"
                        continue
                    momentum = (
                        move_60s * 0.40
                        + move_30s * 0.35
                        + move_10s * 0.15
                        + (pressure - 0.5) * 0.10
                    )
                    if outcome_label.lower() == "up":
                        estimated_prob = 0.50 + momentum * 2.5
                    else:
                        estimated_prob = 0.50 - momentum * 2.5
                    estimated_prob = max(0.05, min(0.95, estimated_prob))
            else:
                # Traditional strike-based markets
                estimated_prob = self._estimate_terminal_prob(
                    spot_price, strike_price, direction, binance.volatility_5m
                )

            best_prob = max(best_prob, estimated_prob)
            this_edge = (estimated_prob - data.mid_price) * 100
            best_edge = max(best_edge, this_edge)

            # Only interested in near-certain outcomes
            if estimated_prob < self.min_certainty:
                block_reason = f"certainty {estimated_prob:.2f} < {self.min_certainty:.2f}"
                continue

            # Current market price
            market_price = data.mid_price

            # Edge in cents
            edge_cents = (estimated_prob - market_price) * 100

            if edge_cents < self.min_edge_cents:
                block_reason = f"edge {edge_cents:.1f}c < {self.min_edge_cents:.1f}c"
                continue

            # Account for taker fees (5-min/15-min crypto markets use dynamic fee)
            fee_cents = _crypto_taker_fee_cents(market_price, data.fee_rate_bps, data.fees_enabled)
            net_edge_cents = edge_cents - fee_cents
            if net_edge_cents < 1.0:  # need at least 1¢ net edge
                block_reason = f"net edge {net_edge_cents:.1f}c < 1.0c"
                continue

            # Confidence: higher when spot is further from strike / probability is more extreme
            if direction == "up_or_down":
                # For momentum-based: confidence from how extreme the probability is
                prob_dist = abs(estimated_prob - 0.50)
                confidence = min(0.55 + prob_dist * 1.5, 0.95)
            else:
                spot_dist_pct = abs(spot_price - strike_price) / max(strike_price, 1) * 100
                confidence = min(0.60 + spot_dist_pct * 0.10, 0.95)

            # Size the bet
            bet_size = self._size_bet(estimated_prob, market_price, data.token_id)
            if bet_size <= 0:
                continue

            # Price: aggressive — we want to fill quickly before expiry
            buy_price = min(data.best_ask, estimated_prob - 0.01)
            buy_price = round(max(0.01, min(0.99, buy_price)), 3)
            shares = bet_size / buy_price

            asset_upper = asset.upper()
            ref_label = "open" if (self._1h_only and direction == "up_or_down") else "strike"
            signal = Signal(
                signal_type=SignalType.BUY,
                token_id=data.token_id,
                market_slug=data.market_slug,
                side=data.outcome,
                price=buy_price,
                size=round(shares, 2),
                confidence=round(confidence, 3),
                reason=(
                    f"Terminal convergence: {asset_upper}=${spot_price:,.0f} vs {ref_label}=${price_to_beat:,.0f} "
                    f"({direction}) | edge={edge_cents:.1f}¢ | est={estimated_prob:.3f} mkt={market_price:.3f}"
                ),
                metadata={
                    "asset": asset,
                    "spot_price": spot_price,
                    "strike_price": strike_price,
                    "price_to_beat": price_to_beat,
                    "direction": direction,
                    "estimated_prob": estimated_prob,
                    "market_prob": market_price,
                    "edge_cents": round(edge_cents, 2),
                    "net_edge_cents": round(net_edge_cents, 2),
                    "strategy": "terminal_convergence",
                    "end_date_ts": data.end_date_ts,
                    "fee_rate_bps": int(data.fee_rate_bps or 0) if data.fee_rate_bps is not None else None,
                },
            )

            signals.append(signal)

        # Keep only the top 3 signals by edge (avoid order flood)
        max_signals_per_cycle = 3
        if len(signals) > max_signals_per_cycle:
            signals.sort(key=lambda s: s.metadata.get("edge_cents", 0), reverse=True)
            signals = signals[:max_signals_per_cycle]

        # Set cooldowns only for signals we actually emit
        for sig in signals:
            self.signals_generated += 1
            self.last_signal_time[sig.token_id] = time.time()
            cprint(f"  🏁 {sig}", "green")
        self._last_best_edge_cents = best_edge if best_edge > -999 else 0.0
        self._last_nearest_expiry_s = nearest_expiry or 0.0
        self._last_fill_mode = "GTD"

        # Status for TUI (always visible)
        mode = "1h-only" if self._1h_only else "5m/15m/1h/4h"
        if signals:
            self._last_scan_status = (
                f"{len(signals)} live | edge {self._last_best_edge_cents:.1f}c | exp {self._last_nearest_expiry_s:.0f}s"
            )
        elif n_eligible > 0:
            self._last_scan_status = f"{n_eligible} in window | {block_reason}"
        else:
            self._last_scan_status = f"0 ({mode})"

        # Diagnostic — throttled; log when eligible but no signals, or periodically when 0 eligible
        now = time.time()
        last_diag = getattr(self, '_last_diag_log', 0)
        if now - last_diag >= 45:  # log at most every 45s
            self._last_diag_log = now
            if not signals and n_eligible > 0:
                edge_str = f"{best_edge:+.1f}¢" if best_edge > -999 else "n/a"
                cprint(
                    f"  📊 terminal_conv [{mode}]: {n_eligible} eligible | "
                    f"best_prob={best_prob:.3f} (need ≥{self.min_certainty}) | "
                    f"best_edge={edge_str} (need ≥{self.min_edge_cents}¢)",
                    "dark_grey",
                )
            elif not signals and n_eligible == 0 and self._1h_only:
                cprint(
                    f"  📊 terminal_conv [1h-only]: 0 markets in 120s window | waiting for next 1h expiry",
                    "dark_grey",
                )

        return signals

    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        """Execute terminal convergence signals — aggressive limit orders."""
        results = []

        for signal in signals:
            if signal.signal_type != SignalType.BUY:
                continue

            try:
                gtd_exp = clob_gtd_expiration_unix(
                    signal.metadata.get("end_date_ts"),
                    max_horizon_sec=90.0,
                    before_resolution_sec=5.0,
                )
                order_result = order_manager.place_limit_order(
                    token_id=signal.token_id,
                    side="BUY",
                    price=signal.price,
                    size=signal.size,
                    order_type="GTD" if gtd_exp is not None else "GTC",
                    expiration=gtd_exp,
                    market_slug=signal.market_slug,
                    fee_rate_bps=signal.metadata.get("fee_rate_bps"),
                    metadata={"strategy": self.name, **signal.metadata},
                )

                if order_result.get("success"):
                    cprint(f"  ✅ Terminal order placed: {order_result.get('order_id')}", "green")
                else:
                    cprint(f"  ❌ Terminal order failed: {order_result.get('error')}", "red")

                results.append(order_result)

            except Exception as e:
                cprint(f"  ❌ Terminal execute error: {e}", "red")
                results.append({"success": False, "error": str(e)})

        return results

    def on_order_filled(self, order_id: str, fill_data: Dict):
        """Track filled positions."""
        super().on_order_filled(order_id, fill_data)
        token_id = fill_data.get("token_id", "")
        size_usd = fill_data.get("size", 0) * fill_data.get("price", 0)
        self.positions[token_id] = self.positions.get(token_id, 0) + size_usd

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_asset_from_market(market_data: MarketData) -> str:
        """Infer asset (btc, eth, sol, xrp) from market question/slug."""
        text = f"{market_data.question} {market_data.market_slug}".lower()
        if "ethereum" in text or " eth " in text or "eth" in text.split():
            return "eth"
        if "solana" in text or " sol " in text or "sol" in text.split():
            return "sol"
        if "xrp" in text:
            return "xrp"
        return "btc"

    @staticmethod
    def _parse_strike(question: str, outcome: str = "Yes"):
        """
        Parse a 5-min BTC market question to extract the strike price and direction.

        Handles two formats:
        1. "Will Bitcoin go above $97,500?" → (97500, "above")
        2. "Bitcoin Up or Down - Feb 15, 11:05AM-11:10AM ET" → (0, "up_or_down")

        For format 2, strike=0 is a sentinel; the caller uses Binance VWAP instead.

        Returns:
            (strike_price, direction, outcome) or None if unparseable.
        """
        import re

        q = question.lower()

        # Handle "Bitcoin Up or Down" format — no explicit strike
        if "up or down" in q:
            return 0, "up_or_down", outcome

        # Look for dollar amounts with optional commas
        price_pattern = r'\$[\d,]+(?:\.\d+)?'
        prices = re.findall(price_pattern, question)

        if not prices:
            return None

        # Take the first price found as the strike
        strike_str = prices[0].replace("$", "").replace(",", "")
        try:
            strike_price = float(strike_str)
        except ValueError:
            return None

        # Determine direction
        if any(kw in q for kw in ["above", "over", "higher than", "go up", "rise above"]):
            direction = "above"
        elif any(kw in q for kw in ["below", "under", "lower than", "go down", "fall below", "drop below"]):
            direction = "below"
        else:
            direction = "above"

        return strike_price, direction, outcome

    @staticmethod
    def _estimate_terminal_prob(
        btc_price: float,
        strike_price: float,
        direction: str,
        volatility: float,
    ) -> float:
        """
        Estimate probability of outcome resolving YES given current BTC price.

        Uses distance from strike relative to recent volatility.
        In the terminal phase, if BTC is well past the strike, probability → 1.

        Simple model (can be upgraded to Black-Scholes for more precision):
            - Distance = |BTC - strike| / strike as %
            - If distance > 2x volatility_per_5min → near certain
            - Linear interpolation otherwise
        """
        if strike_price <= 0:
            return 0.5

        distance_pct = (btc_price - strike_price) / strike_price * 100

        # 5-min volatility from annualized: σ_5min ≈ σ_annual / sqrt(105120)
        # 105120 = number of 5-min periods per year
        vol_5min = volatility / (105120 ** 0.5) if volatility > 0 else 0.01
        vol_5min = max(vol_5min, 0.005)  # floor at 0.5 basis points

        if direction == "above":
            # BTC above strike → YES more likely
            if distance_pct > 0:
                # How many vol units above strike?
                z_score = distance_pct / (vol_5min * 100) if vol_5min > 0 else 10
                # Map z-score to probability (sigmoid-like)
                prob = min(0.50 + z_score * 0.15, 0.99)
            else:
                # Below strike
                z_score = abs(distance_pct) / (vol_5min * 100) if vol_5min > 0 else 10
                prob = max(0.50 - z_score * 0.15, 0.01)
        elif direction == "below":
            # BTC below strike → YES more likely
            if distance_pct < 0:
                z_score = abs(distance_pct) / (vol_5min * 100) if vol_5min > 0 else 10
                prob = min(0.50 + z_score * 0.15, 0.99)
            else:
                z_score = distance_pct / (vol_5min * 100) if vol_5min > 0 else 10
                prob = max(0.50 - z_score * 0.15, 0.01)
        else:
            prob = 0.5

        return round(prob, 4)

    def _size_bet(self, estimated_prob: float, market_prob: float, token_id: str = "") -> float:
        """Size using inventory-aware Kelly with fallback to fixed."""
        try:
            from ..sizing.kelly import kelly_size
            from ..config import PAPER_BALANCE_USD, PAPER_TRADING

            bankroll = PAPER_BALANCE_USD if PAPER_TRADING else 1000.0
            pos_usd = self.positions.get(token_id, 0.0)
            inv_q = pos_usd / market_prob if market_prob > 0 else 0.0
            result = kelly_size(
                estimated_prob=estimated_prob,
                market_price=market_prob,
                bankroll=bankroll,
                max_bet_usd=self.order_size_usd * 2,
                inventory_q=inv_q,
            )
            if result.bet_size_usd > 0:
                return result.bet_size_usd
        except Exception:
            pass

        return self.order_size_usd

    def get_state(self) -> Dict[str, Any]:
        state = super().get_state()
        state.update({
            "positions": self.positions,
            "convergence_window_s": self.convergence_window_s,
            "min_edge_cents": self.min_edge_cents,
            "min_certainty": self.min_certainty,
            "best_edge_cents": self._last_best_edge_cents,
            "nearest_expiry_s": self._last_nearest_expiry_s,
            "fill_mode": self._last_fill_mode,
            "status": getattr(self, "_last_scan_status", "—"),
        })
        return state

