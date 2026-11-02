"""
Dedicated replay harness for the ML directional strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from ..backtest.mappers import snapshots_to_market_data_list
from ..backtest.replay_feed import ReplayBinanceFeed, _parse_time
from ..backtest.store import BacktestStore
from ..config import BACKTEST_DB
from .evaluate import compute_max_drawdown
from .features import DEFAULT_RUNTIME_FEATURE_COLUMNS, FeatureBuilder, align_feature_row
from .model import LightGBMBinaryClassifier


from ..fees import category_fee_rate, fee_per_share


@dataclass
class ExecutionFrictionModel:
    """Simplified maker-first friction model."""

    adverse_selection: float = 0.01
    spread_cost: float = 0.005
    missed_fill_penalty: float = 0.005
    calibration_buffer: float = 0.01

    def threshold(self, market_price: float, execution_mode: str = "maker") -> float:
        taker_fee_rate = fee_per_share(market_price, category_fee_rate("crypto"))
        fee_cost = taker_fee_rate if execution_mode == "taker" else 0.0
        return fee_cost + self.adverse_selection + self.spread_cost + self.missed_fill_penalty + self.calibration_buffer


@dataclass
class BacktestTrade:
    """One simulated trade from the ML backtest."""

    market_id: str
    token_id: str
    outcome: str
    probability: float
    market_price: float
    edge: float
    fill_fraction: float
    pnl: float
    won: bool
    timestamp: float


@dataclass
class MLBacktestResult:
    """Aggregate result for the dedicated ML backtest harness."""

    trades: List[BacktestTrade] = field(default_factory=list)
    total_pnl: float = 0.0
    trade_count: int = 0
    win_rate: float = 0.0
    avg_edge: float = 0.0
    avg_fill_fraction: float = 0.0
    net_ev_per_trade: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0


class MLBacktestEngine:
    """
    Research-only replay engine with fee and fill modeling for `ml_directional`.
    """

    def __init__(
        self,
        *,
        store: Optional[BacktestStore] = None,
        feature_builder: Optional[FeatureBuilder] = None,
        friction_model: Optional[ExecutionFrictionModel] = None,
    ) -> None:
        self.store = store or BacktestStore(BACKTEST_DB)
        self.feature_builder = feature_builder or FeatureBuilder()
        self.friction_model = friction_model or ExecutionFrictionModel()

    def run(
        self,
        *,
        model_path: str,
        market_type: Optional[str] = None,
        coin: str = "btc",
        limit: Optional[int] = None,
    ) -> MLBacktestResult:
        model = LightGBMBinaryClassifier.load(model_path)
        feature_columns = (
            model.artifact.feature_columns
            if model.artifact and model.artifact.feature_columns
            else DEFAULT_RUNTIME_FEATURE_COLUMNS
        )
        threshold_probability = (
            model.artifact.threshold_probability
            if model.artifact
            else 0.53
        )

        markets = self.store.load_markets(market_type=market_type, coin=coin, limit=limit)
        result = MLBacktestResult()

        for market in markets:
            winner = market.get("winner")
            if not winner:
                continue

            snapshots = self.store.load_snapshots(str(market.get("market_id", "")))
            if not snapshots:
                continue

            replay_feed = ReplayBinanceFeed(snapshots)
            traded_tokens: set[str] = set()

            for idx, snapshot in enumerate(snapshots):
                replay_feed.set_current_idx(idx)
                market_data_list = snapshots_to_market_data_list(snapshot, market)
                for market_data in market_data_list:
                    if market_data.token_id in traded_tokens:
                        continue

                    row = self.feature_builder.build_runtime_feature_row(
                        market_data=market_data,
                        binance_state=replay_feed.get_state(),
                        now_ts=_parse_time(snapshot.get("time", 0)),
                    )
                    frame = align_feature_row(row, feature_columns)
                    probability_up = float(model.predict_positive_proba(frame).iloc[0])
                    probability = probability_up
                    if (market_data.outcome or "").lower() == "down":
                        probability = 1.0 - probability_up
                    edge = probability - market_data.mid_price
                    threshold = self.friction_model.threshold(market_data.mid_price, execution_mode="maker")

                    should_buy = probability >= threshold_probability and edge > threshold
                    if not should_buy:
                        continue

                    fill_fraction = max(0.10, min(0.95, 0.35 + max(abs(edge) - threshold, 0.0) * 8.0))
                    won = market_data.outcome.lower() == str(winner).lower()
                    pnl = fill_fraction * ((1.0 - market_data.mid_price) if won else (-market_data.mid_price))

                    result.trades.append(
                        BacktestTrade(
                            market_id=str(market.get("market_id", "")),
                            token_id=market_data.token_id,
                            outcome=market_data.outcome,
                            probability=probability,
                            market_price=market_data.mid_price,
                            edge=edge,
                            fill_fraction=fill_fraction,
                            pnl=pnl,
                            won=won,
                            timestamp=_parse_time(snapshot.get("time", 0)),
                        )
                    )
                    traded_tokens.add(market_data.token_id)

        result.trade_count = len(result.trades)
        if not result.trades:
            return result

        pnl_values = [trade.pnl for trade in result.trades]
        gross_profit = sum(value for value in pnl_values if value > 0)
        gross_loss = abs(sum(value for value in pnl_values if value < 0))

        result.total_pnl = sum(pnl_values)
        result.win_rate = sum(1 for trade in result.trades if trade.won) / len(result.trades)
        result.avg_edge = sum(trade.edge for trade in result.trades) / len(result.trades)
        result.avg_fill_fraction = sum(trade.fill_fraction for trade in result.trades) / len(result.trades)
        result.net_ev_per_trade = result.total_pnl / len(result.trades)
        result.profit_factor = (gross_profit / gross_loss) if gross_loss else float(gross_profit > 0)
        result.max_drawdown = compute_max_drawdown(pnl_values)
        return result
