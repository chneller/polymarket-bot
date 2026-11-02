"""
Polymarket Trading Bot - Main Orchestrator

Ties all components together:
- Client connection
- WebSocket feed for real-time data
- Strategy execution
- Order management
- Risk management
"""

import sys
import re
import time
import signal
import threading
from collections import deque
from dataclasses import replace
from typing import Dict, List, Optional, Tuple, Type
from datetime import datetime

from .logging_utils import cprint, get_session_log_path, set_dashboard_mode
from .dashboard import (
    Dashboard, DashboardState, BinanceSnapshot,
    StrategyRow, PortfolioSnapshot, ExecutionHealthSnapshot,
    MarketQualitySnapshot, OpenOrderRow, OpenPositionRow, RecentFillRow, StrategyDetailRow, MLDecisionRow, log as dash_log,
)
from .config import (
    SCAN_INTERVAL_SECONDS,
    BALANCE_REFRESH_SECONDS,
    MARKET_REFRESH_SECONDS,
    POSITIONS_SYNC_SECONDS,
    MAX_HOURS_TO_EXPIRY,
    SHORTTERM_NEAREST_CYCLE_ONLY,
    SHORTTERM_MAX_HOURS_AHEAD,
    ENABLE_WEBSOCKET_FEED,
    ENABLE_USER_WEBSOCKET_FEED,
    ENABLE_CRYPTO_EVENT_INFRA,
    TERMINAL_CONVERGENCE_1H_ONLY,
    PAPER_TRADING,
    CRYPTO_MARKET_KEYWORDS,
    ONLY_CRYPTO_MARKETS,
    BTC_5MIN_KEYWORDS,
    DISABLED_STRATEGIES,
    ADAPTIVE_RISK_ENABLED,
    ADAPTIVE_MAX_POSITION_PCT,
    ADAPTIVE_MAX_EXPOSURE_PCT,
    ADAPTIVE_MAX_SINGLE_TRADE_PCT,
    ADAPTIVE_DAILY_LOSS_LIMIT_PCT,
    ADAPTIVE_DRAWDOWN_THROTTLE_PCT,
    ADAPTIVE_DRAWDOWN_HALT_PCT,
    ADAPTIVE_MIN_BALANCE_FLOOR_PCT,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_ALERTS_ENABLED,
    MIN_VOLUME_USD,
    TRADING_FEE_RATE,
)
from .fees import estimate_taker_fee, market_fee_params
from .config import (
    BALANCE_STALE_BLOCK_BUYS,
    BALANCE_STALE_MAX_SECONDS,
    POLYMARKET_MIN_ORDER_SIZE,
    ML_DIRECTIONAL_MIN_SECONDS_TO_EXPIRY_15M,
    ML_DIRECTIONAL_MIN_SECONDS_TO_EXPIRY_1H,
    print_config,
    validate_config,
    ENABLE_STRATEGY_ANALYTICS,
    ML_DIRECTIONAL_LEAN_MODE,
    ML_DIRECTIONAL_LEAN_ASSETS,
    ML_DIRECTIONAL_LEAN_HORIZONS,
    ML_DIRECTIONAL_LEAN_EVENTS_ONLY,
    ML_DIRECTIONAL_LEAN_BINANCE_SYMBOLS,
)
from .client import PolymarketClient
from .websocket_feed import WebSocketFeed, UserWebSocketFeed
from .feeds.binance_ws import BinanceFeed
from .order_manager import OrderManager, OrderStatus
from .risk_manager import RiskManager, RiskLevel
from .persistence import SqliteStore
from .analytics.strategy_tracker import StrategyTracker
from .alerts.telegram import TelegramAlerter
from .strategies import get_strategy, list_strategies, BaseStrategy
from .strategies.base_strategy import MarketData, SignalType


def _market_text_payload(market: Dict) -> str:
    parts = [
        market.get("question") or market.get("title") or "",
        market.get("slug") or market.get("market_slug") or "",
        market.get("event_title") or "",
        market.get("event_slug") or "",
    ]
    return " ".join(str(part).lower() for part in parts if part)


def _looks_like_hourly_updown_window(text: str) -> bool:
    lowered = text.lower()
    if "updown" not in lowered and "up or down" not in lowered:
        return False
    if any(token in lowered for token in ("5m", "15m", "4h", "5 min", "15 min", "4 hour", "4-hour")):
        return False
    if bool(
        re.search(
            r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*-\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
            lowered,
        )
    ):
        return True
    if bool(re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\s*et\b", lowered)):
        return True
    return bool(re.search(r"-\d{1,2}(?:am|pm)-et\b", lowered))


def _shortterm_duration_bucket(text: str) -> Optional[str]:
    lowered = text.lower()
    patterns = {
        "5m": [
            r"\b5m\b",
            r"\b5\s*min(?:ute)?s?\b",
            r"\b5-minute\b",
            r"updown-5m",
            r"up-or-down-5m",
        ],
        "15m": [
            r"\b15m\b",
            r"\b15\s*min(?:ute)?s?\b",
            r"\b15-minute\b",
            r"updown-15m",
            r"up-or-down-15m",
        ],
        "1h": [
            r"\b1h\b",
            r"\b1\s*hr\b",
            r"\b1\s*hour\b",
            r"\b60\s*min(?:ute)?s?\b",
            r"updown-1h",
            r"up-or-down-1h",
            r"up or down - 1 hour",
            r"up or down - 1h",
        ],
        "4h": [
            r"\b4h\b",
            r"\b4\s*hr\b",
            r"\b4\s*hour[s]?\b",
            r"\b240\s*min(?:ute)?s?\b",
            r"updown-4h",
            r"up-or-down-4h",
        ],
    }
    for duration, duration_patterns in patterns.items():
        if any(re.search(pattern, lowered) for pattern in duration_patterns):
            return duration
    if _looks_like_hourly_updown_window(lowered):
        return "1h"
    return None


def _shortterm_bucket_key(text: str) -> Optional[str]:
    asset = None
    lowered = text.lower()
    if "bitcoin" in lowered or re.search(r"\bbtc\b", lowered):
        asset = "btc"
    elif "ethereum" in lowered or re.search(r"\beth\b", lowered):
        asset = "eth"
    elif "solana" in lowered or re.search(r"\bsol\b", lowered):
        asset = "sol"
    elif re.search(r"\bxrp\b", lowered):
        asset = "xrp"
    if not asset:
        return None
    duration = _shortterm_duration_bucket(lowered)
    if not duration:
        return None
    return f"{asset}:{duration}"


def _parse_market_end_ts(market: Dict) -> Optional[float]:
    shortterm_text = _market_text_payload(market)
    if _shortterm_bucket_key(shortterm_text):
        for slug_key in ("slug", "market_slug", "event_slug"):
            slug = str(market.get(slug_key) or "")
            parts = slug.rsplit("-", 1)
            if len(parts) == 2 and parts[1].isdigit():
                bucket = _shortterm_bucket_key(f"{shortterm_text} {slug}")
                horizon = _extract_horizon_from_bucket(bucket)
                # Slug trailing int is the window *start* (e.g. btc-updown-15m-<unix>).
                # Nearest-cycle filtering compares this to _current_cycle_end_ts(); using
                # start as "end" makes the *next* window look like it ends at the same
                # instant as the current cycle and slip through as "current".
                duration_seconds = _bucket_duration_seconds(horizon)
                anchor_ts = float(parts[1])
                if duration_seconds:
                    return anchor_ts + duration_seconds
                return anchor_ts

    keys = (
        "endDate",
        "end_date",
        "end_date_iso",
        "endDateIso",
        "endDateISO",
        "closeTime",
        "closedTime",
        "endTime",
        "endTimestamp",
        "end_date_ts",
        "resolutionDate",
        "resolutionTime",
        "resolution_date",
        "gameStartTime",
        "event_end_date",
        "event_end_iso",
        "event_close_time",
        "event_resolution_date",
    )
    for key in keys:
        val = market.get(key)
        if val in (None, ""):
            continue
        if isinstance(val, (int, float)):
            v = float(val)
            if v > 1e12:
                return v / 1000
            if v > 1e9:
                return v
            continue
        if isinstance(val, str):
            raw = val.strip()
            if not raw:
                continue
            if raw.isdigit():
                v = float(raw)
                if v > 1e12:
                    return v / 1000
                if v > 1e9:
                    return v
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
    slug = market.get("slug") or market.get("market_slug") or market.get("event_slug") or ""
    parts = str(slug).rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return float(parts[1])
    return None


def _extract_asset_from_bucket(bucket: Optional[str]) -> Optional[str]:
    if not bucket or ":" not in bucket:
        return None
    return bucket.split(":", 1)[0]


def _extract_horizon_from_bucket(bucket: Optional[str]) -> Optional[str]:
    if not bucket or ":" not in bucket:
        return None
    return bucket.split(":", 1)[1]


def _bucket_duration_seconds(bucket_horizon: Optional[str]) -> Optional[int]:
    mapping = {
        "5m": 5 * 60,
        "15m": 15 * 60,
        "1h": 60 * 60,
        "4h": 4 * 60 * 60,
    }
    return mapping.get(str(bucket_horizon or "").lower())


def _current_cycle_end_ts(now_ts: float, bucket_horizon: Optional[str]) -> Optional[float]:
    duration_seconds = _bucket_duration_seconds(bucket_horizon)
    if not duration_seconds or now_ts <= 0:
        return None
    return ((int(now_ts) // duration_seconds) + 1) * duration_seconds


def _normalize_market_outcome_label(market: Dict, outcome_label: Optional[str], outcome_index: int) -> str:
    label = str(outcome_label or "").strip()
    if not label:
        label = "Yes" if outcome_index == 0 else "No"

    text = _market_text_payload(market)
    if "updown" not in text and "up or down" not in text:
        return label

    lowered = label.lower()
    if lowered == "yes":
        return "UP"
    if lowered == "no":
        return "DOWN"
    if lowered == "up":
        return "UP"
    if lowered == "down":
        return "DOWN"
    return label.upper()


class PolymarketBot:
    """
    Main trading bot orchestrator.
    
    Usage:
        bot = PolymarketBot(strategy="spread")
        bot.start()
        
        # Or run all strategies:
        bot = PolymarketBot(strategy="all")
        bot.start()
    """
    
    def __init__(
        self,
        strategy: str = "btc_5min",
        strategy_config: Optional[Dict] = None
    ):
        """
        Initialize the trading bot.
        
        Args:
            strategy: Strategy name (see strategies/) or "all" for multi-strategy
            strategy_config: Optional strategy configuration overrides
        """
        cprint("\n" + "="*60, "cyan")
        cprint("Polymarket Trading Bot", "cyan", attrs=["bold"])
        cprint("="*60, "cyan")
        
        # Validate configuration
        errors = validate_config()
        if errors:
            for error in errors:
                cprint(f"ERROR: {error}", "red")
            raise ValueError("Configuration errors - check .env file")
        
        # Print config
        print_config()
        
        # Initialize components
        cprint("Initializing components...", "cyan")

        self.client = PolymarketClient()
        self.feed = WebSocketFeed()
        self.user_feed = UserWebSocketFeed(self.client.get_api_credentials, self._user_feed_markets)
        self.store = SqliteStore()
        self.order_manager: Optional[OrderManager] = None
        self.risk_manager = RiskManager(
            store=self.store,
            adaptive_config={
                "enabled": ADAPTIVE_RISK_ENABLED,
                "max_position_pct": ADAPTIVE_MAX_POSITION_PCT,
                "max_exposure_pct": ADAPTIVE_MAX_EXPOSURE_PCT,
                "max_single_trade_pct": ADAPTIVE_MAX_SINGLE_TRADE_PCT,
                "daily_loss_limit_pct": ADAPTIVE_DAILY_LOSS_LIMIT_PCT,
                "drawdown_throttle_pct": ADAPTIVE_DRAWDOWN_THROTTLE_PCT,
                "drawdown_halt_pct": ADAPTIVE_DRAWDOWN_HALT_PCT,
                "min_balance_floor_pct": ADAPTIVE_MIN_BALANCE_FLOOR_PCT,
            },
        )
        self.analytics_enabled = ENABLE_STRATEGY_ANALYTICS
        self.strategy_tracker = StrategyTracker() if self.analytics_enabled else None
        
        # Telegram alerter
        self.telegram = TelegramAlerter(
            bot_token=TELEGRAM_BOT_TOKEN,
            chat_id=TELEGRAM_CHAT_ID,
            enabled=TELEGRAM_ALERTS_ENABLED,
        )
        
        # Wire risk level changes to Telegram
        self.risk_manager.on_risk_change(self._on_risk_level_change)
        
        # Track previous throttle to avoid spamming alerts
        self._last_alerted_throttle: float = 1.0
        self._trade_pause_log: Dict[str, float] = {}
        
        self.multi_strategy_mode = (strategy == "all")
        self.selected_strategy_name = strategy
        self.strategies: List[BaseStrategy] = []
        self.ml_directional_lean_mode = bool(
            ML_DIRECTIONAL_LEAN_MODE and not self.multi_strategy_mode and strategy in {"ml_directional", "terminal_convergence"}
        )
        self.ml_directional_lean_assets = set(ML_DIRECTIONAL_LEAN_ASSETS or ["btc"])
        self.ml_directional_lean_horizons = (
            {"1h"} if strategy == "terminal_convergence" else set(ML_DIRECTIONAL_LEAN_HORIZONS or ["15m", "1h"])
        )

        # Binance feed for cross-asset strategies
        self.binance_feed: Optional[BinanceFeed] = None
        if ENABLE_CRYPTO_EVENT_INFRA:
            feed_symbols = (
                list(ML_DIRECTIONAL_LEAN_BINANCE_SYMBOLS or ["btcusdt"])
                if self.ml_directional_lean_mode
                else None
            )
            self.binance_feed = BinanceFeed(symbols=feed_symbols)
            cprint("Binance feed initialized", "cyan")
            if self.ml_directional_lean_mode:
                cprint(
                    f"Lean Binance symbols: {', '.join(self.binance_feed.symbols)}",
                    "cyan",
                )

        # Strategy config — inject shared resources into strategies
        merged_config = dict(strategy_config or {})
        merged_config["client"] = self.client
        merged_config["risk_manager"] = self.risk_manager
        merged_config["store"] = self.store
        if self.binance_feed:
            merged_config["binance_feed"] = self.binance_feed
        merged_config["1h_only"] = TERMINAL_CONVERGENCE_1H_ONLY
        
        # BTC 5-min strategies that need Binance feed
        _BTC_5MIN_STRATEGIES = ["cross_asset", "terminal_convergence", "orderbook_imbalance"]
        # ML strategies use Binance feed but target slower horizons, so keep them separate
        _ML_STRATEGIES = ["ml_directional"]
        # Phase 5 advanced strategies (work on all markets)
        _ADVANCED_STRATEGIES = ["vpin", "sentiment", "combinatorial_arb", "wallet_copy"]
        # All original strategies
        _CLASSIC_STRATEGIES = ["spread", "arbitrage", "stink_bid", "favorite_longshot", "late_money"]
        
        # Log disabled strategies
        if DISABLED_STRATEGIES:
            cprint(f"Disabled: {', '.join(DISABLED_STRATEGIES)}", "yellow")
        
        def _load_strategy(name: str) -> Optional[BaseStrategy]:
            """Load a strategy, skipping if disabled."""
            if name in DISABLED_STRATEGIES:
                return None
            return get_strategy(name, **merged_config)
        
        if self.multi_strategy_mode:
            cprint("Loading ALL strategies (multi-strategy mode)", "cyan", attrs=["bold"])
            strat_names = list(_CLASSIC_STRATEGIES)
            if ENABLE_CRYPTO_EVENT_INFRA and self.binance_feed:
                strat_names.extend(_BTC_5MIN_STRATEGIES)
                strat_names.extend(_ML_STRATEGIES)
            strat_names.extend(_ADVANCED_STRATEGIES)
            for name in strat_names:
                strat = _load_strategy(name)
                if strat:
                    self.strategies.append(strat)
                    cprint(f"{name}: {strat.description}", "white")
            self.strategy = self.strategies[0] if self.strategies else None
        elif strategy == "btc_5min":
            # Special mode: run only the 5-min BTC strategies
            cprint("Loading BTC 5-min strategies", "cyan", attrs=["bold"])
            for name in _BTC_5MIN_STRATEGIES:
                strat = _load_strategy(name)
                if strat:
                    self.strategies.append(strat)
                    cprint(f"{name}: {strat.description}", "white")
            # Also load passive/advanced strategies that pair well
            for extra in ["stink_bid", "arbitrage", "vpin", "sentiment", "combinatorial_arb", "wallet_copy"]:
                strat = _load_strategy(extra)
                if strat:
                    self.strategies.append(strat)
                    tag = "(passive)" if extra == "stink_bid" else "(advanced)"
                    cprint(f"{extra}: {strat.description} {tag}", "white")
            self.strategy = self.strategies[0] if self.strategies else None
        else:
            cprint(f"Loading strategy: {strategy}", "cyan")
            self.strategy: BaseStrategy = get_strategy(strategy, **merged_config)
            self.strategies = [self.strategy]
            cprint(f"{self.strategy.description}", "white")
        
        # State
        self.is_running = False
        self.markets: Dict[str, Dict] = {}  # condition_id -> market data
        self.market_data_cache: Dict[str, MarketData] = {}
        self._scan_count = 0
        self._last_balance_refresh_success_ts = 0.0
        self._last_positions_sync_ts = 0.0
        self._stale_shortterm_condition_ids: set[str] = set()
        self._stale_shortterm_market_slugs: set[str] = set()
        self._market_quality_stats: Dict[str, float] = {}
        self._last_fill_event_ts = 0.0
        self._recent_fills = deque(maxlen=12)
        self._latest_market_data: List[MarketData] = []
        self._last_market_refresh_started_at = 0.0
        self._next_lean_market_refresh_at = 0.0
        self._lean_event_cache_key = ""
        self._lean_event_cache_markets: List[Dict] = []
        self._lean_event_cache_scanned_events = 0
        self._lean_event_cache_event_market_count = 0
        self._lean_event_cache_expires_at = 0.0
        self._last_lean_universe_signature = ""
        self._last_lean_diagnostics_log_ts = 0.0
        
        # TUI dashboard
        self.dashboard = Dashboard()
        
        # Threads
        self._main_thread: Optional[threading.Thread] = None
        self._market_fetch_thread: Optional[threading.Thread] = None
        
        # Register signal handlers
        self._shutdown_requested = False
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        
        cprint("Bot initialized!\n", "green")
    
    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully. Keep handler minimal so it returns quickly."""
        if self._shutdown_requested:
            # Second Ctrl+C: force exit immediately
            import os
            os._exit(0)
        self._shutdown_requested = True
        set_dashboard_mode(False)
        self.dashboard.stop()
        cprint("\n\nShutdown signal received... (Ctrl+C again to force quit)", "yellow")
        self.is_running = False
        # Main loop will exit and call stop() to cancel orders, etc.
    
    def start(self):
        """Start the trading bot."""
        if self.is_running:
            cprint("Bot already running", "yellow")
            return
        
        cprint("\n" + "="*60, "green")
        cprint("Starting Polymarket Bot", "green", attrs=["bold"])
        cprint("="*60, "green")
        
        # Connect to Polymarket
        if not self.client.connect():
            cprint("Failed to connect to Polymarket", "red")
            return
        
        # Initialize order manager with connected client
        self.order_manager = OrderManager(self.client, store=self.store)
        self.order_manager.start_cleanup_loop()
        for strat in self.strategies:
            strat.config["order_manager"] = self.order_manager

        # Register order callbacks
        self.order_manager.on_fill(self._on_order_fill)
        self.order_manager.on_cancel(self._on_order_cancel)
        
        # Set up WebSocket callbacks
        self.feed.on_orderbook(self._on_orderbook_update)
        self.feed.on_connect(self._on_feed_connect)
        self.feed.on_disconnect(self._on_feed_disconnect)
        self.feed.on_market_resolved(self._on_market_resolved)
        self.feed.on_tick_size_change(self._on_tick_size_change)
        self.user_feed.on_trade(self._on_user_trade_update)
        self.user_feed.on_order(self._on_user_order_update)
        
        # Fetch initial markets
        cprint("\nFetching markets...", "cyan")
        self._fetch_markets()

        # Refresh balance before starting
        self._refresh_balance(force_log=True)
        
        # WebSocket disabled - using REST polling only (more reliable)
        if ENABLE_WEBSOCKET_FEED:
            self.feed.start(blocking=False)
            cprint("WebSocket feed enabled", "cyan")
        else:
            cprint("Using REST API polling (WebSocket disabled)", "cyan")
        if ENABLE_USER_WEBSOCKET_FEED and not PAPER_TRADING:
            self.user_feed.start()
            cprint("User WebSocket feed enabled", "cyan")
        
        # Start Binance feed for cross-asset strategies
        if self.binance_feed:
            self.binance_feed.start()
        
        # Start strategies
        for strat in self.strategies:
            strat.on_start()
        
        self.is_running = True
        
        # Start main trading loop
        strat_names = [s.name for s in self.strategies]
        cprint("\nBot is running!", "green", attrs=["bold"])
        if self.multi_strategy_mode or len(self.strategies) > 1:
            cprint(f"Strategies: {', '.join(strat_names)}", "cyan", attrs=["bold"])
        elif self.strategy:
            cprint(f"Strategy: {self.strategy.name}", "white")
        cprint(f"Markets: {len(self.markets)} crypto markets", "white")
        cprint(f"Paper Trading: {'ON' if PAPER_TRADING else 'OFF (LIVE)'}", 
               "green" if PAPER_TRADING else "red")
        cprint(f"Scan Interval: {SCAN_INTERVAL_SECONDS}s", "white")
        cprint(f"Mode: {'WebSocket + REST' if ENABLE_WEBSOCKET_FEED else 'REST API polling'}", "white")
        if self.binance_feed:
            syms = getattr(self.binance_feed, "symbols", ["btcusdt"])
            sym_str = ",".join(s.upper() for s in syms)
            cprint(f"Binance Feed: {sym_str}", "cyan")
        session_log_path = get_session_log_path()
        if session_log_path:
            cprint(f"Session Log: {session_log_path}", "cyan")
        if self.risk_manager.adaptive_enabled:
            cprint("Adaptive Risk: bankroll-proportional", "cyan")
        cprint("\nPress Ctrl+C to stop\n", "yellow")
        
        # Start TUI dashboard — takes over the terminal
        time.sleep(0.5)  # let final startup messages flush
        self.dashboard.start()
        set_dashboard_mode(True)
        dash_log("Bot started — dashboard active")
        
        # Telegram startup alert
        balance = self.risk_manager.current_balance
        self.telegram.alert_startup(
            balance=balance,
            strategies=strat_names,
            mode="PAPER" if PAPER_TRADING else "LIVE",
        )
        
        # Run main loop
        self._main_loop()
    
    def stop(self):
        """Stop the trading bot gracefully."""
        if not self.is_running:
            return
        
        # Restore normal terminal before printing shutdown info
        set_dashboard_mode(False)
        self.dashboard.stop()
        
        cprint("\nStopping bot...", "yellow")
        self.is_running = False
        
        # Stop all strategies
        for strat in self.strategies:
            try:
                strat.on_stop()
            except Exception:
                pass
        
        # Cancel all open orders on shutdown (safety)
        if self.order_manager:
            active = self.order_manager.get_active_orders()
            if active:
                cprint(f"Cancelling {len(active)} active orders...", "yellow")
            cancelled = self.order_manager.cancel_all_orders("Bot shutdown")
            if cancelled:
                cprint(f"Cancelled {cancelled} orders", "yellow")
            self.order_manager.stop_cleanup_loop()
        
        # Stop Binance feed
        if self.binance_feed:
            try:
                self.binance_feed.stop()
            except Exception:
                pass
        
        # Stop WebSocket feed (if it was running)
        try:
            self.feed.stop()
        except Exception:
            pass
        try:
            self.user_feed.stop()
        except Exception:
            pass

        # Stop client background tasks (e.g. CLOB heartbeat loop)
        try:
            self.client.stop_background_tasks()
        except Exception:
            pass
        
        # Print final stats
        self._print_final_stats()
        if self.analytics_enabled and self.strategy_tracker:
            self.strategy_tracker.print_scorecard()
        
        # Telegram shutdown alert (includes session summary)
        self.telegram.alert_shutdown("User requested")
        self.telegram.stop()
        
        cprint("Bot stopped\n", "green")
    
    def _main_loop(self):
        """Main trading loop."""
        last_scan = 0
        last_fill_check = 0
        last_balance_check = 0
        last_market_refresh = 0
        last_sync = 0
        fill_check_interval = 30 if (ENABLE_USER_WEBSOCKET_FEED and not PAPER_TRADING) else 10
        sync_interval = POSITIONS_SYNC_SECONDS  # Sync order state + positions (catches missed fills, fixes drifted exposure)
        self._recent_trades_cache: List[Dict] = []  # shared with VPIN
        self._first_scan_done = False
        
        while self.is_running:
            try:
                now = time.time()
                
                # Check if it's time to scan
                if now - last_scan >= SCAN_INTERVAL_SECONDS:
                    t0 = time.time()
                    self._scan_and_trade()
                    dt = time.time() - t0
                    if dt > 5:
                        cprint(f"scan_and_trade took {dt:.1f}s (slow!)", "yellow")
                    last_scan = time.time()  # use actual time, not stale `now`
                
                # Check for fills periodically
                if time.time() - last_fill_check >= fill_check_interval:
                    t0 = time.time()
                    self._check_for_fills()
                    self._cancel_out_of_cycle_shortterm_orders()
                    dt = time.time() - t0
                    if dt > 5:
                        cprint(f"fill_check took {dt:.1f}s (slow!)", "yellow")
                    last_fill_check = time.time()

                # Refresh balance periodically
                if time.time() - last_balance_check >= BALANCE_REFRESH_SECONDS:
                    t0 = time.time()
                    self._refresh_balance()
                    dt = time.time() - t0
                    if dt > 5:
                        cprint(f"balance_refresh took {dt:.1f}s (slow!)", "yellow")
                    last_balance_check = time.time()

                # Refresh market universe periodically
                if time.time() - last_market_refresh >= MARKET_REFRESH_SECONDS:
                    t0 = time.time()
                    should_refresh = True
                    if self.ml_directional_lean_mode and self._next_lean_market_refresh_at > time.time():
                        should_refresh = False
                    if should_refresh:
                        self._fetch_markets(is_refresh=True)
                    dt = time.time() - t0
                    if should_refresh and dt > 5:
                        cprint(f"market_refresh took {dt:.1f}s (slow!)", "yellow")
                    last_market_refresh = time.time()

                # Sync order state + positions from exchange/API (catches missed fills)
                if time.time() - last_sync >= sync_interval and not PAPER_TRADING:
                    try:
                        if self.order_manager.orders:
                            self.order_manager.sync_with_exchange()
                        # Sync exposure from Data API positions (ground truth, not fill-derived)
                        positions = self.client.get_positions(limit=150)
                        if positions is not None:
                            self.risk_manager.sync_positions_from_api(positions)
                        else:
                            cprint("positions sync skipped: API fetch failed", "yellow")
                        last_sync = time.time()
                        self._last_positions_sync_ts = time.time()
                    except Exception as e:
                        cprint(f"sync error: {e}", "yellow")
                
                # Small sleep to prevent CPU spinning
                time.sleep(0.1)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                cprint(f"Main loop error: {e}", "red")
                time.sleep(1)
        
        self.stop()
    
    def _scan_and_trade(self):
        """Scan markets and execute strategy (or all strategies in multi-mode)."""
        from .logging_utils import _DASHBOARD_MODE
        
        self._scan_count += 1
        
        # Check if trading is allowed
        can_trade, reason = self.risk_manager.can_trade()
        if not can_trade:
            now_ts = time.time()
            throttle_seconds = 300 if any(
                token in reason.lower() for token in ("entry window", "entry cap")
            ) else 30
            last_logged = self._trade_pause_log.get(reason, 0.0)
            if now_ts - last_logged >= throttle_seconds:
                self._trade_pause_log[reason] = now_ts
                cprint(f"Trading paused: {reason}", "yellow")
            self._push_dashboard_state()
            return
        
        # Build market data for strategy
        market_data_list = self._build_market_data()
        
        if not market_data_list:
            if not _DASHBOARD_MODE:
                cprint(f"Scanning {len(self.markets)} markets... (no price data yet)", "white")
            self._push_dashboard_state()
            return
        
        # Binance state (logged to panel, not spammed to log)
        if not _DASHBOARD_MODE:
            cprint(f"\nAnalyzed {len(market_data_list)} tokens @ {datetime.now().strftime('%H:%M:%S')}", "cyan")
            if hasattr(self, 'binance_feed') and self.binance_feed:
                bs = self.binance_feed.get_state()
                if bs.connected and bs.last_price > 0:
                    cprint(
                        f"BTC ${bs.last_price:,.0f} | "
                        f"10s={bs.price_change_pct_10s:+.4f}% "
                        f"30s={bs.price_change_pct_30s:+.4f}% "
                        f"60s={bs.price_change_pct_60s:+.4f}% | "
                        f"vol={bs.volatility_5m:.2f}σ press={bs.bid_pressure:.2f}",
                        "dark_grey",
                    )
                elif not bs.connected:
                    cprint("Binance: NOT CONNECTED", "red")
        
        # First-scan diagnostic: show market matching stats
        if not self._first_scan_done:
            self._first_scan_done = True
            self._log_market_diagnostics(market_data_list)
        self._latest_market_data = list(market_data_list)
        self._update_live_position_marks(market_data_list)
        
        # Show adaptive risk state (only log if throttled — that's important)
        if self.risk_manager.adaptive_enabled:
            throttle = self.risk_manager.get_throttle_factor()
            if throttle < 1.0:
                cprint(f"Throttle: {throttle*100:.0f}% (drawdown protection active)", "yellow")
        
        # Run each strategy
        total_signals = 0
        for strategy in self.strategies:
            strat_name = strategy.name.upper()
            
            # Check strategy health before running
            if self.analytics_enabled and self.strategy_tracker:
                healthy, health_reason = self.strategy_tracker.is_strategy_healthy(strategy.name)
                if not healthy:
                    cprint(f"[{strat_name}] Disabled: {health_reason}", "yellow")
                    continue
            
            # Run strategy analysis
            signals = strategy.analyze(market_data_list)
            
            if signals:
                total_signals += len(signals)
                
                # Execute signals
                n_blocked = 0
                n_executed = 0
                block_reason = ""
                for sig in signals:
                    is_sell = sig.signal_type == SignalType.SELL
                    trade_value = sig.size * sig.price
                    adaptive_size = self.risk_manager.get_adaptive_order_size(trade_value)
                    exec_signal = sig
                    resized = False
                    condition_id = str(sig.metadata.get("condition_id") or "")
                    market_slug = str(sig.market_slug or "")

                    if (
                        (condition_id and condition_id in self._stale_shortterm_condition_ids)
                        or (market_slug and market_slug in self._stale_shortterm_market_slugs)
                    ):
                        n_blocked += 1
                        block_reason = "Short-term signal suppressed until next market refresh"
                        continue

                    stale_reason = None if is_sell else self._shortterm_signal_stale_reason(sig)
                    if stale_reason:
                        if condition_id:
                            self._stale_shortterm_condition_ids.add(condition_id)
                        if market_slug:
                            self._stale_shortterm_market_slugs.add(market_slug)
                        n_blocked += 1
                        block_reason = stale_reason
                        cprint(
                            f"Skipped short-term signal: {sig.market_slug} ({stale_reason})",
                            "yellow",
                        )
                        continue

                    if (
                        BALANCE_STALE_BLOCK_BUYS
                        and not is_sell
                        and self._last_balance_refresh_success_ts > 0
                    ):
                        age = time.time() - self._last_balance_refresh_success_ts
                        if age > BALANCE_STALE_MAX_SECONDS:
                            n_blocked += 1
                            block_reason = (
                                f"Balance data stale ({int(age)}s > {BALANCE_STALE_MAX_SECONDS}s)"
                            )
                            continue
                    
                    # For BUY: check risk limits. For SELL (position close): skip
                    if not is_sell:
                        can_open, reason = self.risk_manager.can_open_position(
                            sig.token_id,
                            adaptive_size,
                            sig.price,
                            strategy=strategy.name,
                        )
                        if not can_open:
                            n_blocked += 1
                            block_reason = reason
                            continue

                        # Adaptive risk should resize actual execution, not just checks.
                        if sig.price > 0:
                            adaptive_shares = adaptive_size / sig.price
                            if adaptive_shares < POLYMARKET_MIN_ORDER_SIZE:
                                n_blocked += 1
                                block_reason = f"Size below Polymarket minimum ({POLYMARKET_MIN_ORDER_SIZE:.0f} shares)"
                                continue
                            exec_signal = replace(sig, size=round(max(POLYMARKET_MIN_ORDER_SIZE, adaptive_shares), 4))
                            resized = abs(exec_signal.size - sig.size) > 0.0001

                            # Visibility: show when adaptive risk materially resizes BUYs.
                            if resized and not _DASHBOARD_MODE:
                                original_notional = sig.size * sig.price
                                resized_notional = exec_signal.size * exec_signal.price
                                rel_delta = (
                                    abs(resized_notional - original_notional) / original_notional
                                    if original_notional > 0
                                    else 0.0
                                )
                                if rel_delta >= 0.05:
                                    import time as _t
                                    _arl = getattr(self, "_adaptive_resize_log", {})
                                    _now = _t.time()
                                    key = f"{strategy.name}:{sig.token_id}"
                                    if _now - _arl.get(key, 0) >= 15:
                                        _arl[key] = _now
                                        self._adaptive_resize_log = _arl
                                        throttle = self.risk_manager.get_throttle_factor()
                                        cprint(
                                            f"adaptive resize: "
                                            f"${original_notional:.2f} -> ${resized_notional:.2f} "
                                            f"({sig.size:.2f} -> {exec_signal.size:.2f} shares, throttle {throttle*100:.0f}%)",
                                            "cyan",
                                        )
                    
                    exec_signal = replace(
                        exec_signal,
                        metadata={**(exec_signal.metadata or {}), "outcome_side": exec_signal.side},
                    )

                    # Execute via strategy
                    results = strategy.execute([exec_signal], self.order_manager)
                    
                    # Record analytics at submission time only; fee/volume accounting happens on fills.
                    for result in results:
                        if result.get("success"):
                            n_executed += 1
                            if self.analytics_enabled and self.strategy_tracker:
                                self.strategy_tracker.record_trade(
                                    strategy=strategy.name,
                                    token_id=exec_signal.token_id,
                                    market_slug=exec_signal.market_slug,
                                    side=exec_signal.side,
                                    price=exec_signal.price,
                                    size=exec_signal.size,
                                    pnl=0.0,  # P&L tracked on exit
                                    fees=0.0,
                                    is_exit=is_sell,
                                )
                
                # Log summary: executed trades always, risk blocks throttled
                if n_executed > 0:
                    cprint(f"[{strat_name}] {n_executed}/{len(signals)} signal(s) executed!", "green", attrs=["bold"])
                if n_blocked > 0:
                    import time as _t
                    _rb_log = getattr(self, '_risk_block_log', {})
                    _now = _t.time()
                    throttle_seconds = 300 if any(
                        token in block_reason.lower() for token in ("entry window", "entry cap")
                    ) else 30
                    if _now - _rb_log.get(f"{strat_name}:{block_reason}", 0) >= throttle_seconds:
                        _rb_log[f"{strat_name}:{block_reason}"] = _now
                        self._risk_block_log = _rb_log
                        cprint(f"[{strat_name}] {n_blocked} blocked: {block_reason}", "yellow")
            else:
                # "No opportunities" is noise — skip in dashboard mode
                if not _DASHBOARD_MODE:
                    cprint(f"[{strat_name}] No opportunities", "white")
        
        if total_signals == 0 and not _DASHBOARD_MODE:
            cprint("Waiting for opportunities...", "white")
        
        # Update the TUI dashboard
        self._push_dashboard_state()
    
    def _push_dashboard_state(self) -> None:
        """Build a DashboardState snapshot and push it to the TUI."""
        from .logging_utils import _DASHBOARD_MODE
        if not _DASHBOARD_MODE:
            return
        
        # Binance snapshot
        bs = BinanceSnapshot()
        if self.binance_feed:
            b = self.binance_feed.get_state()
            bs = BinanceSnapshot(
                connected=b.connected,
                price=b.last_price,
                chg_10s=b.price_change_pct_10s,
                chg_30s=b.price_change_pct_30s,
                chg_60s=b.price_change_pct_60s,
                volatility=b.volatility_5m,
                pressure=b.bid_pressure,
            )
        
        # Strategy rows
        strat_rows = []
        strategy_details: List[StrategyDetailRow] = []
        ml_decision_rows: List[MLDecisionRow] = []
        eligible_counts = self._priority_strategy_eligibility()
        now_ts = time.time()
        for strat in self.strategies:
            state = strat.get_state()
            if self.analytics_enabled and self.strategy_tracker:
                healthy, _ = self.strategy_tracker.is_strategy_healthy(strat.name)
            else:
                healthy = True
            
            # Last signal time
            last_ts = ""
            if hasattr(strat, 'last_signal_time') and strat.last_signal_time:
                most_recent = max(strat.last_signal_time.values()) if strat.last_signal_time else 0
                if most_recent > 0:
                    ago = time.time() - most_recent
                    if ago < 60:
                        last_ts = f"{ago:.0f}s ago"
                    elif ago < 3600:
                        last_ts = f"{ago/60:.0f}m ago"
                    else:
                        last_ts = f"{ago/3600:.1f}h ago"

            horizon_stats = state.get("horizon_stats") if isinstance(state, dict) else None
            if strat.name == "ml_directional" and isinstance(horizon_stats, dict):
                for horizon in ("15m", "1h"):
                    horizon_state = horizon_stats.get(horizon, {}) or {}
                    decision_snapshot = horizon_state.get("decision_snapshot", {}) or {}
                    horizon_last_ts = ""
                    horizon_last_value = float(horizon_state.get("last_signal_ts", 0) or 0)
                    if horizon_last_value > 0:
                        ago = max(0.0, now_ts - horizon_last_value)
                        if ago < 60:
                            horizon_last_ts = f"{ago:.0f}s ago"
                        elif ago < 3600:
                            horizon_last_ts = f"{ago/60:.0f}m ago"
                        else:
                            horizon_last_ts = f"{ago/3600:.1f}h ago"
                    accuracy_samples = int(horizon_state.get("resolved_accuracy_samples", 0) or 0)
                    brier_samples = int(horizon_state.get("resolved_brier_samples", 0) or 0)
                    accuracy_display = (
                        f"{float(horizon_state.get('rolling_accuracy', 0.0) or 0.0):.2%}"
                        if accuracy_samples > 0
                        else "—"
                    )
                    brier_display = (
                        f"{float(horizon_state.get('rolling_brier', 0.0) or 0.0):.3f}"
                        if brier_samples > 0
                        else "—"
                    )
                    sizing_debug = horizon_state.get("sizing_debug", {}) or {}
                    sizing_detail = ""
                    if sizing_debug:
                        bet_size_val = sizing_debug.get("bet_size_usd")
                        mode_val = sizing_debug.get("mode") or "—"
                        kelly_enabled = sizing_debug.get("kelly_enabled")
                        bet_size_txt = f"${float(bet_size_val):.2f}" if bet_size_val not in (None, "") else "—"
                        sizing_label = "kelly" if kelly_enabled is not False else "fixed"
                        sizing_detail = f" | {sizing_label} {mode_val} {bet_size_txt}"
                    strat_rows.append(StrategyRow(
                        name=f"{strat.name}:{horizon}",
                        signals=int(horizon_state.get("signals", 0) or 0),
                        trades=int(horizon_state.get("trades", 0) or 0),
                        pnl=state['pnl'] if self.analytics_enabled else 0.0,
                        healthy=healthy,
                        last_signal=horizon_last_ts or "—",
                        status=horizon_state.get("status", "—"),
                        detail=(
                            f"model {horizon_state.get('model_version', '—')} | "
                            f"eligible {eligible_counts.get(f'ml_directional:{horizon}', 0)}"
                            f"{sizing_detail}"
                        ),
                    ))
                    strategy_details.append(
                        StrategyDetailRow(
                            name=f"{strat.name}:{horizon}",
                            summary=horizon_state.get("status", "—"),
                            detail=(
                                f"model {horizon_state.get('model_version', '—')} | "
                                f"acc {accuracy_display} | "
                                f"brier {brier_display} | "
                                f"pending {int(horizon_state.get('pending_resolutions', 0) or 0)} | "
                                f"eligible {eligible_counts.get(f'ml_directional:{horizon}', 0)}"
                                f"{sizing_detail}"
                            ),
                        )
                    )
                    inference_ts = float(decision_snapshot.get("last_inference_ts", 0) or 0)
                    ml_decision_rows.append(
                        MLDecisionRow(
                            horizon=horizon,
                            decision=str(decision_snapshot.get("decision") or "—"),
                            market=str(decision_snapshot.get("market_slug") or ""),
                            outcome=str(decision_snapshot.get("outcome") or ""),
                            probability=float(decision_snapshot.get("probability", 0.0) or 0.0),
                            threshold_probability=float(decision_snapshot.get("threshold_probability", 0.0) or 0.0),
                            market_mid_price=float(decision_snapshot.get("market_mid_price", 0.0) or 0.0),
                            buy_price=float(decision_snapshot.get("buy_price", 0.0) or 0.0),
                            entry_edge=float(decision_snapshot.get("entry_edge", 0.0) or 0.0),
                            mid_edge=float(decision_snapshot.get("mid_edge", 0.0) or 0.0),
                            required_edge=float(decision_snapshot.get("required_edge", 0.0) or 0.0),
                            block_reason=str(decision_snapshot.get("block_reason") or ""),
                            status=str(horizon_state.get("status") or "—"),
                            inference_age_seconds=max(0.0, now_ts - inference_ts) if inference_ts > 0 else 0.0,
                            model_version=str(horizon_state.get("model_version") or "—"),
                        )
                    )
                continue

            strat_rows.append(StrategyRow(
                name=strat.name,
                signals=state['signals_generated'],
                trades=state['trades_executed'],
                pnl=state['pnl'] if self.analytics_enabled else 0.0,
                healthy=healthy,
                last_signal=last_ts,
                status=state.get('status', ''),
                detail=self._strategy_summary_text(strat.name, state),
            ))
            strategy_details.append(
                self._strategy_detail_row(strat.name, state)
            )
        
        # Portfolio snapshot
        rm = self.risk_manager
        active_orders = 0
        open_position_rows: List[OpenPositionRow] = []
        open_order_rows: List[OpenOrderRow] = []
        om_filled = 0
        om_cancelled = 0
        om_fill_rate = 0.0
        max_orders = 10
        if self.order_manager:
            active_orders_list = self.order_manager.get_active_orders()
            active_orders = len(active_orders_list)
            for order in sorted(active_orders_list, key=lambda item: item.created_at, reverse=True)[:8]:
                metadata = order.metadata or {}
                partial_fill = ""
                if order.filled_size > 0:
                    partial_fill = f"{order.filled_size:.2f}/{order.size:.2f}"
                open_order_rows.append(
                    OpenOrderRow(
                        strategy=str(metadata.get("strategy") or "unknown"),
                        market=order.market_slug,
                        outcome=str(metadata.get("outcome_side") or metadata.get("side") or "—"),
                        price=order.price,
                        size=order.size,
                        age_seconds=order.age_seconds,
                        status=order.status.value,
                        partial_fill=partial_fill,
                    )
                )
            om_stats = self.order_manager.get_stats()
            om_filled = om_stats.get('total_filled', 0)
            om_fill_events = om_stats.get('total_fill_events', om_filled)
            om_cancelled = om_stats.get('total_cancelled', 0)
            om_fill_rate = om_stats.get('fill_rate', 0) * 100
            max_orders = getattr(self.order_manager, 'max_active_orders', 10)
        
        total_exp = rm.get_total_exposure()
        rm_status = rm.get_status()
        for position in sorted(rm.positions.values(), key=lambda item: abs(item.unrealized_pnl), reverse=True)[:8]:
            cost_basis = float(position.cost_basis or 0.0)
            pnl_pct = (position.unrealized_pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0
            open_position_rows.append(
                OpenPositionRow(
                    market=position.market_slug,
                    outcome=position.side,
                    entry=position.avg_price,
                    mark=position.current_price,
                    size=position.size,
                    unrealized_pnl=position.unrealized_pnl,
                    pnl_pct=pnl_pct,
                )
            )
        balance_age = time.time() - self._last_balance_refresh_success_ts if self._last_balance_refresh_success_ts > 0 else 0.0
        portfolio = PortfolioSnapshot(
            balance=rm.current_balance,
            start_balance=rm.starting_balance,
            exposure=total_exp,
            exposure_pct=(total_exp / rm.current_balance * 100) if rm.current_balance > 0 else 0,
            daily_pnl=rm_status.get('daily_pnl', 0.0),
            unrealized_pnl=rm_status.get('unrealized_pnl', 0.0),
            positions=len(rm.positions),
            active_orders=active_orders,
            max_orders=max_orders,
            filled=om_filled,
            fill_events=om_fill_events,
            cancelled=om_cancelled,
            fill_rate=om_fill_rate,
            throttle=rm.get_throttle_factor() if rm.adaptive_enabled else 1.0,
            balance_age_seconds=balance_age,
            balance_stale_block_buys=BALANCE_STALE_BLOCK_BUYS,
            balance_stale_max_seconds=float(BALANCE_STALE_MAX_SECONDS),
            last_balance_sync_ts=self._last_balance_refresh_success_ts,
            last_positions_sync_ts=self._last_positions_sync_ts,
            entry_gate_reason=str(rm_status.get('entry_gate_reason') or ''),
            entry_days=str(rm_status.get('entry_days') or 'ALL'),
            entry_window=str(rm_status.get('entry_window') or 'ALL'),
        )

        now_ts = time.time()
        feed_stats = self.feed.get_stats() if self.feed else {}
        user_feed_stats = self.user_feed.get_stats() if self.user_feed else {}
        last_market_event_age = 0.0
        last_user_event_age = 0.0
        if self.feed and self.feed.last_event_time:
            last_market_event_age = max((datetime.now() - self.feed.last_event_time).total_seconds(), 0.0)
        if self.user_feed and self.user_feed.last_event_time:
            last_user_event_age = max((datetime.now() - self.user_feed.last_event_time).total_seconds(), 0.0)
        execution_health = ExecutionHealthSnapshot(
            market_ws_connected=bool(feed_stats.get("is_connected")),
            user_ws_connected=bool(user_feed_stats.get("is_connected")),
            binance_connected=bool(bs.connected),
            paper_mode=bool(PAPER_TRADING),
            last_market_event_age=last_market_event_age,
            last_user_event_age=last_user_event_age,
            balance_age=balance_age,
            positions_age=max(now_ts - self._last_positions_sync_ts, 0.0) if self._last_positions_sync_ts else 0.0,
            last_fill_age=max(now_ts - self._last_fill_event_ts, 0.0) if self._last_fill_event_ts else 0.0,
        )
        market_quality = MarketQualitySnapshot(
            total_tokens=int(self._market_quality_stats.get("total_tokens", 0) or 0),
            real_quote_tokens=int(self._market_quality_stats.get("real_quote_tokens", 0) or 0),
            missing_quote_tokens=int(self._market_quality_stats.get("missing_quote_tokens", 0) or 0),
            not_accepting_orders=int(self._market_quality_stats.get("not_accepting_orders", 0) or 0),
            resolved_tokens=int(self._market_quality_stats.get("resolved_tokens", 0) or 0),
            eligible_ml=int(
                eligible_counts.get("ml_directional:15m", 0) + eligible_counts.get("ml_directional:1h", 0)
            ),
            eligible_terminal=int(eligible_counts.get("terminal_convergence", 0)),
            eligible_combo=int(eligible_counts.get("combinatorial_arb", 0)),
        )
        recent_fill_rows = [
            RecentFillRow(
                strategy=item["strategy"],
                market=item["market"],
                side=item["side"],
                price=float(item["price"]),
                size=float(item["size"]),
                age_seconds=max(now_ts - float(item["timestamp"]), 0.0),
            )
            for item in list(self._recent_fills)
        ]
        
        # Build Risk Engine snapshot
        from .dashboard import RiskEngineSnapshot
        try:
            from .native.pmkernel import NATIVE_AVAILABLE
            self.risk_manager.compute_portfolio_greeks()
            re_snap = RiskEngineSnapshot(
                native_available=NATIVE_AVAILABLE,
                engine_label="NATIVE" if NATIVE_AVAILABLE else "PYTHON",
                net_delta=self.risk_manager.net_delta,
                net_gamma=self.risk_manager.net_gamma,
                active_markets=len(self.risk_manager.positions),
            )
        except Exception:
            re_snap = RiskEngineSnapshot()

        state = DashboardState(
            paper=PAPER_TRADING,
            n_markets=len(self.markets),
            scan_number=self._scan_count,
            binance=bs,
            strategies=strat_rows,
            portfolio=portfolio,
            execution_health=execution_health,
            market_quality=market_quality,
            open_positions=open_position_rows,
            open_orders=open_order_rows,
            recent_fills=recent_fill_rows,
            strategy_details=strategy_details,
            ml_decisions=ml_decision_rows,
            risk_engine=re_snap,
        )
        self.dashboard.update(state)

    def _update_live_position_marks(self, market_data_list: List[MarketData]) -> None:
        prices: Dict[str, float] = {}
        for data in market_data_list:
            if getattr(data, "has_real_quotes", False) and data.mid_price > 0:
                prices[data.token_id] = float(data.mid_price)
            elif data.last_price > 0:
                prices[data.token_id] = float(data.last_price)
        if prices:
            self.risk_manager.update_prices(prices)

    def _strategy_detail_row(self, name: str, state: Dict) -> StrategyDetailRow:
        eligible_counts = self._priority_strategy_eligibility()
        if name == "terminal_convergence":
            return StrategyDetailRow(
                name=name,
                summary=str(state.get("status", "—") or "—"),
                detail=(
                    f"best edge {float(state.get('best_edge_cents', 0.0) or 0.0):.1f}c | "
                    f"nearest exp {float(state.get('nearest_expiry_s', 0.0) or 0.0):.0f}s | "
                    f"mode {state.get('fill_mode', '—')} | eligible {eligible_counts.get('terminal_convergence', 0)}"
                ),
            )
        if name == "combinatorial_arb":
            return StrategyDetailRow(
                name=name,
                summary=str(state.get("status", "—") or "—"),
                detail=(
                    f"parsed {int(state.get('parsed_markets', 0) or 0)} | "
                    f"valid {int(state.get('valid_parsed', 0) or 0)} | "
                    f"cooldowns {int(state.get('active_cooldowns', 0) or 0)} | "
                    f"eligible {eligible_counts.get('combinatorial_arb', 0)}"
                ),
            )
        return StrategyDetailRow(
            name=name,
            summary=str(state.get("status", "—") or "—"),
            detail=(
                f"signals {int(state.get('signals_generated', 0) or 0)} | "
                f"trades {int(state.get('trades_executed', 0) or 0)}"
            ),
        )

    def _strategy_summary_text(self, name: str, state: Dict) -> str:
        eligible_counts = self._priority_strategy_eligibility()
        if name == "terminal_convergence":
            return (
                f"edge {float(state.get('best_edge_cents', 0.0) or 0.0):.1f}c | "
                f"exp {float(state.get('nearest_expiry_s', 0.0) or 0.0):.0f}s | "
                f"eligible {eligible_counts.get('terminal_convergence', 0)}"
            )
        if name == "combinatorial_arb":
            return (
                f"valid {int(state.get('valid_parsed', 0) or 0)} | "
                f"cooldowns {int(state.get('active_cooldowns', 0) or 0)} | "
                f"eligible {eligible_counts.get('combinatorial_arb', 0)}"
            )
        return (
            f"signals {int(state.get('signals_generated', 0) or 0)} | "
            f"trades {int(state.get('trades_executed', 0) or 0)}"
        )

    def _priority_strategy_eligibility(self) -> Dict[str, int]:
        counts = {
            "ml_directional:15m": 0,
            "ml_directional:1h": 0,
            "terminal_convergence": 0,
            "combinatorial_arb": 0,
        }
        latest_market_data = getattr(self, "_latest_market_data", [])
        strategies = getattr(self, "strategies", [])
        if not latest_market_data or not strategies:
            return counts

        ml_strategy = next((s for s in strategies if s.name == "ml_directional"), None)
        terminal_strategy = next((s for s in strategies if s.name == "terminal_convergence"), None)
        combo_strategy = next((s for s in strategies if s.name == "combinatorial_arb"), None)

        for data in latest_market_data:
            if ml_strategy and ml_strategy.should_trade_market(data):
                text = f"{data.question} {data.market_slug}".lower()
                horizon = ml_strategy._extract_horizon(text) or "15m"
                key = f"ml_directional:{horizon}"
                if key in counts:
                    counts[key] += 1
            if terminal_strategy and terminal_strategy.should_trade_market(data):
                counts["terminal_convergence"] += 1
            if combo_strategy and combo_strategy._eligible_market(data):
                if combo_strategy._parse_market(data):
                    counts["combinatorial_arb"] += 1
        return counts

    def _user_feed_markets(self) -> List[str]:
        return sorted(str(condition_id) for condition_id in self.markets.keys() if condition_id)
    
    def _parse_market_end_ts(self, m: dict) -> Optional[float]:
        """Parse market end/resolution timestamp. Returns Unix sec or None."""
        return _parse_market_end_ts(m)

    def _build_market_data(self) -> List[MarketData]:
        """Build MarketData objects from cached data."""
        data_list = []
        quality_stats = {
            "total_tokens": 0,
            "real_quote_tokens": 0,
            "missing_quote_tokens": 0,
            "not_accepting_orders": 0,
            "resolved_tokens": 0,
        }

        for condition_id, market in self.markets.items():
            try:
                # Gamma API includes bestBid/bestAsk directly in market data!
                best_bid = float(market.get("bestBid", 0) or 0)
                best_ask = float(market.get("bestAsk", 1) or 1)
                market_has_real_quotes = best_bid > 0 and best_ask > 0 and best_ask < 1
                accepting_orders = bool(market.get("acceptingOrders", True))
                fees_enabled = bool(market.get("feesEnabled", True))
                fee_rate_bps = market.get("feeRateBps")
                if fee_rate_bps is None and fees_enabled:
                    _rate, _exp, _taker_only = market_fee_params(market)
                    fee_rate_bps = _rate * 10_000
                try:
                    fee_rate_bps = float(fee_rate_bps) if fee_rate_bps is not None else None
                except (TypeError, ValueError):
                    fee_rate_bps = None
                is_resolved = bool(
                    market.get("resolved")
                    or market.get("marketResolved")
                    or market.get("market_resolved")
                    or market.get("closed")
                )
                resolution_outcome = market.get("winningOutcome") or market.get("winner") or market.get("resolutionOutcome")
                if hasattr(self, "feed") and self.feed:
                    resolved_event = self.feed.resolved_markets.get(str(condition_id))
                    if resolved_event:
                        is_resolved = True
                        if resolved_event.winning_outcome:
                            resolution_outcome = resolved_event.winning_outcome

                # Check if this is a crypto short-term market (5m, 15m, 1h, 4h from events)
                q_lower = market.get("question", "").lower()
                slug_lower = market.get("slug", "").lower()
                mtext = f"{q_lower} {slug_lower}"
                is_crypto_st = (
                    any(kw in mtext for kw in ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp"])
                    and any(kw in mtext for kw in BTC_5MIN_KEYWORDS)
                )
                
                if not market_has_real_quotes and not is_crypto_st and not any(
                    kw in mtext for kw in ["above", "below", "over", "under", "exceed", "reach"]
                ):
                    continue
                if not market_has_real_quotes:
                    best_bid = 0.0
                    best_ask = 0.0
                
                mid = (best_bid + best_ask) / 2
                spread = best_ask - best_bid
                
                # Get volume - try different field names
                volume = float(
                    market.get("volume24hrClob", 0) or 
                    market.get("volumeClob", 0) or 
                    market.get("volume", 0) or 0
                )
                
                # Get the actual token ID for YES outcome
                # The CLOB API needs the token_id, not the conditionId!
                # clobTokenIds format: ["YES_TOKEN_ID", "NO_TOKEN_ID"]
                clob_tokens = market.get("clobTokenIds", [])
                
                if not clob_tokens or len(clob_tokens) < 1:
                    continue
                
                # Parse clobTokenIds - it might be a string or list
                if isinstance(clob_tokens, str):
                    import json
                    try:
                        clob_tokens = json.loads(clob_tokens)
                    except:
                        continue
                
                # Parse outcomes — e.g. ["Up","Down"], ["Yes","No"]
                raw_outcomes = market.get("outcomes", [])
                if isinstance(raw_outcomes, str):
                    import json as _json
                    try:
                        raw_outcomes = _json.loads(raw_outcomes)
                    except Exception:
                        raw_outcomes = ["Yes", "No"]
                if not raw_outcomes:
                    raw_outcomes = ["Yes", "No"]
                
                # Build one MarketData per outcome token
                for idx, token_id in enumerate(clob_tokens):
                    if not token_id:
                        continue
                    outcome_label = _normalize_market_outcome_label(
                        market,
                        raw_outcomes[idx] if idx < len(raw_outcomes) else "Yes",
                        idx,
                    )
                    
                    # For the first token, use API bid/ask directly
                    # For the second token, invert (complement pricing)
                    token_has_real_quotes = market_has_real_quotes
                    if idx == 0:
                        t_bid, t_ask = best_bid, best_ask
                    else:
                        if market_has_real_quotes:
                            t_bid = round(max(0.01, 1.0 - best_ask), 4)
                            t_ask = round(min(0.99, 1.0 - best_bid), 4)
                        else:
                            t_bid, t_ask = 0.0, 0.0
                    
                    t_mid = (t_bid + t_ask) / 2
                    t_spread = t_ask - t_bid

                    # Get cached recent trades for this token (for VPIN)
                    token_trades = [
                        t for t in getattr(self, '_recent_trades_cache', [])
                        if (t.get("asset_id") or t.get("token_id")) == token_id
                    ]

                    end_ts = self._parse_market_end_ts(market) if is_crypto_st else None

                    # Prefer WebSocket orderbook for best_bid/best_ask when available (real-time)
                    ob_data = None
                    if hasattr(self, 'feed') and self.feed:
                        ws_ob = self.feed.get_latest_orderbook(token_id)
                        if ws_ob is not None:
                            ob_data = {"bids": ws_ob.bids, "asks": ws_ob.asks}
                            # Override Gamma prices with WebSocket for latency-sensitive strategies
                            t_bid = float(ws_ob.best_bid) if ws_ob.bids else t_bid
                            t_ask = float(ws_ob.best_ask) if ws_ob.asks else t_ask
                            t_mid = (t_bid + t_ask) / 2
                            t_spread = t_ask - t_bid
                            token_has_real_quotes = bool(ws_ob.bids or ws_ob.asks)

                    data_source_quality = "live_quotes" if token_has_real_quotes else "missing_quotes"
                    quality_stats["total_tokens"] += 1
                    if token_has_real_quotes:
                        quality_stats["real_quote_tokens"] += 1
                    else:
                        quality_stats["missing_quote_tokens"] += 1
                    if not accepting_orders:
                        quality_stats["not_accepting_orders"] += 1
                    if is_resolved:
                        quality_stats["resolved_tokens"] += 1

                    tick_size = None
                    if hasattr(self, "feed") and self.feed:
                        tick_size = self.feed.tick_size_by_token.get(str(token_id))
                    if not tick_size or tick_size <= 0:
                        for key in (
                            "orderPriceMinTickSize",
                            "order_price_min_tick_size",
                            "minimumTickSize",
                            "minimum_tick_size",
                        ):
                            raw_ts = market.get(key)
                            if raw_ts is None:
                                continue
                            try:
                                tsv = float(raw_ts)
                                if tsv > 0:
                                    tick_size = tsv
                                    break
                            except (TypeError, ValueError):
                                pass
                    if not tick_size or tick_size <= 0:
                        tick_size = 0.001

                    data = MarketData(
                        token_id=token_id,
                        condition_id=condition_id,
                        market_slug=market.get("slug", ""),
                        question=market.get("question", ""),
                        outcome=outcome_label,
                        best_bid=t_bid,
                        best_ask=t_ask,
                        mid_price=t_mid,
                        spread=t_spread,
                        volume_24h=volume,
                        liquidity=float(market.get("liquidityClob", 0) or 0),
                        last_price=float(market.get("lastTradePrice", t_mid) or t_mid),
                        orderbook=ob_data,
                        recent_trades=token_trades if token_trades else None,
                        end_date_ts=end_ts,
                        event_title=str(market.get("event_title") or ""),
                        event_slug=str(market.get("event_slug") or ""),
                        has_real_quotes=token_has_real_quotes,
                        accepting_orders=accepting_orders,
                        fees_enabled=fees_enabled,
                        fee_rate_bps=fee_rate_bps,
                        is_resolved=is_resolved,
                        resolution_outcome=str(resolution_outcome).upper() if resolution_outcome not in (None, "") else None,
                        data_source_quality=data_source_quality,
                        quote_source="websocket" if ob_data else ("gamma" if token_has_real_quotes else "missing"),
                        tick_size=tick_size,
                    )
                    
                    data_list.append(data)
                    
            except Exception as e:
                continue
        self._market_quality_stats = quality_stats
        return data_list

    def _log_market_diagnostics(self, market_data_list: List[MarketData]) -> None:
        """Log how many markets match each strategy's filters (first scan only)."""
        btc_count = 0
        btc_5min_count = 0
        crypto_count = 0
        with_orderbook = 0
        with_trades = 0
        sample_all: List[str] = []  # first few market questions for debugging

        for md in market_data_list:
            q = md.question.lower()
            slug = md.market_slug.lower()
            text = f"{q} {slug}"  # search both fields
            is_btc = any(kw in text for kw in ["bitcoin", "btc"])
            is_5min = any(kw in text for kw in BTC_5MIN_KEYWORDS)
            is_crypto = any(kw in text for kw in CRYPTO_MARKET_KEYWORDS)

            if is_btc:
                btc_count += 1
            if is_btc and is_5min:
                btc_5min_count += 1
            if is_crypto:
                crypto_count += 1
            if md.orderbook:
                with_orderbook += 1
            if md.recent_trades:
                with_trades += 1
            if len(sample_all) < 5:
                sample_all.append(f"Q={q[:60]} | slug={slug[:40]}")

        cprint("\nMarket Diagnostics (first scan):", "cyan", attrs=["bold"])
        cprint(f"Total tokens: {len(market_data_list)}", "white")
        cprint(f"Crypto markets: {crypto_count}", "white")
        cprint(f"BTC markets: {btc_count}", "white")
        cprint(f"BTC short-term markets: {btc_5min_count}", "white")
        cprint(f"With orderbook data: {with_orderbook}", "white")
        cprint(f"With recent trades: {with_trades}", "white")

        # Combinatorial arb: threshold markets (above/below + numeric)
        threshold_count = sum(
            1 for md in market_data_list
            if any(kw in (md.question or "").lower() for kw in ["above", "below", "over", "under", "exceed", "reach"])
            and any(kw in (md.question or "").lower() for kw in ["bitcoin", "btc", "ethereum", "eth", "solana", "sol"])
        )
        cprint(f"Threshold markets (combo_arb): {threshold_count}", "white")

        if btc_5min_count == 0:
            cprint(f"No BTC short-term markets matched!", "yellow")
            cprint(f"Sample markets (first 5):", "yellow")
            for s in sample_all:
                cprint(f"{s}", "yellow")

    def _fetch_markets(self, is_refresh: bool = False):
        """Fetch and filter available markets."""
        try:
            now_ts = time.time()
            self._last_market_refresh_started_at = now_ts
            desired_buckets = {
                f"{asset}:{horizon}"
                for asset in self.ml_directional_lean_assets
                for horizon in self.ml_directional_lean_horizons
            } if self.ml_directional_lean_mode else set()
            lean_target_max_sec = 0.0
            if self.ml_directional_lean_mode:
                if SHORTTERM_MAX_HOURS_AHEAD > 0:
                    lean_target_max_sec = SHORTTERM_MAX_HOURS_AHEAD * 3600
                elif MAX_HOURS_TO_EXPIRY > 0:
                    lean_target_max_sec = MAX_HOURS_TO_EXPIRY * 3600

            def _is_within_lean_target_window(market_like: Dict) -> bool:
                if not self.ml_directional_lean_mode:
                    return True
                end_ts = _parse_market_end_ts(market_like)
                if end_ts is None:
                    return False
                remaining = end_ts - now_ts
                if remaining < 0:
                    return False
                if lean_target_max_sec > 0 and remaining > lean_target_max_sec:
                    return False
                return True

            def _is_current_cycle_candidate(market_like: Dict) -> bool:
                bucket = _shortterm_bucket_key(_market_text_payload(market_like))
                horizon = _extract_horizon_from_bucket(bucket)
                end_ts = _parse_market_end_ts(market_like)
                if not bucket or not horizon or end_ts is None:
                    return False
                if end_ts < now_ts:
                    return False
                cycle_end_ts = _current_cycle_end_ts(now_ts, horizon)
                if cycle_end_ts is None:
                    return False
                cycle_grace_sec = 90
                return end_ts <= (cycle_end_ts + cycle_grace_sec)

            def _enriched_event_market(event: Dict, sub_market: Dict) -> Dict:
                sm = dict(sub_market)
                if not sm.get("question"):
                    sm["question"] = event.get("title", "")
                if not sm.get("slug"):
                    sm["slug"] = event.get("slug", "")
                sm["event_title"] = event.get("title", "")
                sm["event_slug"] = event.get("slug", "")
                if event.get("endDate") and not sm.get("endDate"):
                    sm["event_end_date"] = event.get("endDate")
                if event.get("end_date") and not sm.get("end_date"):
                    sm["event_end_date"] = event.get("end_date")
                if event.get("closeTime") and not sm.get("closeTime"):
                    sm["event_close_time"] = event.get("closeTime")
                if event.get("resolutionDate") and not sm.get("resolutionDate"):
                    sm["event_resolution_date"] = event.get("resolutionDate")
                return sm

            def _lean_event_cache_ttl_seconds(reference_ts: float) -> float:
                deadlines: List[float] = []
                for horizon in self.ml_directional_lean_horizons:
                    cycle_end_ts = _current_cycle_end_ts(reference_ts, horizon)
                    if cycle_end_ts is None:
                        continue
                    min_remaining = {
                        "15m": ML_DIRECTIONAL_MIN_SECONDS_TO_EXPIRY_15M,
                        "1h": ML_DIRECTIONAL_MIN_SECONDS_TO_EXPIRY_1H,
                    }.get(horizon, 0)
                    deadlines.append(float(cycle_end_ts) - max(float(min_remaining), 30.0) - reference_ts)
                if not deadlines:
                    return 5.0
                return min(15.0, max(2.0, min(deadlines)))

            if self.ml_directional_lean_mode:
                if ML_DIRECTIONAL_LEAN_EVENTS_ONLY:
                    result = []
                    scanned_market_count = 0
                else:
                    result = []
                    scanned_market_count = 0
                    matched_market_buckets = set()
                    market_cursor = ""
                    max_market_pages = 10
                    for _ in range(max_market_pages):
                        page = self.client.get_markets_page(next_cursor=market_cursor, tag="crypto")
                        if isinstance(page, dict) and "error" in page:
                            result = page
                            break
                        batch = page.get("data", []) if isinstance(page, dict) else []
                        if not isinstance(batch, list) or not batch:
                            break
                        result.extend(batch)
                        scanned_market_count += len(batch)
                        for market in batch:
                            bucket = _shortterm_bucket_key(_market_text_payload(market))
                            if bucket in desired_buckets and _is_within_lean_target_window(market):
                                matched_market_buckets.add(bucket)
                        if matched_market_buckets >= desired_buckets:
                            break
                        market_cursor = str(page.get("next_cursor", "") if isinstance(page, dict) else "").strip()
                        if not market_cursor:
                            break
            else:
                result = self.client.get_markets()
            
            if isinstance(result, dict) and "error" in result:
                cprint(f"Failed to fetch markets: {result['error']}", "red")
                return
            
            markets = result if isinstance(result, list) else result.get("data", [])
            if self.ml_directional_lean_mode:
                cprint(f"lean market scan inspected {scanned_market_count} /markets entries", "dark_grey")
            
            event_market_count = 0
            use_cached_lean_events = False
            lean_event_log_messages: List[Tuple[str, str]] = []
            if ENABLE_CRYPTO_EVENT_INFRA:
                event_limit = 100
                events = []
                scanned_events = 0
                if self.ml_directional_lean_mode:
                    if ML_DIRECTIONAL_LEAN_EVENTS_ONLY:
                        desired_buckets_key = ",".join(sorted(desired_buckets))
                        if (
                            desired_buckets_key == self._lean_event_cache_key
                            and now_ts < self._lean_event_cache_expires_at
                        ):
                            markets.extend(dict(item) for item in self._lean_event_cache_markets)
                            scanned_events = self._lean_event_cache_scanned_events
                            event_market_count = self._lean_event_cache_event_market_count
                            use_cached_lean_events = True
                        else:
                            matched_buckets = set()
                            cached_lean_markets: List[Dict] = []
                            initial_event_pages = max(len(desired_buckets) * 3, 6) if desired_buckets else 6
                            max_event_pages = max(initial_event_pages, 60)
                            for page_idx in range(max_event_pages):
                                batch = self.client.get_events(
                                    limit=event_limit,
                                    max_pages=1,
                                    offset=page_idx * event_limit,
                                    order="endDate",
                                    ascending=True,
                                )
                                if not batch:
                                    break
                                scanned_events += len(batch)
                                for event in batch:
                                    slug = event.get("slug", "").lower()
                                    title = event.get("title", "").lower()
                                    text = f"{title} {slug}"
                                    is_crypto_event = any(
                                        kw in text for kw in ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "up or down", "updown"]
                                    )
                                    if not is_crypto_event:
                                        continue
                                    for sub_market in event.get("markets", []) or []:
                                        if sub_market.get("closed"):
                                            continue
                                        if not sub_market.get("acceptingOrders"):
                                            continue
                                        sm = _enriched_event_market(event, sub_market)
                                        bucket = _shortterm_bucket_key(_market_text_payload(sm))
                                        if bucket not in desired_buckets:
                                            continue
                                        if not _is_within_lean_target_window(sm):
                                            continue
                                        markets.append(sm)
                                        cached_lean_markets.append(dict(sm))
                                        event_market_count += 1
                                        matched_buckets.add(bucket)
                                if matched_buckets >= desired_buckets:
                                    break
                                if page_idx + 1 >= initial_event_pages and matched_buckets:
                                    break
                            self._lean_event_cache_key = desired_buckets_key
                            self._lean_event_cache_markets = cached_lean_markets
                            self._lean_event_cache_scanned_events = scanned_events
                            self._lean_event_cache_event_market_count = event_market_count
                            self._lean_event_cache_expires_at = now_ts + _lean_event_cache_ttl_seconds(now_ts)
                    else:
                        matched_buckets = set()
                        max_event_pages = 10
                        for page_idx in range(max_event_pages):
                            batch = self.client.get_events(
                                limit=event_limit,
                                max_pages=1,
                                offset=page_idx * event_limit,
                                order="endDate",
                                ascending=True,
                            )
                            if not batch:
                                break
                            events.extend(batch)
                            scanned_events += len(batch)
                            for event in batch:
                                event_text = f"{event.get('title', '')} {event.get('slug', '')}".lower()
                                bucket = _shortterm_bucket_key(event_text)
                                if bucket in desired_buckets and _is_within_lean_target_window(event):
                                    matched_buckets.add(bucket)
                                for sub_market in event.get("markets", []) or []:
                                    sm = _enriched_event_market(event, sub_market)
                                    market_text = _market_text_payload(sm)
                                    bucket = _shortterm_bucket_key(market_text)
                                    if bucket in desired_buckets and _is_within_lean_target_window(sm):
                                        matched_buckets.add(bucket)
                            if matched_buckets >= desired_buckets:
                                break
                else:
                    events = self.client.get_events(limit=event_limit)
                    scanned_events = len(events)
                if events:
                    for event in events:
                        slug = event.get("slug", "").lower()
                        title = event.get("title", "").lower()
                        text = f"{title} {slug}"
                        is_crypto_event = any(
                            kw in text for kw in ["bitcoin", "btc", "ethereum", "eth",
                                                  "solana", "sol", "xrp", "up or down", "updown"]
                        )
                        if not is_crypto_event:
                            continue
                        for sub_market in event.get("markets", []):
                            if sub_market.get("closed"):
                                continue
                            if not sub_market.get("acceptingOrders"):
                                continue
                            sm = _enriched_event_market(event, sub_market)
                            markets.append(sm)
                            event_market_count += 1
                if event_market_count > 0:
                    lean_event_log_messages.append((f"Found {event_market_count} crypto event markets from /events", "cyan"))
                if self.ml_directional_lean_mode:
                    if use_cached_lean_events:
                        lean_event_log_messages.append((
                            f"lean event cache hit: {event_market_count} market(s) from {scanned_events} cached event(s)",
                            "dark_grey",
                        ))
                    else:
                        lean_event_log_messages.append((
                            f"lean event scan inspected {scanned_events} event(s)",
                            "dark_grey",
                        ))

            if self.ml_directional_lean_mode:
                discovered_buckets = set()
                discovered_current_buckets = set()
                for market in markets:
                    bucket = _shortterm_bucket_key(_market_text_payload(market))
                    if bucket in desired_buckets:
                        discovered_buckets.add(bucket)
                        if _is_current_cycle_candidate(market):
                            discovered_current_buckets.add(bucket)
                missing_current_buckets = desired_buckets - discovered_current_buckets
                if missing_current_buckets:
                    if discovered_buckets:
                        cprint(
                            f"Lean scan missing current buckets [{', '.join(sorted(missing_current_buckets))}]; widening search",
                            "yellow",
                        )
                    else:
                        cprint("Lean scan found no BTC short-term buckets; widening search", "yellow")
                    broad_events = self.client.get_events(
                        limit=100,
                        max_pages=20,
                        order="endDate",
                        ascending=True,
                    )
                    extra_event_market_count = 0
                    seen_ids = {
                        market.get("conditionId") or market.get("condition_id") or market.get("id")
                        for market in markets
                    }
                    for event in broad_events:
                        slug = event.get("slug", "").lower()
                        title = event.get("title", "").lower()
                        text = f"{title} {slug}"
                        is_crypto_event = any(
                            kw in text for kw in ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "up or down", "updown"]
                        )
                        if not is_crypto_event:
                            continue
                        for sub_market in event.get("markets", []):
                            if sub_market.get("closed"):
                                continue
                            if not sub_market.get("acceptingOrders"):
                                continue
                            sm = dict(sub_market)
                            if not sm.get("question"):
                                sm["question"] = event.get("title", "")
                            if not sm.get("slug"):
                                sm["slug"] = event.get("slug", "")
                            sm["event_title"] = event.get("title", "")
                            sm["event_slug"] = event.get("slug", "")
                            if event.get("endDate") and not sm.get("endDate"):
                                sm["event_end_date"] = event.get("endDate")
                            if event.get("end_date") and not sm.get("end_date"):
                                sm["event_end_date"] = event.get("end_date")
                            if event.get("closeTime") and not sm.get("closeTime"):
                                sm["event_close_time"] = event.get("closeTime")
                            if event.get("resolutionDate") and not sm.get("resolutionDate"):
                                sm["event_resolution_date"] = event.get("resolutionDate")
                            bucket = _shortterm_bucket_key(_market_text_payload(sm))
                            if bucket not in desired_buckets:
                                continue
                            if not _is_within_lean_target_window(sm):
                                continue
                            cid = sm.get("conditionId") or sm.get("condition_id") or sm.get("id")
                            if cid in seen_ids:
                                continue
                            markets.append(sm)
                            if cid:
                                seen_ids.add(cid)
                            extra_event_market_count += 1
                    if extra_event_market_count > 0:
                        cprint(f"fallback added {extra_event_market_count} event markets", "dark_grey")
            
            # Filter markets based on settings
            filtered = []
            seen_ids = set()
            max_expiry_sec = MAX_HOURS_TO_EXPIRY * 3600 if MAX_HOURS_TO_EXPIRY > 0 else 0
            unmatched_btc_samples = []
            lean_candidate_counts: Dict[str, int] = {}

            for market in markets:
                text = _market_text_payload(market)
                bucket = _shortterm_bucket_key(text)
                is_crypto_shortterm = bucket is not None
                bucket_asset = _extract_asset_from_bucket(bucket)
                bucket_horizon = _extract_horizon_from_bucket(bucket)
                is_target_lean_bucket = (
                    self.ml_directional_lean_mode
                    and bucket is not None
                    and bucket_asset in self.ml_directional_lean_assets
                    and bucket_horizon in self.ml_directional_lean_horizons
                )

                def _mark_reason(reason: str) -> None:
                    if is_target_lean_bucket:
                        lean_candidate_counts[reason] = lean_candidate_counts.get(reason, 0) + 1

                if (
                    self.ml_directional_lean_mode
                    and len(unmatched_btc_samples) < 5
                    and bucket is None
                    and any(token in text for token in ("btc", "bitcoin", "updown", "up or down"))
                ):
                    unmatched_btc_samples.append(
                        {
                            "question": market.get("question") or market.get("title") or "",
                            "slug": market.get("slug") or market.get("market_slug") or "",
                            "event_title": market.get("event_title") or "",
                            "event_slug": market.get("event_slug") or "",
                            "text": text[:180],
                        }
                    )

                if self.ml_directional_lean_mode:
                    if not bucket:
                        continue
                    if bucket_asset not in self.ml_directional_lean_assets:
                        _mark_reason("wrong_asset")
                        continue
                    if bucket_horizon not in self.ml_directional_lean_horizons:
                        _mark_reason("wrong_horizon")
                        continue
                
                # Deduplicate by conditionId
                cid = market.get("conditionId") or market.get("condition_id") or market.get("id")
                if cid in seen_ids:
                    _mark_reason("duplicate")
                    continue
                seen_ids.add(cid)
                
                # Skip markets resolving in more than MAX_HOURS_TO_EXPIRY
                # Exempt threshold markets (combo_arb). Filter far-dated non-threshold (e.g. up/down buckets).
                if max_expiry_sec > 0:
                    is_threshold = any(kw in text for kw in ["above", "below", "over", "under", "exceed", "reach"])
                    # Short-term cycle mode applies a tighter per-bucket nearest filter below.
                    if not is_threshold and not (SHORTTERM_NEAREST_CYCLE_ONLY and is_crypto_shortterm):
                        end_ts = _parse_market_end_ts(market)
                        if end_ts is not None and (end_ts - now_ts) > max_expiry_sec:
                            _mark_reason("too_far_expiry")
                            continue
                
                # Check if crypto-related (if filter enabled)
                if ONLY_CRYPTO_MARKETS:
                    is_crypto = any(kw in text for kw in CRYPTO_MARKET_KEYWORDS)
                    if not is_crypto:
                        _mark_reason("not_crypto")
                        continue
                
                # Check not closed
                if market.get("closed"):
                    _mark_reason("closed")
                    continue

                if self.ml_directional_lean_mode and not market.get("acceptingOrders", True):
                    _mark_reason("not_accepting_orders")
                    continue
                
                # Crypto short-term (5m/15m/1h/4h from events) often have 0 volume — bypass
                # Threshold markets (combo_arb) also bypass volume floor — many have low 24h volume
                is_threshold_mkt = any(kw in text for kw in ["above", "below", "over", "under", "exceed", "reach"])
                volume = float(market.get("volume24hr", 0) or market.get("volume24hrClob", 0) or 0)
                min_vol = 0 if (is_crypto_shortterm or is_threshold_mkt) else MIN_VOLUME_USD
                if volume < min_vol:
                    _mark_reason("low_volume")
                    continue
                
                _mark_reason("passed_primary_filters")
                filtered.append(market)

            if SHORTTERM_NEAREST_CYCLE_ONLY and filtered:
                shortterm_max_sec = (
                    SHORTTERM_MAX_HOURS_AHEAD * 3600
                    if SHORTTERM_MAX_HOURS_AHEAD > 0
                    else max_expiry_sec
                )
                nearest_by_bucket: Dict[str, tuple] = {}
                kept_non_shortterm = []
                shortterm_seen = 0
                nearest_cycle_rejections: Dict[str, int] = {}
                future_cycle_samples: Dict[str, List[str]] = {}

                for market in filtered:
                    text = _market_text_payload(market)
                    bucket = _shortterm_bucket_key(text)
                    if not bucket:
                        kept_non_shortterm.append(market)
                        continue

                    if self.ml_directional_lean_mode:
                        bucket_asset = _extract_asset_from_bucket(bucket)
                        bucket_horizon = _extract_horizon_from_bucket(bucket)
                        if bucket_asset not in self.ml_directional_lean_assets:
                            continue
                        if bucket_horizon not in self.ml_directional_lean_horizons:
                            continue

                    end_ts = _parse_market_end_ts(market)
                    if end_ts is None:
                        if self.ml_directional_lean_mode:
                            nearest_cycle_rejections["missing_end_ts"] = nearest_cycle_rejections.get("missing_end_ts", 0) + 1
                        continue

                    remaining = end_ts - now_ts
                    if remaining < 0:
                        if self.ml_directional_lean_mode:
                            nearest_cycle_rejections["expired"] = nearest_cycle_rejections.get("expired", 0) + 1
                        continue
                    if shortterm_max_sec > 0 and remaining > shortterm_max_sec:
                        if self.ml_directional_lean_mode:
                            nearest_cycle_rejections["beyond_horizon"] = nearest_cycle_rejections.get("beyond_horizon", 0) + 1
                        continue

                    bucket_cycle_end_ts = _current_cycle_end_ts(now_ts, bucket_horizon)
                    cycle_grace_sec = 90
                    if bucket_cycle_end_ts is not None and end_ts > (bucket_cycle_end_ts + cycle_grace_sec):
                        if self.ml_directional_lean_mode:
                            nearest_cycle_rejections["future_cycle"] = nearest_cycle_rejections.get("future_cycle", 0) + 1
                            sample_slug = str(market.get("slug") or market.get("market_slug") or market.get("event_slug") or "")
                            bucket_samples = future_cycle_samples.setdefault(bucket, [])
                            if sample_slug and len(bucket_samples) < 3:
                                bucket_samples.append(sample_slug)
                        continue

                    shortterm_seen += 1
                    current = nearest_by_bucket.get(bucket)
                    if current is None or remaining < current[0]:
                        nearest_by_bucket[bucket] = (remaining, market)

                nearest_shortterm = [v[1] for v in nearest_by_bucket.values()]
                filtered = kept_non_shortterm + nearest_shortterm
                nearest_cycle_summary = (
                    f"Short-term nearest-cycle filter: {len(nearest_shortterm)} kept "
                    f"across {len(nearest_by_bucket)} buckets (from {shortterm_seen})"
                )
                if not self.ml_directional_lean_mode:
                    cprint(nearest_cycle_summary, "cyan")
                if self.ml_directional_lean_mode:
                    signature_parts: List[str] = []
                    horizon_counts: Dict[str, int] = {}
                    for market in nearest_shortterm:
                        bucket = _shortterm_bucket_key(_market_text_payload(market))
                        horizon = _extract_horizon_from_bucket(bucket)
                        slug = str(market.get("slug") or market.get("market_slug") or market.get("event_slug") or "—")
                        end_ts = _parse_market_end_ts(market)
                        if horizon:
                            horizon_counts[horizon] = horizon_counts.get(horizon, 0) + 1
                        signature_parts.append(f"{bucket}:{slug}:{int(end_ts) if end_ts is not None else 0}")
                    horizon_summary = ", ".join(
                        f"{h}={horizon_counts.get(h, 0)}"
                        for h in sorted(self.ml_directional_lean_horizons)
                    )
                    lean_universe_signature = "|".join(sorted(signature_parts))
                    should_log_lean_diagnostics = (
                        lean_universe_signature != self._last_lean_universe_signature
                        or (now_ts - self._last_lean_diagnostics_log_ts) >= 120
                        or not is_refresh
                    )
                    if should_log_lean_diagnostics:
                        self._last_lean_universe_signature = lean_universe_signature
                        self._last_lean_diagnostics_log_ts = now_ts
                        cprint(nearest_cycle_summary, "cyan")
                        for message, color in lean_event_log_messages:
                            cprint(message, color)
                        cprint(
                            f"Lean universe: {len(nearest_shortterm)} market(s) "
                            f"for {', '.join(sorted(self.ml_directional_lean_assets))} "
                            f"{', '.join(sorted(self.ml_directional_lean_horizons))}",
                            "cyan",
                        )
                        for market in nearest_shortterm:
                            bucket = _shortterm_bucket_key(_market_text_payload(market))
                            horizon = _extract_horizon_from_bucket(bucket)
                            end_ts = _parse_market_end_ts(market)
                            cycle_end_ts = _current_cycle_end_ts(now_ts, horizon)
                            slug = str(market.get("slug") or market.get("market_slug") or market.get("event_slug") or "—")
                            if bucket and horizon and end_ts is not None and cycle_end_ts is not None:
                                cprint(
                                    f"selected {bucket}: slug={slug} end={int(end_ts)} cycle_end={int(cycle_end_ts)} lag={int(end_ts - cycle_end_ts)}s",
                                    "cyan",
                                )
                        cprint(f"horizons: {horizon_summary or '—'}", "cyan")
                        if lean_candidate_counts:
                            counts_summary = ", ".join(
                                f"{key}={value}" for key, value in sorted(lean_candidate_counts.items())
                            )
                            cprint(f"lean candidate flow: {counts_summary}", "yellow")
                        if nearest_cycle_rejections:
                            rejection_summary = ", ".join(
                                f"{key}={value}" for key, value in sorted(nearest_cycle_rejections.items())
                            )
                            cprint(f"nearest-cycle rejections: {rejection_summary}", "yellow")
                        if future_cycle_samples:
                            for bucket, samples in sorted(future_cycle_samples.items()):
                                cprint(
                                    f"future-cycle samples [{bucket}]: {', '.join(samples)}",
                                    "yellow",
                                )
                        if not nearest_shortterm and unmatched_btc_samples:
                            cprint("unmatched BTC-ish samples:", "yellow")
                            for sample in unmatched_btc_samples:
                                cprint(
                                    f"q={sample['question']!r} slug={sample['slug']!r} "
                                    f"event_title={sample['event_title']!r} event_slug={sample['event_slug']!r}",
                                    "yellow",
                                )

            refreshed_markets: Dict[str, Dict] = {}
            for market in filtered:
                condition_id = market.get("conditionId") or market.get("condition_id") or market.get("id")
                if condition_id:
                    refreshed_markets[condition_id] = market

            if self.ml_directional_lean_mode:
                refresh_deadline_candidates: List[float] = []
                for market in refreshed_markets.values():
                    bucket = _shortterm_bucket_key(_market_text_payload(market))
                    horizon = _extract_horizon_from_bucket(bucket)
                    end_ts = _parse_market_end_ts(market)
                    if not horizon or end_ts is None:
                        continue
                    min_remaining = {
                        "15m": ML_DIRECTIONAL_MIN_SECONDS_TO_EXPIRY_15M,
                        "1h": ML_DIRECTIONAL_MIN_SECONDS_TO_EXPIRY_1H,
                    }.get(horizon, 0)
                    deadline = float(end_ts) - max(float(min_remaining), 30.0)
                    refresh_deadline_candidates.append(deadline)
                if refresh_deadline_candidates:
                    self._next_lean_market_refresh_at = max(
                        now_ts + 5.0,
                        min(refresh_deadline_candidates),
                    )
                else:
                    self._next_lean_market_refresh_at = now_ts + float(MARKET_REFRESH_SECONDS)
                if missing_current_buckets:
                    self._next_lean_market_refresh_at = min(
                        self._next_lean_market_refresh_at,
                        now_ts + 15.0,
                    )

            if is_refresh and self.ml_directional_lean_mode and not refreshed_markets and self.markets:
                preserved_markets: Dict[str, Dict] = {}
                for condition_id, market in self.markets.items():
                    bucket = _shortterm_bucket_key(_market_text_payload(market))
                    if bucket not in desired_buckets:
                        continue
                    if _is_current_cycle_candidate(market):
                        preserved_markets[condition_id] = market
                if preserved_markets:
                    refreshed_markets = preserved_markets
                    cprint(
                        f"Lean refresh returned no current replacements; preserving {len(preserved_markets)} existing market(s)",
                        "yellow",
                    )

            if is_refresh:
                self._refresh_feed_subscriptions(refreshed_markets)
                self.markets = refreshed_markets
                self.user_feed.set_markets(self._user_feed_markets())
                self._stale_shortterm_condition_ids.clear()
                self._stale_shortterm_market_slugs.clear()
                self._cancel_stale_shortterm_orders(refreshed_markets)
            else:
                self.markets.update(refreshed_markets)
                self.user_feed.set_markets(self._user_feed_markets())

            market_type = "crypto" if ONLY_CRYPTO_MARKETS else "all"
            refresh_label = "Refreshed" if is_refresh else "Found"
            cprint(f"{refresh_label} {len(self.markets)} tradeable markets ({market_type})", "green")
            if not is_refresh and ENABLE_WEBSOCKET_FEED:
                token_ids = []
                for market in self.markets.values():
                    for token in market.get("tokens", []):
                        if token.get("token_id"):
                            token_ids.append(token["token_id"])
                    for tid in market.get("clobTokenIds", []) or []:
                        if isinstance(tid, str) and tid:
                            token_ids.append(tid)
                        elif isinstance(tid, dict) and tid.get("token_id"):
                            token_ids.append(tid["token_id"])

                if token_ids:
                    self.feed.subscribe(list(dict.fromkeys(token_ids)))

        except Exception as e:
            cprint(f"Error fetching markets: {e}", "red")

    def _refresh_feed_subscriptions(self, refreshed_markets: Dict[str, Dict]) -> None:
        """Refresh WebSocket subscriptions for updated markets."""
        if not ENABLE_WEBSOCKET_FEED:
            return

        try:
            def _collect_token_ids(markets_dict):
                out = set()
                for market in markets_dict.values():
                    for token in market.get("tokens", []):
                        if token.get("token_id"):
                            out.add(token["token_id"])
                    for tid in market.get("clobTokenIds", []) or []:
                        if isinstance(tid, str) and tid:
                            out.add(tid)
                        elif isinstance(tid, dict) and tid.get("token_id"):
                            out.add(tid["token_id"])
                return out

            current_tokens = _collect_token_ids(self.markets)
            new_tokens = _collect_token_ids(refreshed_markets)

            tokens_to_unsubscribe = list(current_tokens - new_tokens)
            tokens_to_subscribe = list(new_tokens - current_tokens)

            if tokens_to_unsubscribe:
                self.feed.unsubscribe(tokens_to_unsubscribe)
            if tokens_to_subscribe:
                self.feed.subscribe(tokens_to_subscribe)
        except Exception as e:
            cprint(f"WebSocket subscription refresh failed: {e}", "red")

    def _cancel_stale_shortterm_orders(self, refreshed_markets: Dict[str, Dict]) -> None:
        """Cancel active short-term orders that are no longer in the current market universe."""
        if not self.order_manager or not refreshed_markets:
            return

        current_token_ids = set()
        current_condition_ids = set(refreshed_markets.keys())
        current_market_slugs = set()

        for market in refreshed_markets.values():
            slug = str(market.get("slug") or market.get("market_slug") or market.get("event_slug") or "")
            if slug:
                current_market_slugs.add(slug)
            for token in market.get("tokens", []) or []:
                token_id = token.get("token_id")
                if token_id:
                    current_token_ids.add(str(token_id))
            for tid in market.get("clobTokenIds", []) or []:
                if isinstance(tid, str) and tid:
                    current_token_ids.add(tid)
                elif isinstance(tid, dict) and tid.get("token_id"):
                    current_token_ids.add(str(tid["token_id"]))

        cancelled = 0
        for order in list(self.order_manager.get_active_orders()):
            bucket = _shortterm_bucket_key(str(order.market_slug or ""))
            if not bucket:
                continue

            condition_id = str((order.metadata or {}).get("condition_id") or "")
            token_id = str(order.token_id or "")
            market_slug = str(order.market_slug or "")
            still_current = (
                (condition_id and condition_id in current_condition_ids)
                or (token_id and token_id in current_token_ids)
                or (market_slug and market_slug in current_market_slugs)
            )
            if still_current:
                continue

            result = self.order_manager.cancel_order(order.order_id, "Short-term market rolled to new cycle")
            if result.get("success"):
                cancelled += 1

        if cancelled:
            cprint(f"Cancelled {cancelled} stale short-term order(s) after market refresh", "yellow")

    def _shortterm_signal_stale_reason(self, signal) -> Optional[str]:
        """Return a human-readable reason when a short-term signal should be blocked."""
        market_slug = str(signal.market_slug or "")
        bucket = _shortterm_bucket_key(market_slug)
        horizon = _extract_horizon_from_bucket(bucket)
        if not bucket or not horizon:
            return None

        current_bucket_slugs = {
            str((market or {}).get("slug") or "")
            for market in self.markets.values()
            if _shortterm_bucket_key(_market_text_payload(market or {})) == bucket
        }
        current_bucket_slugs.discard("")

        end_ts = signal.metadata.get("end_date_ts") if isinstance(signal.metadata, dict) else None
        try:
            end_ts = float(end_ts) if end_ts not in (None, "") else None
        except (TypeError, ValueError):
            end_ts = None
        if end_ts is None:
            end_ts = _parse_market_end_ts({"slug": market_slug})
        if end_ts is None:
            return None

        now_ts = time.time()
        current_cycle_end_ts = _current_cycle_end_ts(now_ts, horizon)
        if current_cycle_end_ts is not None and abs(end_ts - current_cycle_end_ts) > 90:
            return "market is not in the current cycle"

        min_seconds_remaining = {
            "15m": ML_DIRECTIONAL_MIN_SECONDS_TO_EXPIRY_15M,
            "1h": ML_DIRECTIONAL_MIN_SECONDS_TO_EXPIRY_1H,
        }.get(horizon, 0)
        remaining = end_ts - now_ts
        if remaining < min_seconds_remaining:
            return f"only {max(int(remaining), 0)}s left (< {min_seconds_remaining}s minimum)"

        if current_bucket_slugs and market_slug not in current_bucket_slugs:
            return "market rolled before execution"
        return None

    def _cancel_out_of_cycle_shortterm_orders(self) -> None:
        """Cancel active short-term orders whose end time is not in the current live cycle."""
        if not self.order_manager:
            return

        now_ts = time.time()
        cycle_grace_sec = 90
        cancelled = 0

        for order in list(self.order_manager.get_active_orders()):
            bucket = _shortterm_bucket_key(str(order.market_slug or ""))
            horizon = _extract_horizon_from_bucket(bucket)
            if not bucket or not horizon:
                continue

            metadata = order.metadata or {}
            end_ts = metadata.get("end_date_ts")
            try:
                end_ts = float(end_ts) if end_ts not in (None, "") else None
            except (TypeError, ValueError):
                end_ts = None
            if end_ts is None:
                end_ts = _parse_market_end_ts({"slug": order.market_slug})
            if end_ts is None:
                continue

            current_cycle_end_ts = _current_cycle_end_ts(now_ts, horizon)
            if current_cycle_end_ts is None:
                continue

            if abs(end_ts - current_cycle_end_ts) <= cycle_grace_sec:
                continue

            result = self.order_manager.cancel_order(
                order.order_id,
                f"Short-term order outside current {bucket} cycle",
            )
            if result.get("success"):
                cancelled += 1

        if cancelled:
            cprint(f"Cancelled {cancelled} out-of-cycle short-term order(s)", "yellow")

    def _refresh_balance(self, force_log: bool = False) -> None:
        """Fetch latest balance and update risk manager."""
        try:
            diagnostics = self.client.get_balance_diagnostics()
            balance = diagnostics.get("balance")
            if balance is None:
                if force_log:
                    cprint(
                        f"WARNING: Balance unavailable from API (source={diagnostics.get('source', 'unknown')})",
                        "yellow",
                    )
                return

            self.risk_manager.set_balance(balance)
            self._last_balance_refresh_success_ts = time.time()
            if force_log:
                label = "PAPER" if PAPER_TRADING else "LIVE"
                cprint(f"{label} balance: ${balance:.2f}", "white")
                if not PAPER_TRADING and balance <= 0:
                    details = diagnostics.get("details", {}) or {}
                    proxy = str(details.get("proxy_address") or "")
                    proxy_short = f"{proxy[:6]}...{proxy[-4:]}" if len(proxy) >= 10 else proxy or "—"
                    cprint(
                        f"WARNING: Live balance resolved to $0.00 (source={diagnostics.get('source', 'unknown')}, proxy={proxy_short}, sig={details.get('signature_type', '—')})",
                        "yellow",
                    )
        except Exception as e:
            if force_log:
                cprint(f"WARNING: Balance refresh failed: {e}", "yellow")
        
    def _on_orderbook_update(self, update):
        """Handle orderbook updates from WebSocket."""
        pass
    
    def _on_feed_connect(self):
        """Handle WebSocket connection."""
        cprint("WebSocket connected", "green")
    
    def _on_feed_disconnect(self, reason: str):
        """Handle WebSocket disconnection."""
        cprint(f"WebSocket disconnected: {reason}", "yellow")
    
    def _check_for_fills(self):
        """Poll Polymarket for recent trades and detect fills."""
        try:
            trades = self.client.get_trades(limit=50)
            if not trades:
                return

            self._recent_trades_cache = trades

            for trade in trades:
                trade_id = trade.get("id") or trade.get("trade_id")
                if not trade_id:
                    aid = trade.get("asset_id") or trade.get("token_id") or trade.get("asset")
                    ts = trade.get("timestamp")
                    if aid and ts is not None:
                        trade_id = f"{aid}_{trade.get('side')}_{trade.get('price')}_{trade.get('size')}_{ts}"
                    else:
                        continue

                token_id = trade.get("asset_id") or trade.get("token_id") or trade.get("asset")
                side = (trade.get("side") or "").upper()
                price = float(trade.get("price", 0))
                size = float(trade.get("size", 0))
                trade_order_id = (
                    trade.get("order_id") or trade.get("orderID")
                    or trade.get("maker_order_id") or trade.get("makerOrderId")
                    or trade.get("taker_order_id") or trade.get("takerOrderId")
                )
                order = self._find_order_for_trade(token_id, side, price, size, trade_order_id)
                if order and order.is_active:
                    self._handle_fill_event(
                        order,
                        {
                            "trade_id": trade_id,
                            "side": order.side,
                            "price": price,
                            "size": size,
                            "token_id": token_id,
                            "fee_rate_bps": trade.get("fee_rate_bps"),
                        },
                    )
        except Exception as e:
            cprint(f"   ⚠️  Fill check error: {e}", "yellow")
    
    def _on_order_fill(self, order, fill_data: Dict):
        """Handle order fills."""
        fill_size = float(fill_data.get("size", order.size) or 0)
        fill_price = float(fill_data.get("price", order.price) or 0)
        fill_side = str(fill_data.get("side") or order.side or "").upper()
        self._last_fill_event_ts = time.time()
        self._last_positions_sync_ts = self._last_fill_event_ts
        executed_notional = fill_size * fill_price
        raw_fee_rate_bps = (
            fill_data.get("fee_rate_bps")
            if fill_data.get("fee_rate_bps") is not None
            else (order.metadata or {}).get("fee_rate_bps")
        )
        if raw_fee_rate_bps is not None:
            try:
                fee_rate = max(float(raw_fee_rate_bps), 0.0) / 10_000.0
            except (TypeError, ValueError):
                fee_rate = 0.0
        elif (order.metadata or {}).get("post_only"):
            fee_rate = 0.0
        else:
            fee_rate = None
        is_maker = bool((order.metadata or {}).get("post_only")) or str(
            fill_data.get("trader_side") or fill_data.get("liquidity") or ""
        ).lower() in ("maker", "m")
        fee_est = 0.0 if is_maker else estimate_taker_fee(fill_size, fill_price, fee_rate)
        outcome_side = (
            (order.metadata or {}).get("outcome_side")
            or (order.metadata or {}).get("side")
            or "YES"
        )

        self.risk_manager.update_position(
            token_id=order.token_id,
            market_slug=order.market_slug,
            side=str(outcome_side).upper(),
            size_delta=fill_size,
            price=fill_price,
            is_entry=(fill_side == "BUY"),
        )
        self.risk_manager.record_trade(executed_notional, fee_est)

        # Persist trade record
        if self.store:
            trade_id = fill_data.get("trade_id") or f"{order.order_id}_{int(time.time() * 1000)}"
            strategy_name = order.metadata.get("strategy", "unknown") if order.metadata else "unknown"
            trade_record = {
                "trade_id": trade_id,
                "order_id": order.order_id,
                "token_id": order.token_id,
                "market_slug": order.market_slug,
                "side": fill_side,
                "price": fill_price,
                "size": fill_size,
                "strategy": strategy_name,
                "traded_at": datetime.now().isoformat(),
            }
            try:
                self.store.save_trade(trade_record)
            except Exception as e:
                cprint(f"❌ Failed to persist trade {trade_id}: {e}", "red")
        
        # Record exit in analytics tracker
        strategy_name = order.metadata.get("strategy", "unknown") if order.metadata else "unknown"
        if fill_side == "SELL" and order.metadata and self.analytics_enabled and self.strategy_tracker:
            entry_price = order.metadata.get("entry_price") or order.price
            pnl = (fill_price - entry_price) * fill_size
            self.strategy_tracker.record_trade(
                strategy=strategy_name,
                token_id=fill_data.get("token_id", ""),
                market_slug=order.market_slug,
                side="SELL",
                price=fill_price,
                size=fill_size,
                pnl=pnl,
                fees=fee_est,
                is_exit=True,
            )
        self._recent_fills.appendleft(
            {
                "strategy": strategy_name,
                "market": order.market_slug,
                "side": fill_side,
                "price": fill_price,
                "size": fill_size,
                "timestamp": self._last_fill_event_ts,
            }
        )
        
        # Notify the correct strategy (not just the first one)
        strategy_name = order.metadata.get("strategy", "") if order.metadata else ""
        notified = False
        for strat in self.strategies:
            if strat.name == strategy_name:
                exit_action = strat.on_order_filled(order.order_id, fill_data)
                notified = True
                break
        
        # Fallback: notify first strategy if no match
        if not notified and self.strategy:
            exit_action = self.strategy.on_order_filled(order.order_id, fill_data)
        else:
            exit_action = None
        
        # Handle exit order if strategy requests one
        if exit_action and exit_action.get("action") == "place_exit":
            self.order_manager.place_limit_order(
                token_id=exit_action["token_id"],
                side=exit_action["side"],
                price=exit_action["price"],
                size=exit_action["size"],
                market_slug=order.market_slug,
                metadata={
                    "reason": exit_action.get("reason", "Exit order"),
                    "strategy": strategy_name,
                    "entry_price": order.price,
                    "outcome_side": (order.metadata or {}).get("outcome_side"),
                }
            )

    def _trade_already_processed(self, trade_id: Optional[str]) -> bool:
        if not trade_id:
            return False
        if not hasattr(self, "_processed_trades"):
            self._processed_trades = []
            self._processed_trade_ids = set()
        if trade_id in self._processed_trade_ids:
            return True
        self._processed_trades.append(trade_id)
        self._processed_trade_ids.add(trade_id)
        if len(self._processed_trades) > 500:
            stale_ids = self._processed_trades[:-200]
            self._processed_trades = self._processed_trades[-200:]
            for stale_id in stale_ids:
                self._processed_trade_ids.discard(stale_id)
        return False

    @staticmethod
    def _norm_id(oid: str) -> str:
        if not oid or not isinstance(oid, str):
            return ""
        return oid.lower().replace("0x", "").strip()

    def _find_order_for_trade(
        self,
        token_id: Optional[str],
        side: str,
        price: float,
        size: float,
        trade_order_id: Optional[str] = None,
    ):
        if not self.order_manager:
            return None

        if trade_order_id:
            order = self.order_manager.orders.get(trade_order_id)
            if order:
                return order
            norm = self._norm_id(trade_order_id)
            for oid, candidate in self.order_manager.orders.items():
                if self._norm_id(oid) == norm:
                    return candidate

        if not token_id or not side or price <= 0 or size <= 0:
            return None

        candidates = [
            order for order in self.order_manager.orders.values()
            if order.is_active
            and (order.token_id == token_id or self._norm_id(order.token_id or "") == self._norm_id(str(token_id or "")))
            and (order.side or "").upper() == side
            and abs(order.size - size) < 0.5
            and abs(order.price - price) < 0.02
        ]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            return min(candidates, key=lambda order: abs(order.size - size) + abs(order.price - price) * 10)
        return None

    def _handle_fill_event(self, order, fill_data: Dict):
        trade_id = fill_data.get("trade_id")
        if self._trade_already_processed(trade_id):
            return

        strategy_name = (order.metadata or {}).get("strategy", "unknown")
        side = str(fill_data.get("side") or order.side or "").upper()
        price = float(fill_data.get("price", order.price) or order.price)
        size = float(fill_data.get("size", order.size) or order.size)

        cprint(
            f"💰 FILL [{strategy_name.upper()}]: {side} {size:.2f} @ ${price:.3f} | {order.market_slug}",
            "green",
            attrs=["bold"],
        )
        self.order_manager.process_fill(order.order_id, fill_data)
        self._refresh_balance()
        self.telegram.alert_fill(
            strategy=strategy_name,
            side=side,
            price=price,
            size=size,
            market=order.market_slug,
        )

    def _on_user_trade_update(self, update):
        try:
            trade = update.raw
            trade_id = trade.get("id") or update.trade_id
            if not trade_id:
                return
            trade_status = str(
                trade.get("status")
                or trade.get("trade_status")
                or trade.get("state")
                or ""
            ).upper()
            if trade_status in {"FAILED", "RETRYING"}:
                return
            if trade_status and trade_status not in {"MINED", "CONFIRMED", "COMPLETED", "SUCCESS"}:
                return

            maker_orders = trade.get("maker_orders") or []
            for maker_order in maker_orders:
                order_id = maker_order.get("order_id")
                order = self._find_order_for_trade(
                    token_id=maker_order.get("asset_id") or trade.get("asset_id"),
                    side=str(maker_order.get("side") or ""),
                    price=float(maker_order.get("price", trade.get("price", 0)) or 0),
                    size=float(maker_order.get("matched_amount", maker_order.get("size", 0)) or 0),
                    trade_order_id=order_id,
                )
                if order and order.is_active:
                    self._handle_fill_event(
                        order,
                        {
                            "trade_id": trade_id,
                            "side": order.side,
                            "price": float(maker_order.get("price", trade.get("price", 0)) or 0),
                            "size": float(maker_order.get("matched_amount", maker_order.get("size", 0)) or 0),
                            "token_id": maker_order.get("asset_id") or trade.get("asset_id"),
                            "fee_rate_bps": maker_order.get("fee_rate_bps", trade.get("fee_rate_bps")),
                        },
                    )
                    return

            order = self._find_order_for_trade(
                token_id=trade.get("asset_id"),
                side=str(trade.get("side") or ""),
                price=float(trade.get("price", 0) or 0),
                size=float(trade.get("size", 0) or 0),
                trade_order_id=trade.get("taker_order_id") or trade.get("takerOrderId"),
            )
            if order and order.is_active:
                self._handle_fill_event(
                    order,
                    {
                        "trade_id": trade_id,
                        "side": order.side,
                        "price": float(trade.get("price", 0) or 0),
                        "size": float(trade.get("size", 0) or 0),
                        "token_id": trade.get("asset_id"),
                        "fee_rate_bps": trade.get("fee_rate_bps"),
                    },
                )
        except Exception as e:
            cprint(f"user trade update error: {e}", "yellow")

    def _on_user_order_update(self, update):
        try:
            if not self.order_manager:
                return
            order = self.order_manager.get_order(update.order_id)
            if not order or not order.is_active:
                return
            raw_status = str(update.raw.get("status") or update.status or "").upper()
            raw_type = str(update.raw.get("type") or "").upper()
            if raw_type == "PLACEMENT" or raw_status == "LIVE":
                order.status = OrderStatus.OPEN
                order.updated_at = datetime.now()
                self.order_manager._persist_order(order)
            if raw_type == "UPDATE":
                try:
                    size_matched = float(update.raw.get("size_matched", 0) or 0)
                except (TypeError, ValueError):
                    size_matched = 0.0
                if size_matched > 0:
                    order.filled_size = max(order.filled_size, size_matched)
                    # Order-channel size_matched is cumulative and may arrive before the
                    # trade event with the executable fill details. Keep the order active
                    # so the trade channel or REST sync can still drive risk/PnL updates.
                    order.status = OrderStatus.PARTIAL
                    order.updated_at = datetime.now()
                    if order.metadata is None:
                        order.metadata = {}
                    order.metadata["user_ws_size_matched"] = max(
                        float((order.metadata or {}).get("user_ws_size_matched", 0.0) or 0.0),
                        size_matched,
                    )
                    self.order_manager._persist_order(order)
            if raw_status in {"CANCELED", "CANCELLED"} or raw_type in {"CANCELLATION", "CANCEL"}:
                submitted_expiration = (order.metadata or {}).get("submitted_expiration") if order.metadata else None
                raw_reason = (
                    update.raw.get("reason")
                    or update.raw.get("cancel_reason")
                    or update.raw.get("message")
                    or update.raw.get("error")
                    or ""
                )
                detail_bits = [
                    f"type={raw_type or 'unknown'}",
                    f"status={raw_status or 'unknown'}",
                ]
                if submitted_expiration:
                    detail_bits.append(f"exp={submitted_expiration}")
                if raw_reason:
                    detail_bits.append(f"reason={raw_reason}")
                if update.raw.get("expiration"):
                    detail_bits.append(f"raw_exp={update.raw.get('expiration')}")
                if update.raw.get("order_type"):
                    detail_bits.append(f"raw_order_type={update.raw.get('order_type')}")
                cancel_reason = f"Exchange/user channel cancellation ({', '.join(detail_bits)})"
                self.order_manager.mark_order_cancelled(order.order_id, cancel_reason)
        except Exception as e:
            cprint(f"user order update error: {e}", "yellow")

    def _on_market_resolved(self, update):
        market = self.markets.get(update.condition_id)
        if market is None:
            return
        market["resolved"] = True
        market["winner"] = update.winning_outcome
        market["resolutionOutcome"] = update.winning_outcome

    def _on_tick_size_change(self, update):
        try:
            self.client.invalidate_market_metadata(update.token_id)
        except Exception as e:
            cprint(f"tick size cache invalidation error: {e}", "yellow")
    
    def _on_risk_level_change(self, level: RiskLevel, reason: str):
        """Forward risk level changes to Telegram."""
        self.telegram.alert_risk_event(level.value, reason)
        
        # Also check throttle changes
        throttle = self.risk_manager.get_throttle_factor()
        if throttle != self._last_alerted_throttle:
            if self.risk_manager.session_peak_balance > 0:
                dd = (self.risk_manager.session_peak_balance - self.risk_manager.current_balance) / self.risk_manager.session_peak_balance
            else:
                dd = 0
            self.telegram.alert_throttle(throttle, dd)
            self._last_alerted_throttle = throttle
    
    def _on_order_cancel(self, order, reason: str):
        """Handle order cancellations."""
        # Notify the correct strategy
        strategy_name = order.metadata.get("strategy", "") if order.metadata else ""
        for strat in self.strategies:
            if strat.name == strategy_name:
                strat.on_order_cancelled(order.order_id, reason)
                return
        # Fallback
        if self.strategy:
            self.strategy.on_order_cancelled(order.order_id, reason)
    
    def _print_final_stats(self):
        """Print final statistics."""
        cprint("\n" + "="*60, "cyan")
        cprint("📊 Final Statistics", "cyan", attrs=["bold"])
        cprint("="*60, "cyan")
        
        # Strategy stats (all strategies in multi-mode)
        total_signals = 0
        total_trades = 0
        total_pnl = 0.0
        
        for strategy in self.strategies:
            state = strategy.get_state()
            total_signals += state['signals_generated']
            total_trades += state['trades_executed']
            if self.analytics_enabled:
                total_pnl += state['pnl']
            
            cprint(f"\n  [{state['name'].upper()}]", "cyan")
            if self.analytics_enabled:
                cprint(f"    Signals: {state['signals_generated']} | Trades: {state['trades_executed']} | PnL: ${state['pnl']:.2f}", "white")
            else:
                cprint(f"    Signals: {state['signals_generated']} | Trades: {state['trades_executed']} | PnL: disabled", "white")
        
        if len(self.strategies) > 1:
            cprint(f"\n  TOTAL:", "green", attrs=["bold"])
            cprint(f"    Signals: {total_signals} | Trades: {total_trades}", "white")
            if self.analytics_enabled:
                pnl_color = "green" if total_pnl >= 0 else "red"
                cprint(f"    Combined PnL: ${total_pnl:.2f}", pnl_color)
        
        # Order manager stats
        if self.order_manager:
            om_stats = self.order_manager.get_stats()
            cprint(f"\n  Total Orders: {om_stats['total_placed']}", "white")
            cprint(f"  Filled: {om_stats['total_filled']}", "white")
            cprint(f"  Cancelled: {om_stats['total_cancelled']}", "white")
            cprint(f"  Fill Rate: {om_stats['fill_rate']*100:.1f}%", "white")
        
        # Risk manager stats
        self.risk_manager.print_status()
        
        cprint("="*60 + "\n", "cyan")


def main():
    """Entry point for the bot."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Polymarket Trading Bot")
    parser.add_argument(
        "--strategy", "-s",
        default="btc_5min",
        help=(
            "Strategy to use: stink_bid, late_money, cross_asset, "
            "terminal_convergence, orderbook_imbalance, wallet_copy, "
            "'btc_5min' (recommended), or 'all' (default: btc_5min)"
        )
    )
    parser.add_argument(
        "--list-strategies", "-l",
        action="store_true",
        help="List available strategies"
    )
    parser.add_argument(
        "--paper", "-p",
        action="store_true",
        help="Force paper trading mode"
    )
    
    args = parser.parse_args()
    
    if args.list_strategies:
        cprint("\n📋 Available Strategies:\n", "cyan", attrs=["bold"])
        for name, info in list_strategies().items():
            cprint(f"  • {name}", "green")
            cprint(f"    {info['description']}\n", "white")
        return
    
    # Create and run bot
    try:
        bot = PolymarketBot(strategy=args.strategy)
        bot.start()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        cprint(f"\n❌ Fatal error: {e}", "red")
        sys.exit(1)


if __name__ == "__main__":
    main()
