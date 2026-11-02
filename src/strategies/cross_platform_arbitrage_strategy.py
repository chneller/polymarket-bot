"""Cross-platform arbitrage strategy (Polymarket + Kalshi)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Optional

from ..logging_utils import cprint
from ..fees import estimate_taker_fee
from ..config import (
    ORDER_SIZE_USD,
    TRADING_FEE_RATE,
    KALSHI_MARKET_MAP_PATH,
    KALSHI_MIN_PROFIT_CENTS,
    KALSHI_TRADING_ENABLED,
    PAPER_TRADING,
)
from ..kalshi_client import KalshiClient
from .base_strategy import BaseStrategy, Signal, SignalType, MarketData


@dataclass
class KalshiMapping:
    polymarket_slug: str
    kalshi_ticker: str


class CrossPlatformArbitrageStrategy(BaseStrategy):
    """Arbitrage between Polymarket and Kalshi for mapped markets."""

    name = "cross_platform_arbitrage"
    description = "Cross-platform arbitrage between Polymarket and Kalshi"
    version = "0.1.0"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.order_size_usd = self.config.get("order_size_usd", ORDER_SIZE_USD)
        self.min_profit_cents = self.config.get("min_profit_cents", KALSHI_MIN_PROFIT_CENTS)
        self.kalshi = KalshiClient()
        self.market_map = self._load_market_map()

    def _load_market_map(self) -> Dict[str, KalshiMapping]:
        path = Path(self.config.get("market_map_path", KALSHI_MARKET_MAP_PATH))
        if not path.exists():
            cprint(f"⚠️  Kalshi map not found at {path}. No cross-platform markets loaded.", "yellow")
            return {}

        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            cprint(f"❌ Failed to parse Kalshi map {path}: {exc}", "red")
            return {}

        mappings: Dict[str, KalshiMapping] = {}
        for item in data.get("mappings", []):
            slug = item.get("polymarket_slug")
            ticker = item.get("kalshi_ticker")
            if slug and ticker:
                mappings[slug] = KalshiMapping(polymarket_slug=slug, kalshi_ticker=ticker)
        return mappings

    def analyze(self, market_data: List[MarketData]) -> List[Signal]:
        signals: List[Signal] = []
        if not self.market_map:
            return signals

        min_profit = self.min_profit_cents / 100

        for data in market_data:
            mapping = self.market_map.get(data.market_slug)
            if not mapping:
                continue

            kalshi_prices = self.kalshi.get_best_prices(mapping.kalshi_ticker)
            if not kalshi_prices:
                continue

            pm_yes_ask = data.best_ask
            kalshi_no_ask = kalshi_prices.no_ask
            if pm_yes_ask <= 0 or kalshi_no_ask is None:
                continue

            combined_cost = pm_yes_ask + kalshi_no_ask
            # Polymarket taker fee on the PM leg; Kalshi leg approximated with the same curve.
            edge = 1 - combined_cost - estimate_taker_fee(1.0, pm_yes_ask) - estimate_taker_fee(1.0, kalshi_no_ask)

            if edge <= 0 or (edge * 100) < self.min_profit_cents:
                continue

            if not KALSHI_TRADING_ENABLED:
                cprint(
                    f"⚠️  Kalshi arb found but trading disabled: {data.market_slug} ({edge*100:.2f}¢)",
                    "yellow",
                )
                continue

            size_shares = self.order_size_usd / pm_yes_ask
            signals.append(
                Signal(
                    signal_type=SignalType.BUY,
                    token_id=data.token_id,
                    market_slug=data.market_slug,
                    side="YES",
                    price=round(pm_yes_ask, 3),
                    size=round(size_shares, 2),
                    confidence=min(edge * 10, 1.0),
                    reason=(
                        f"Cross-platform arb: PM YES ask {pm_yes_ask:.3f} + "
                        f"Kalshi NO ask {kalshi_no_ask:.3f} = {combined_cost:.3f}"
                    ),
                    metadata={
                        "kalshi_ticker": mapping.kalshi_ticker,
                        "kalshi_no_ask": kalshi_no_ask,
                        "combined_cost": combined_cost,
                        "edge": edge,
                    },
                )
            )

        return signals

    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        results: List[Dict] = []
        for signal in signals:
            if signal.signal_type != SignalType.BUY:
                continue

            kalshi_ticker = signal.metadata.get("kalshi_ticker")
            if not kalshi_ticker:
                results.append({"success": False, "error": "Missing Kalshi ticker"})
                continue

            if not KALSHI_TRADING_ENABLED:
                results.append({"success": False, "error": "Kalshi trading disabled"})
                continue

            kalshi_no_ask = signal.metadata.get("kalshi_no_ask")
            if kalshi_no_ask is None:
                results.append({"success": False, "error": "Missing Kalshi price"})
                continue

            if PAPER_TRADING:
                # Paper trading: place Polymarket leg and log Kalshi leg intent.
                order_result = order_manager.place_limit_order(
                    token_id=signal.token_id,
                    side="BUY",
                    price=signal.price,
                    size=signal.size,
                    order_type="GTC",
                    market_slug=signal.market_slug,
                )
                results.append(order_result)
                if order_result.get("success"):
                    cprint(
                        f"📝 [PAPER] Kalshi NO leg for {kalshi_ticker} @ {kalshi_no_ask:.3f}",
                        "yellow",
                    )
                continue

            # Live trading: place Polymarket leg first, then Kalshi hedge.
            order_result = order_manager.place_limit_order(
                token_id=signal.token_id,
                side="BUY",
                price=signal.price,
                size=signal.size,
                order_type="GTC",
                market_slug=signal.market_slug,
            )
            results.append(order_result)
            if not order_result.get("success"):
                continue

            kalshi_count = max(1, int(round(signal.size)))
            client_order_id = f"pm_{order_result.get('order_id', 'unknown')}"
            try:
                kalshi_result = self.kalshi.create_order(
                    ticker=kalshi_ticker,
                    side="no",
                    action="buy",
                    count=kalshi_count,
                    price=float(kalshi_no_ask),
                    client_order_id=client_order_id,
                )
                results.append({"success": True, "kalshi_order": kalshi_result})
            except Exception as exc:
                cprint(f"❌ Kalshi order failed for {kalshi_ticker}: {exc}", "red")
                results.append({"success": False, "error": str(exc)})

        return results
