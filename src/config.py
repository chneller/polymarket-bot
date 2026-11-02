"""
Polymarket Micro-Spread Trading Bot - Configuration
"""

import os
import time
from pathlib import Path
from typing import List, Optional, Tuple
from dotenv import load_dotenv


def _getenv_nonempty(name: str, default: str) -> str:
    """Return env value unless it is unset or blank."""
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _parse_utc_hour_windows(raw: str) -> tuple[List[Tuple[int, int]], Optional[str]]:
    text = (raw or "").strip()
    if not text:
        return [], None

    windows: List[Tuple[int, int]] = []
    for chunk in text.split(","):
        part = chunk.strip()
        if not part:
            continue
        if "-" not in part:
            return [], f"Invalid UTC hour window '{part}' (expected start-end)"
        start_raw, end_raw = [item.strip() for item in part.split("-", 1)]
        try:
            start = int(start_raw)
            end = int(end_raw)
        except ValueError:
            return [], f"Invalid UTC hour window '{part}' (hours must be integers)"
        if start < 0 or start > 23:
            return [], f"Invalid UTC hour window '{part}' (start must be 0-23)"
        if end < 1 or end > 24:
            return [], f"Invalid UTC hour window '{part}' (end must be 1-24)"
        if start == end:
            return [], f"Invalid UTC hour window '{part}' (window must span at least 1 hour)"
        windows.append((start, end))

    return windows, None


def _parse_utc_allowed_days(raw: str) -> tuple[List[int], Optional[str]]:
    text = (raw or "").strip()
    if not text:
        return [], None

    day_map = {
        "mon": 0,
        "monday": 0,
        "tue": 1,
        "tues": 1,
        "tuesday": 1,
        "wed": 2,
        "wednesday": 2,
        "thu": 3,
        "thur": 3,
        "thurs": 3,
        "thursday": 3,
        "fri": 4,
        "friday": 4,
        "sat": 5,
        "saturday": 5,
        "sun": 6,
        "sunday": 6,
    }
    days: List[int] = []
    for chunk in text.split(","):
        part = chunk.strip().lower()
        if not part:
            continue
        if part not in day_map:
            return [], f"Invalid UTC day '{part}' (use sun,mon,tue,wed,thu,fri,sat)"
        day_value = day_map[part]
        if day_value not in days:
            days.append(day_value)
    return days, None


def _format_utc_hour_windows(windows: List[Tuple[int, int]]) -> str:
    if not windows:
        return "ALL"
    return ", ".join(f"{start:02d}:00-{end:02d}:00 UTC" for start, end in windows)


def _format_utc_allowed_days(days: List[int]) -> str:
    if not days:
        return "ALL"
    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return ", ".join(labels[day] for day in sorted(days))

# Load environment variables in two layers:
# 1) Optional shared non-secret config file (for collaboration/agents)
# 2) Private .env secrets file (overrides shared values)
PROJECT_ROOT = Path(__file__).parent.parent
PUBLIC_CONFIG_FILE = os.getenv(
    "BOT_PUBLIC_CONFIG_FILE",
    str(PROJECT_ROOT / "config" / "bot.public.env"),
)
ACTIVE_CONFIG_SOURCE_PATH = (os.getenv("BOT_CONFIG_SOURCE_PATH") or "").strip()
PRIVATE_CONFIG_FILE = str(PROJECT_ROOT / '.env')
PUBLIC_CONFIG_FILE_LOADED = False
if Path(PUBLIC_CONFIG_FILE).exists():
    load_dotenv(dotenv_path=PUBLIC_CONFIG_FILE, override=False)
    PUBLIC_CONFIG_FILE_LOADED = True
elif os.getenv("BOT_PUBLIC_CONFIG_FILE"):
    # User explicitly pointed to a shared config path that doesn't exist.
    # Log loudly to avoid silent fallback to defaults.
    print(f"⚠️  BOT_PUBLIC_CONFIG_FILE not found: {PUBLIC_CONFIG_FILE}")
# Keep shell/exported vars as highest priority (important for BOT_WALLET_ID=... runs).
load_dotenv(dotenv_path=PRIVATE_CONFIG_FILE, override=False)

# =============================================================================
# POLYMARKET API SETTINGS
# =============================================================================
CLOB_HOST = "https://clob.polymarket.com"
# Minimum order size in shares (Polymarket rejects below this)
POLYMARKET_MIN_ORDER_SIZE = float(os.getenv("POLYMARKET_MIN_ORDER_SIZE", "5"))
GAMMA_HOST = "https://gamma-api.polymarket.com"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CHAIN_ID = 137  # Polygon mainnet

# =============================================================================
# KALSHI API SETTINGS
# =============================================================================
KALSHI_BASE_URL = os.getenv("KALSHI_BASE_URL", "https://api.elections.kalshi.com")
KALSHI_TRADE_API_PATH = os.getenv("KALSHI_TRADE_API_PATH", "/trade-api/v2")
KALSHI_ACCESS_KEY = os.getenv("KALSHI_ACCESS_KEY", "")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "")
KALSHI_RATE_LIMIT_PER_SECOND = int(os.getenv("KALSHI_RATE_LIMIT_PER_SECOND", "5"))
KALSHI_ORDERBOOK_TTL_SECONDS = int(os.getenv("KALSHI_ORDERBOOK_TTL_SECONDS", "2"))
KALSHI_TRADING_ENABLED = os.getenv("KALSHI_TRADING_ENABLED", "false").lower() == "true"
KALSHI_MARKET_MAP_PATH = os.getenv(
    "KALSHI_MARKET_MAP_PATH",
    str(PROJECT_ROOT / "data" / "kalshi_market_map.json"),
)
KALSHI_MIN_PROFIT_CENTS = float(os.getenv("KALSHI_MIN_PROFIT_CENTS", "2"))

# =============================================================================
# POLYBACKTEST API (for backtesting)
# =============================================================================
POLYBACKTEST_API_KEY = os.getenv("POLYBACKTEST_API_KEY", "")
POLYBACKTEST_BASE_URL = os.getenv("POLYBACKTEST_BASE_URL", "https://api.polybacktest.com")

# =============================================================================
# BINANCE API SETTINGS
# =============================================================================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")
BINANCE_WS_URL = _getenv_nonempty("BINANCE_WS_URL", "wss://stream.binance.com:9443/ws")
BINANCE_WS_COMBINED_URL = _getenv_nonempty(
    "BINANCE_WS_COMBINED_URL", "wss://stream.binance.com:9443/stream"
)
BINANCE_REST_URL = _getenv_nonempty("BINANCE_REST_URL", "https://api.binance.com")
BINANCE_FUTURES_REST_URL = _getenv_nonempty("BINANCE_FUTURES_REST_URL", "https://fapi.binance.com")
BINANCE_FUTURES_WS_COMBINED_URL = _getenv_nonempty(
    "BINANCE_FUTURES_WS_COMBINED_URL", "wss://fstream.binance.com/stream"
)
BINANCE_SYMBOL = os.getenv("BINANCE_SYMBOL", "btcusdt")
# Comma-separated symbols for multi-asset feed (BTC, ETH, SOL, XRP). Single symbol = legacy mode.
BINANCE_SYMBOLS = [
    s.strip().lower()
    for s in os.getenv("BINANCE_SYMBOLS", "btcusdt,ethusdt,solusdt,xrpusdt").split(",")
    if s.strip()
]

# =============================================================================
# CREDENTIALS (from .env)
# =============================================================================
# Optional wallet profile selector for multi-wallet runs on same machine.
# Example:
#   BOT_WALLET_ID=ALPHA
#   POLYMARKET_PRIVATE_KEY_ALPHA=...
#   POLYMARKET_PROXY_ADDRESS_ALPHA=0x...
BOT_WALLET_ID = os.getenv("BOT_WALLET_ID", "").strip()
if BOT_WALLET_ID:
    _suffix = BOT_WALLET_ID.upper()
    PRIVATE_KEY = os.getenv(f"POLYMARKET_PRIVATE_KEY_{_suffix}", "") or os.getenv("POLYMARKET_PRIVATE_KEY", "")
    PROXY_ADDRESS = os.getenv(f"POLYMARKET_PROXY_ADDRESS_{_suffix}", "") or os.getenv("POLYMARKET_PROXY_ADDRESS", "")
    SIGNATURE_TYPE = int(
        _getenv_nonempty(f"SIGNATURE_TYPE_{_suffix}", _getenv_nonempty("SIGNATURE_TYPE", "2"))
    )
else:
    PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")
    SIGNATURE_TYPE = int(_getenv_nonempty("SIGNATURE_TYPE", "2"))

# Signature types (per Polymarket docs):
# 0 = EOA, 1 = POLY_PROXY (email/magic), 2 = GNOSIS_SAFE
POLYMARKET_BUILDER_CODE = os.getenv("POLYMARKET_BUILDER_CODE", "").strip()

# =============================================================================
# TRADING PARAMETERS
# =============================================================================
# Minimum spread in cents to consider profitable (after fees)
# Note: Most crypto markets have 0.1-1c spreads. Set to 1 for real trading.
MIN_SPREAD_CENTS = float(os.getenv("MIN_SPREAD_CENTS", "1"))

# Order size per trade in USD
ORDER_SIZE_USD = float(os.getenv("ORDER_SIZE_USD", "10"))

# Maximum position per market in USD
MAX_POSITION_USD = float(os.getenv("MAX_POSITION_USD", "100"))

# Maximum total exposure across all markets
MAX_TOTAL_EXPOSURE_USD = float(os.getenv("MAX_TOTAL_EXPOSURE_USD", "500"))

# =============================================================================
# RISK MANAGEMENT
# =============================================================================
# Daily loss limit - stop trading if reached
DAILY_LOSS_LIMIT_USD = float(os.getenv("DAILY_LOSS_LIMIT_USD", "50"))

# Daily profit target (optional)
DAILY_PROFIT_TARGET_USD = float(os.getenv("DAILY_PROFIT_TARGET_USD", "100"))

# Minimum balance to maintain
MIN_BALANCE_USD = float(os.getenv("MIN_BALANCE_USD", "50"))

# Maximum concurrent active orders
MAX_ACTIVE_ORDERS = int(os.getenv("MAX_ACTIVE_ORDERS", "20"))

# Order timeout - cancel orders older than this (seconds)
ORDER_TIMEOUT_SECONDS = int(os.getenv("ORDER_TIMEOUT_SECONDS", "300"))
ENTRY_WINDOW_HOURS_UTC, ENTRY_WINDOW_HOURS_UTC_ERROR = _parse_utc_hour_windows(
    os.getenv("ENTRY_WINDOW_HOURS_UTC", "")
)
ENTRY_ALLOWED_DAYS_UTC, ENTRY_ALLOWED_DAYS_UTC_ERROR = _parse_utc_allowed_days(
    os.getenv("ENTRY_ALLOWED_DAYS_UTC", "")
)

# =============================================================================
# MARKET FILTERS
# =============================================================================
# Only trade prices between these bounds (avoid near-resolution)
MIN_PRICE_CENTS = float(os.getenv("MIN_PRICE_CENTS", "5"))
MAX_PRICE_CENTS = float(os.getenv("MAX_PRICE_CENTS", "95"))

# Minimum 24h volume to consider market
MIN_VOLUME_USD = float(os.getenv("MIN_VOLUME_USD", "10000"))

# Only trade crypto markets (set to false for political/sports/all markets)
ONLY_CRYPTO_MARKETS = os.getenv("ONLY_CRYPTO_MARKETS", "false").lower() == "true"

# Spread strategy: restrict to crypto only (false = trade all markets in universe)
SPREAD_ONLY_CRYPTO_MARKETS = os.getenv("SPREAD_ONLY_CRYPTO_MARKETS", "false").lower() == "true"
# When true: only trade crypto short-term (5m/15m/1h/4h up/down), exclude MegaETH/long-dated
SPREAD_ONLY_SHORTTERM_CRYPTO = os.getenv("SPREAD_ONLY_SHORTTERM_CRYPTO", "false").lower() == "true"
# Spread log: false = one summary line per scan (TUI-friendly); true = per-signal detail
SPREAD_LOG_VERBOSE = os.getenv("SPREAD_LOG_VERBOSE", "false").lower() == "true"

# Keywords to identify crypto price prediction markets
CRYPTO_MARKET_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
    "xrp", "ripple", "crypto", "price"
]

# =============================================================================
# CRYPTO EVENT / BINANCE INFRA SETTINGS
# =============================================================================
# Enables Binance price-feed infra plus crypto event-market discovery used by
# short-term BTC strategies and ml_directional. Falls back to legacy ENABLE_BTC_5MIN.
ENABLE_CRYPTO_EVENT_INFRA = os.getenv(
    "ENABLE_CRYPTO_EVENT_INFRA",
    os.getenv("ENABLE_BTC_5MIN", "true"),
).lower() == "true"

# Block orders on markets that resolve in more than this many hours (0 = disabled)
MAX_HOURS_TO_EXPIRY = float(os.getenv("MAX_HOURS_TO_EXPIRY", "1"))
# Keep only nearest short-term crypto cycle buckets (5m/15m/1h/4h per asset)
SHORTTERM_NEAREST_CYCLE_ONLY = os.getenv("SHORTTERM_NEAREST_CYCLE_ONLY", "true").lower() == "true"
# Cap for nearest short-term buckets in hours (0 = use MAX_HOURS_TO_EXPIRY)
SHORTTERM_MAX_HOURS_AHEAD = float(os.getenv("SHORTTERM_MAX_HOURS_AHEAD", "0"))

# Keywords to identify short-term crypto markets (5m, 15m, 1h, 4h)
# Event slugs: btc-updown-5m-*, sol-updown-15m-*, ethereum-up-or-down-*
# Market titles: "Bitcoin Up or Down - 5 min", "- 15 min", "- 1 hour"
BTC_5MIN_KEYWORDS = [
    # Event slug patterns (from /events): btc-updown-5m, ethereum-up-or-down
    "updown", "up-or-down", "5m", "15m", "1h", "4h",
    # Exact Polymarket phrasing (from market titles)
    "up or down - 5 min",
    "up or down - 15 min",
    "up or down - 1 hour",
    "up or down - 1h",
    "up or down - 4 hour",
    "up or down - 4h",
    # Fallback patterns
    "5 min", "5-min", "5min", "5-minute", "5 minute",
    "15 min", "15-min", "15min",
    "1 hour", "4 hour", "4h", "4-hour",
    "up or down",
]

# Cross-asset latency thresholds
# Minimum BTC price move (%) on Binance to trigger a signal
# 0.05% ≈ $50 on $100k BTC — fires often enough for 5-min markets
BTC_MIN_MOVE_PCT = float(os.getenv("BTC_MIN_MOVE_PCT", "0.05"))
# Reaction window — seconds after Binance move to trade Polymarket
BTC_REACTION_WINDOW_SECONDS = float(os.getenv("BTC_REACTION_WINDOW_SECONDS", "10"))
# Minimum confidence to trade cross-asset signal
BTC_MIN_CONFIDENCE = float(os.getenv("BTC_MIN_CONFIDENCE", "0.52"))

# Terminal convergence — seconds before expiry to start trading
TERMINAL_CONVERGENCE_WINDOW_SECONDS = int(os.getenv("TERMINAL_CONVERGENCE_WINDOW_SECONDS", "60"))
# Minimum mispricing (cents) to trigger terminal convergence
TERMINAL_MIN_EDGE_CENTS = float(os.getenv("TERMINAL_MIN_EDGE_CENTS", "3"))
# Restrict to 1h Up/Down only (Binance = resolution source; 5m/15m/4h use Chainlink)
TERMINAL_CONVERGENCE_1H_ONLY = os.getenv("TERMINAL_CONVERGENCE_1H_ONLY", "true").lower() == "true"

# Keywords for 1h-only mode (Polymarket 1h Up/Down event slugs and titles)
# e.g. "Bitcoin Up or Down - March 4, 1PM ET", "Hourly Crypto", "btc-updown-1h-..."
TERMINAL_1H_KEYWORDS = [
    "1h", "1 hour", "hourly",
    "up or down - 1 hour", "up or down - 1h", "updown-1h",
]
# Keywords that indicate NOT 1h (5m/15m/4h) — exclude when matching "up or down"
TERMINAL_NON_1H_KEYWORDS = ["5m", "15m", "4h", "5 min", "15 min", "4 hour", "updown-5m", "updown-15m", "updown-4h"]

# Orderbook imbalance — minimum bid/ask volume ratio to signal
ORDERBOOK_IMBALANCE_RATIO = float(os.getenv("ORDERBOOK_IMBALANCE_RATIO", "2.5"))

# =============================================================================
# KELLY CRITERION SETTINGS
# =============================================================================
# Kelly fraction mode: "full", "half", "quarter"
KELLY_FRACTION_MODE = os.getenv("KELLY_FRACTION_MODE", "half")
# Maximum Kelly bet as fraction of bankroll (safety cap)
KELLY_MAX_BET_FRACTION = float(os.getenv("KELLY_MAX_BET_FRACTION", "0.05"))
# Minimum edge required before Kelly sizes a bet (below this → skip)
KELLY_MIN_EDGE = float(os.getenv("KELLY_MIN_EDGE", "0.01"))
ML_DIRECTIONAL_KELLY_ENABLED = os.getenv("ML_DIRECTIONAL_KELLY_ENABLED", "true").lower() == "true"

# =============================================================================
# bs-p NATIVE ENGINE (Avellaneda-Stoikov quoting + analytics)
# =============================================================================
# Master kill-switch: set to false to force pure-Python fallback
NATIVE_ENGINE_ENABLED = os.getenv("NATIVE_ENGINE_ENABLED", "true").lower() == "true"
# Path to libpmkernel.dylib / .so (auto-discovered if unset)
PMKERNEL_LIB_PATH = os.getenv("PMKERNEL_LIB_PATH", "")
# Risk aversion — higher = wider spreads, safer.  Start high for small bankrolls.
QUOTING_GAMMA = float(os.getenv("QUOTING_GAMMA", "1.0"))
# Liquidity / order-arrival parameter.  Higher = tighter spreads.
QUOTING_K = float(os.getenv("QUOTING_K", "2.0"))
# Default tau when market end_date is unknown (fraction of a day)
QUOTING_TAU_DEFAULT = float(os.getenv("QUOTING_TAU_DEFAULT", "0.05"))
# Greeks alert thresholds
GREEKS_DELTA_ALERT = float(os.getenv("GREEKS_DELTA_ALERT", "0.3"))
GREEKS_GAMMA_ALERT = float(os.getenv("GREEKS_GAMMA_ALERT", "0.2"))

# =============================================================================
# VOLATILITY / MARKET MAKING REGIME
# =============================================================================
# Rolling window for volatility calculation (seconds)
VOL_WINDOW_SECONDS = int(os.getenv("VOL_WINDOW_SECONDS", "300"))
# High-vol threshold (annualized σ above this = widen spreads)
VOL_HIGH_THRESHOLD = float(os.getenv("VOL_HIGH_THRESHOLD", "0.80"))
# Low-vol threshold (below this = tighten spreads)
VOL_LOW_THRESHOLD = float(os.getenv("VOL_LOW_THRESHOLD", "0.30"))
# Spread multiplier in high-vol regime
VOL_HIGH_SPREAD_MULT = float(os.getenv("VOL_HIGH_SPREAD_MULT", "1.5"))
# Spread multiplier in low-vol regime
VOL_LOW_SPREAD_MULT = float(os.getenv("VOL_LOW_SPREAD_MULT", "0.7"))

# =============================================================================
# TELEGRAM ALERTS
# =============================================================================
# Create a bot via @BotFather, then get chat_id from /getUpdates
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ALERTS_ENABLED = os.getenv("TELEGRAM_ALERTS_ENABLED", "true").lower() == "true"

# =============================================================================
# STRATEGY SELECTION
# =============================================================================
# Comma-separated list of strategies to DISABLE (won't load even in "all" mode)
# Default: disable money-losing / capital-inefficient strategies for small bankrolls
DISABLED_STRATEGIES = [
    s.strip()
    for s in os.getenv("DISABLED_STRATEGIES", "spread,favorite_longshot,cross_platform_arbitrage").split(",")
    if s.strip()
]

# Enable adaptive (bankroll-proportional) risk management
ADAPTIVE_RISK_ENABLED = os.getenv("ADAPTIVE_RISK_ENABLED", "true").lower() == "true"
ADAPTIVE_MAX_POSITION_PCT = float(os.getenv("ADAPTIVE_MAX_POSITION_PCT", "0.12"))
ADAPTIVE_MAX_EXPOSURE_PCT = float(os.getenv("ADAPTIVE_MAX_EXPOSURE_PCT", "0.30"))
ADAPTIVE_MAX_SINGLE_TRADE_PCT = float(os.getenv("ADAPTIVE_MAX_SINGLE_TRADE_PCT", "0.04"))
ADAPTIVE_DAILY_LOSS_LIMIT_PCT = float(os.getenv("ADAPTIVE_DAILY_LOSS_LIMIT_PCT", "0.06"))
ADAPTIVE_DRAWDOWN_THROTTLE_PCT = float(os.getenv("ADAPTIVE_DRAWDOWN_THROTTLE_PCT", "0.05"))
ADAPTIVE_DRAWDOWN_HALT_PCT = float(os.getenv("ADAPTIVE_DRAWDOWN_HALT_PCT", "0.12"))
ADAPTIVE_MIN_BALANCE_FLOOR_PCT = float(os.getenv("ADAPTIVE_MIN_BALANCE_FLOOR_PCT", "0.70"))

# =============================================================================
# VPIN (Volume-Synchronized Probability of Informed Trading)
# =============================================================================
VPIN_THRESHOLD = float(os.getenv("VPIN_THRESHOLD", "0.60"))
VPIN_BUCKET_SECONDS = int(os.getenv("VPIN_BUCKET_SECONDS", "15"))
VPIN_BUCKET_COUNT = int(os.getenv("VPIN_BUCKET_COUNT", "20"))
VPIN_MIN_VOLUME_USD = float(os.getenv("VPIN_MIN_VOLUME_USD", "50"))
VPIN_COOLDOWN_SECONDS = int(os.getenv("VPIN_COOLDOWN_SECONDS", "120"))

# =============================================================================
# SENTIMENT (CryptoPanic news feed)
# =============================================================================
# Optional: CryptoPanic API key for premium feed (free tier works without it)
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")
SENTIMENT_THRESHOLD = float(os.getenv("SENTIMENT_THRESHOLD", "0.40"))
SENTIMENT_POLL_INTERVAL = int(os.getenv("SENTIMENT_POLL_INTERVAL", "60"))
SENTIMENT_COOLDOWN = int(os.getenv("SENTIMENT_COOLDOWN", "300"))

# =============================================================================
# COMBINATORIAL ARBITRAGE
# =============================================================================
COMBO_MIN_EDGE_CENTS = int(os.getenv("COMBO_MIN_EDGE_CENTS", "3"))
COMBO_COOLDOWN = int(os.getenv("COMBO_COOLDOWN", "300"))
# Edge-based size scaling (Kroer et al. 2016): larger violation → larger bet
COMBO_EDGE_SIZE_FACTOR = float(os.getenv("COMBO_EDGE_SIZE_FACTOR", "0.5"))
COMBO_EDGE_SIZE_CAP = float(os.getenv("COMBO_EDGE_SIZE_CAP", "2.0"))

# =============================================================================
# WALLET COPY (Track best traders)
# =============================================================================
# Comma-separated proxy wallet addresses to copy (manual list)
TRACKED_WALLETS = [
    w.strip() for w in os.getenv("TRACKED_WALLETS", "").split(",") if w.strip()
]
# Auto-fetch top N traders from leaderboard (PNL-ranked)
WALLET_COPY_USE_LEADERBOARD = os.getenv("WALLET_COPY_USE_LEADERBOARD", "true").lower() == "true"
WALLET_COPY_LEADERBOARD_TOP_N = int(os.getenv("WALLET_COPY_LEADERBOARD_TOP_N", "5"))
WALLET_COPY_LEADERBOARD_CATEGORY = os.getenv("WALLET_COPY_LEADERBOARD_CATEGORY", "CRYPTO")
WALLET_COPY_LEADERBOARD_PERIOD = os.getenv("WALLET_COPY_LEADERBOARD_PERIOD", "MONTH")
# Copy parameters
WALLET_COPY_SIZE_USD = float(os.getenv("WALLET_COPY_SIZE_USD", str(ORDER_SIZE_USD)))
WALLET_COPY_SIZE_MULTIPLIER = float(os.getenv("WALLET_COPY_SIZE_MULTIPLIER", "1.0"))
WALLET_COPY_MAX_DELAY_SECONDS = int(os.getenv("WALLET_COPY_MAX_DELAY_SECONDS", "120"))
WALLET_COPY_MIN_TRADE_USD = float(os.getenv("WALLET_COPY_MIN_TRADE_USD", "10"))
WALLET_COPY_CRYPTO_ONLY = os.getenv("WALLET_COPY_CRYPTO_ONLY", "true").lower() == "true"
WALLET_COPY_COOLDOWN_SECONDS = int(os.getenv("WALLET_COPY_COOLDOWN_SECONDS", "60"))
WALLET_COPY_MIN_WALLET_POLL_SECONDS = float(os.getenv("WALLET_COPY_MIN_WALLET_POLL_SECONDS", "2.0"))

# Blocked wallets: never track or copy (hedge/MM/volume-farmer)
# Comma-separated env override + data/blocked_wallets.txt (one address per line)
WALLET_COPY_BLOCKED_WALLETS_ENV = [
    w.strip().lower()
    for w in os.getenv("WALLET_COPY_BLOCKED_WALLETS", "").split(",")
    if w.strip()
]
_BLOCKED_WALLETS_FILE = Path(PROJECT_ROOT) / "data" / "blocked_wallets.txt"


def _load_blocked_wallets() -> list[str]:
    """Merge env + file into normalized (lowercase) blocked list."""
    out = set(WALLET_COPY_BLOCKED_WALLETS_ENV)
    if _BLOCKED_WALLETS_FILE.exists():
        try:
            with open(_BLOCKED_WALLETS_FILE) as f:
                for line in f:
                    line = line.split("#")[0].strip()
                    if line and line.startswith("0x"):
                        out.add(line.lower())
        except OSError:
            pass
    return list(out)


WALLET_COPY_BLOCKED_WALLETS = _load_blocked_wallets()

# Wallet rotation (auto-replace inactive tracked wallets)
WALLET_ROTATION_ENABLED = os.getenv("WALLET_ROTATION_ENABLED", "false").lower() == "true"
WALLET_ROTATION_REFRESH_INTERVAL_SECONDS = float(os.getenv("WALLET_ROTATION_REFRESH_INTERVAL_SECONDS", "600"))
WALLET_ROTATION_INACTIVITY_THRESHOLD_HOURS = float(os.getenv("WALLET_ROTATION_INACTIVITY_THRESHOLD_HOURS", "48"))
WALLET_ROTATION_MAX_REPLACEMENTS_PER_CYCLE = int(os.getenv("WALLET_ROTATION_MAX_REPLACEMENTS_PER_CYCLE", "2"))
WALLET_ROTATION_MIN_TRACKED_WALLETS = int(os.getenv("WALLET_ROTATION_MIN_TRACKED_WALLETS", "1"))
WALLET_ROTATION_CANDIDATE_POOL_SIZE = int(os.getenv("WALLET_ROTATION_CANDIDATE_POOL_SIZE", "30"))
WALLET_ROTATION_MIN_TRADES = int(os.getenv("WALLET_ROTATION_MIN_TRADES", "20"))
WALLET_ROTATION_MIN_CRYPTO_PCT = float(os.getenv("WALLET_ROTATION_MIN_CRYPTO_PCT", "70"))
WALLET_ROTATION_MIN_SHORTTERM_PCT = float(os.getenv("WALLET_ROTATION_MIN_SHORTTERM_PCT", "40"))
WALLET_ROTATION_MAX_DAYS_SINCE_LAST_TRADE = float(os.getenv("WALLET_ROTATION_MAX_DAYS_SINCE_LAST_TRADE", "7"))
WALLET_ROTATION_MIN_TRADES_PER_DAY = float(os.getenv("WALLET_ROTATION_MIN_TRADES_PER_DAY", "0.5"))
WALLET_ROTATION_REMOVED_COOLDOWN_HOURS = float(os.getenv("WALLET_ROTATION_REMOVED_COOLDOWN_HOURS", "24"))

# =============================================================================
# BOT BEHAVIOR
# =============================================================================
# How often to scan for opportunities (seconds)
SCAN_INTERVAL_SECONDS = float(os.getenv("SCAN_INTERVAL_SECONDS", "5"))

# How often to refresh market universe (seconds)
MARKET_REFRESH_SECONDS = int(os.getenv("MARKET_REFRESH_SECONDS", "300"))

# How often to refresh account balance (seconds)
BALANCE_REFRESH_SECONDS = int(os.getenv("BALANCE_REFRESH_SECONDS", "60"))

# How often to reconcile active orders + positions against the exchange (seconds).
# Catches missed fills and corrects drifted exposure.
POSITIONS_SYNC_SECONDS = int(os.getenv("POSITIONS_SYNC_SECONDS", "120"))

# Paper trading mode (no real orders)
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"

# Paper trading balance (used when PAPER_TRADING=true)
PAPER_BALANCE_USD = float(os.getenv("PAPER_BALANCE_USD", "1000"))

# Enable WebSocket feed for orderbook updates (recommended: true for real-time data)
ENABLE_WEBSOCKET_FEED = os.getenv("ENABLE_WEBSOCKET_FEED", "true").lower() == "true"
# Enable authenticated user WebSocket for real-time order/fill updates in live mode
ENABLE_USER_WEBSOCKET_FEED = os.getenv("ENABLE_USER_WEBSOCKET_FEED", "true").lower() == "true"

# Log level
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# =============================================================================
# RETRY / BACKOFF SETTINGS
# =============================================================================
RETRY_MAX_ATTEMPTS = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
RETRY_BASE_DELAY_SECONDS = float(os.getenv("RETRY_BASE_DELAY_SECONDS", "0.5"))
RETRY_MAX_DELAY_SECONDS = float(os.getenv("RETRY_MAX_DELAY_SECONDS", "5"))

# Auto-refresh CLOB allowance to prevent live trading stalls
AUTO_ALLOWANCE_REFRESH_ENABLED = os.getenv("AUTO_ALLOWANCE_REFRESH_ENABLED", "true").lower() == "true"
ALLOWANCE_REFRESH_SECONDS = int(os.getenv("ALLOWANCE_REFRESH_SECONDS", "900"))
ALLOWANCE_DIAGNOSTICS_ENABLED = os.getenv("ALLOWANCE_DIAGNOSTICS_ENABLED", "false").lower() == "true"
# CLOB session heartbeat
CLOB_HEARTBEAT_ENABLED = os.getenv("CLOB_HEARTBEAT_ENABLED", "true").lower() == "true"
CLOB_HEARTBEAT_INTERVAL_SECONDS = float(os.getenv("CLOB_HEARTBEAT_INTERVAL_SECONDS", "8"))
# Bound read calls so slow endpoints don't stall scan loop
TRADE_FETCH_TIMEOUT_SECONDS = float(os.getenv("TRADE_FETCH_TIMEOUT_SECONDS", "7"))
BALANCE_FETCH_TIMEOUT_SECONDS = float(os.getenv("BALANCE_FETCH_TIMEOUT_SECONDS", "7"))
# Safety: block BUY entries if balance refresh is stale
BALANCE_STALE_BLOCK_BUYS = os.getenv("BALANCE_STALE_BLOCK_BUYS", "true").lower() == "true"
BALANCE_STALE_MAX_SECONDS = int(os.getenv("BALANCE_STALE_MAX_SECONDS", "240"))

# Disable analytics/P&L tracking to reduce runtime overhead
ENABLE_STRATEGY_ANALYTICS = os.getenv("ENABLE_STRATEGY_ANALYTICS", "true").lower() == "true"

# TUI: filter Activity log by keywords (comma-separated). Empty = show all.
DASHBOARD_LOG_FILTER = [k.strip() for k in os.getenv("DASHBOARD_LOG_FILTER", "").split(",") if k.strip()]

# =============================================================================
# RATE LIMITS (Polymarket CLOB)
# =============================================================================
# POST /order: 240/sec burst, 40/sec sustained
ORDER_RATE_LIMIT_BURST = 240
ORDER_RATE_LIMIT_SUSTAINED = 40

# General endpoints: lower limits
GENERAL_RATE_LIMIT = 100

# =============================================================================
# FEE STRUCTURE
# =============================================================================
# Taker fees follow Polymarket's formula: fee = shares * feeRate * p * (1 - p)
# (see src/fees.py, https://docs.polymarket.com/trading/fees). Per-market rates come
# from Gamma's feeSchedule; fallback per category via FEE_RATE_<CATEGORY> env vars,
# default category FEE_DEFAULT_CATEGORY=crypto (0.07).
# TRADING_FEE_RATE is DEPRECATED: if set explicitly it is used as a legacy flat
# fraction of notional. Unset, it holds the crypto worst-case-at-p=0.5 equivalent
# (0.035 of notional) for code that still needs a single scalar.
TRADING_FEE_RATE = float(os.getenv("TRADING_FEE_RATE", "0.035"))

# Maker fee (round-trip): 0 = Polymarket makers pay zero fees (2026+). Used by spread strategy.
MAKER_FEE_RATE = float(os.getenv("MAKER_FEE_RATE", "0"))

# GTD: API requires expiration unix well after "now" (~60s security threshold); pad for clock skew.
CLOB_GTD_MIN_LEAD_SECONDS = int(os.getenv("CLOB_GTD_MIN_LEAD_SECONDS", "90"))

# Minimum profit margin after fees to execute trade
MIN_PROFIT_MARGIN = float(os.getenv("MIN_PROFIT_MARGIN", "0.005"))  # 0.5%

# =============================================================================
# ML DIRECTIONAL STRATEGY
# =============================================================================
ML_DIRECTIONAL_ENABLED = os.getenv("ML_DIRECTIONAL_ENABLED", "true").lower() == "true"
ML_DIRECTIONAL_ENABLED_HORIZONS = [
    value.strip().lower()
    for value in os.getenv("ML_DIRECTIONAL_ENABLED_HORIZONS", "15m,1h").split(",")
    if value.strip()
]
ML_DIRECTIONAL_MIN_PROBABILITY = float(os.getenv("ML_DIRECTIONAL_MIN_PROBABILITY", "0.53"))
ML_DIRECTIONAL_MIN_EDGE = float(os.getenv("ML_DIRECTIONAL_MIN_EDGE", "0.03"))
ML_DIRECTIONAL_MAKER_OFFSET = float(os.getenv("ML_DIRECTIONAL_MAKER_OFFSET", "0.005"))
# Post-only limits must rest below the ask; disable to allow taker when the book moves (you pay taker fees).
ML_DIRECTIONAL_POST_ONLY = os.getenv("ML_DIRECTIONAL_POST_ONLY", "true").lower() == "true"
# How many ticks below best_ask to place post-only buys (safety against sub-second book drift).
ML_DIRECTIONAL_POST_ONLY_BUFFER_TICKS = max(1, int(os.getenv("ML_DIRECTIONAL_POST_ONLY_BUFFER_TICKS", "2")))
ML_DIRECTIONAL_SIGNAL_COOLDOWN_SECONDS = int(os.getenv("ML_DIRECTIONAL_SIGNAL_COOLDOWN_SECONDS", "45"))
ML_DIRECTIONAL_MAX_SIGNALS_PER_CYCLE = int(os.getenv("ML_DIRECTIONAL_MAX_SIGNALS_PER_CYCLE", "2"))
ML_DIRECTIONAL_GTD_MAX_HORIZON_SECONDS = int(os.getenv("ML_DIRECTIONAL_GTD_MAX_HORIZON_SECONDS", "240"))
ML_DIRECTIONAL_ATR_HALT_PERCENTILE = float(os.getenv("ML_DIRECTIONAL_ATR_HALT_PERCENTILE", "0.95"))
ML_DIRECTIONAL_FEED_STALE_SECONDS = int(os.getenv("ML_DIRECTIONAL_FEED_STALE_SECONDS", "10"))
ML_DIRECTIONAL_MIN_SECONDS_TO_EXPIRY_15M = int(os.getenv("ML_DIRECTIONAL_MIN_SECONDS_TO_EXPIRY_15M", "60"))
ML_DIRECTIONAL_MIN_SECONDS_TO_EXPIRY_1H = int(os.getenv("ML_DIRECTIONAL_MIN_SECONDS_TO_EXPIRY_1H", "180"))
ML_DIRECTIONAL_ROLLING_ACCURACY_WINDOW = int(os.getenv("ML_DIRECTIONAL_ROLLING_ACCURACY_WINDOW", "100"))
ML_DIRECTIONAL_MIN_ROLLING_ACCURACY = float(os.getenv("ML_DIRECTIONAL_MIN_ROLLING_ACCURACY", "0.51"))
ML_DIRECTIONAL_BRIER_WINDOW = int(os.getenv("ML_DIRECTIONAL_BRIER_WINDOW", "50"))
ML_DIRECTIONAL_MAX_ROLLING_BRIER = float(os.getenv("ML_DIRECTIONAL_MAX_ROLLING_BRIER", "0.26"))
ML_DIRECTIONAL_SOFT_LOSS_STREAK = int(os.getenv("ML_DIRECTIONAL_SOFT_LOSS_STREAK", "7"))
ML_DIRECTIONAL_SOFT_PAUSE_SECONDS = int(os.getenv("ML_DIRECTIONAL_SOFT_PAUSE_SECONDS", str(2 * 3600)))
ML_DIRECTIONAL_HARD_LOSS_STREAK = int(os.getenv("ML_DIRECTIONAL_HARD_LOSS_STREAK", "10"))
ML_DIRECTIONAL_HARD_PAUSE_SECONDS = int(os.getenv("ML_DIRECTIONAL_HARD_PAUSE_SECONDS", str(24 * 3600)))
ML_DIRECTIONAL_ONLY_CRYPTO = os.getenv("ML_DIRECTIONAL_ONLY_CRYPTO", "true").lower() == "true"
ML_DIRECTIONAL_LEAN_MODE = os.getenv("ML_DIRECTIONAL_LEAN_MODE", "false").lower() == "true"
ML_DIRECTIONAL_LEAN_ASSETS = [
    value.strip().lower()
    for value in os.getenv("ML_DIRECTIONAL_LEAN_ASSETS", "btc").split(",")
    if value.strip()
]
ML_DIRECTIONAL_LEAN_HORIZONS = [
    value.strip().lower()
    for value in os.getenv("ML_DIRECTIONAL_LEAN_HORIZONS", "15m,1h").split(",")
    if value.strip()
]
ML_DIRECTIONAL_LEAN_EVENTS_ONLY = os.getenv("ML_DIRECTIONAL_LEAN_EVENTS_ONLY", "true").lower() == "true"
ML_DIRECTIONAL_LEAN_BINANCE_SYMBOLS = [
    value.strip().lower()
    for value in os.getenv("ML_DIRECTIONAL_LEAN_BINANCE_SYMBOLS", "btcusdt").split(",")
    if value.strip()
]

# =============================================================================
# DATA PATHS
# =============================================================================
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
ML_DATA_DIR = DATA_DIR / "ml"
ML_OHLC_DIR = ML_DATA_DIR / "ohlc"
ML_ARTIFACTS_DIR = ML_DATA_DIR / "artifacts"
ML_COLLECTORS_DIR = ML_DATA_DIR / "collectors"
ML_DIRECTIONAL_MODEL_PATH = Path(
    os.getenv("ML_DIRECTIONAL_MODEL_PATH", str(ML_ARTIFACTS_DIR / "ml_directional_latest.pkl"))
)
ML_DIRECTIONAL_MODEL_PATH_15M = Path(
    os.getenv("ML_DIRECTIONAL_MODEL_PATH_15M", str(ML_DIRECTIONAL_MODEL_PATH))
)
ML_DIRECTIONAL_MODEL_PATH_1H = Path(
    os.getenv("ML_DIRECTIONAL_MODEL_PATH_1H", str(ML_DIRECTIONAL_MODEL_PATH))
)
ML_BINANCE_COLLECTOR_DB = Path(
    os.getenv("ML_BINANCE_COLLECTOR_DB", str(ML_COLLECTORS_DIR / "binance_microstructure.sqlite"))
)
ML_COLLECTOR_DEPTH_LEVELS = int(os.getenv("ML_COLLECTOR_DEPTH_LEVELS", "20"))
ML_COLLECTOR_REST_POLL_SECONDS = int(os.getenv("ML_COLLECTOR_REST_POLL_SECONDS", "60"))

# SQLite database path for persisted bot state
_state_mode = "paper" if PAPER_TRADING else "live"
_default_db_name = (
    f"bot_state_{_state_mode}_{BOT_WALLET_ID.lower()}.sqlite"
    if BOT_WALLET_ID
    else f"bot_state_{_state_mode}.sqlite"
)
BOT_STATE_DB = Path(os.getenv("BOT_STATE_DB", str(DATA_DIR / _default_db_name)))

# Backtest data directory and DB
BACKTEST_DIR = DATA_DIR / "backtest"
BACKTEST_DB = Path(os.getenv("BACKTEST_DB", str(BACKTEST_DIR / "polybacktest.db")))

# Create directories if they don't exist
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
ML_DATA_DIR.mkdir(exist_ok=True)
ML_OHLC_DIR.mkdir(exist_ok=True)
ML_ARTIFACTS_DIR.mkdir(exist_ok=True)
ML_COLLECTORS_DIR.mkdir(exist_ok=True)


def clob_gtd_expiration_unix(
    end_date_ts: Optional[float],
    *,
    max_horizon_sec: float = 120.0,
    before_resolution_sec: float = 15.0,
) -> Optional[int]:
    """
    Unix timestamp for Polymarket GTD orders.

    The API rejects expirations inside ~60s of "now". If the market resolves before
    we can satisfy that lead time (while staying before_resolution_sec ahead of end),
    return None and use GTC instead.
    """
    now = time.time()
    min_exp = now + float(CLOB_GTD_MIN_LEAD_SECONDS)
    if end_date_ts is not None:
        cap = float(end_date_ts) - before_resolution_sec
        raw = min(cap, now + max_horizon_sec)
    else:
        cap = float("inf")
        raw = now + max_horizon_sec
    candidate = max(raw, min_exp)
    if end_date_ts is not None and candidate > cap + 1e-9:
        return None
    return int(candidate)


def validate_config():
    """Validate required configuration is present"""
    errors = []
    
    if not PRIVATE_KEY:
        errors.append("POLYMARKET_PRIVATE_KEY not set in .env")
    
    if not PROXY_ADDRESS:
        errors.append("POLYMARKET_PROXY_ADDRESS not set in .env")
    
    if MIN_SPREAD_CENTS < 0.1:
        errors.append("MIN_SPREAD_CENTS should be at least 0.1 cent")
    
    if ORDER_SIZE_USD < 1:
        errors.append("ORDER_SIZE_USD should be at least $1")
    
    if ENTRY_WINDOW_HOURS_UTC_ERROR:
        errors.append(ENTRY_WINDOW_HOURS_UTC_ERROR)

    if ENTRY_ALLOWED_DAYS_UTC_ERROR:
        errors.append(ENTRY_ALLOWED_DAYS_UTC_ERROR)

    return errors


def print_config():
    """Print current configuration (hiding sensitive data)"""
    from .logging_utils import cprint
    
    cprint("\n" + "="*60, "cyan")
    cprint("Bot Configuration", "cyan", attrs=["bold"])
    cprint("="*60, "cyan")
    if ACTIVE_CONFIG_SOURCE_PATH:
        cprint(f"  Active Config Source: {ACTIVE_CONFIG_SOURCE_PATH}", "white")
    cprint(
        f"  Public Config File: {PUBLIC_CONFIG_FILE if PUBLIC_CONFIG_FILE_LOADED else f'{PUBLIC_CONFIG_FILE} (not loaded)'}",
        "white",
    )
    cprint(f"  Private Config File: {PRIVATE_CONFIG_FILE}", "white")
    
    cprint(f"  Private Key: {'*' * 10}...{PRIVATE_KEY[-4:] if PRIVATE_KEY else 'NOT SET'}", "yellow")
    cprint(f"  Proxy Address: {PROXY_ADDRESS[:10]}...{PROXY_ADDRESS[-4:] if PROXY_ADDRESS else 'NOT SET'}", "yellow")
    cprint(f"  Signature Type: {SIGNATURE_TYPE} (Browser Wallet)", "white")
    
    cprint("\nTrading Parameters:", "cyan")
    cprint(f"  Min Spread: {MIN_SPREAD_CENTS}¢", "white")
    cprint(f"  Order Size: ${ORDER_SIZE_USD}", "white")
    cprint(f"  Max Position/Market: ${MAX_POSITION_USD}", "white")
    cprint(f"  Max Total Exposure: ${MAX_TOTAL_EXPOSURE_USD}", "white")
    
    cprint("\nRisk Management:", "cyan")
    cprint(f"  Daily Loss Limit: ${DAILY_LOSS_LIMIT_USD}", "white")
    cprint(f"  Min Balance: ${MIN_BALANCE_USD}", "white")
    cprint(f"  Max Active Orders: {MAX_ACTIVE_ORDERS}", "white")
    cprint(f"  Order Timeout: {ORDER_TIMEOUT_SECONDS}s", "white")
    cprint(f"  Entry Days: {_format_utc_allowed_days(ENTRY_ALLOWED_DAYS_UTC)}", "white")
    cprint(f"  Entry Window: {_format_utc_hour_windows(ENTRY_WINDOW_HOURS_UTC)}", "white")

    cprint("\nBot Settings:", "cyan")
    cprint(f"  Paper Trading: {'ON' if PAPER_TRADING else 'OFF (LIVE)'}", "green" if PAPER_TRADING else "red")
    cprint(f"  State DB: {BOT_STATE_DB}", "white")
    cprint(f"  Scan Interval: {SCAN_INTERVAL_SECONDS}s", "white")
    cprint(f"  Markets: {'Crypto only' if ONLY_CRYPTO_MARKETS else 'ALL markets (political, sports, crypto)'}", "cyan" if not ONLY_CRYPTO_MARKETS else "white")
    cprint(f"  Log Level: {LOG_LEVEL}", "white")
    
    if ENABLE_CRYPTO_EVENT_INFRA:
        cprint("\nCrypto Event / Binance Settings:", "cyan")
        cprint(f"  Min BTC Move: {BTC_MIN_MOVE_PCT}%", "white")
        cprint(f"  Reaction Window: {BTC_REACTION_WINDOW_SECONDS}s", "white")
        cprint(f"  Terminal Window: {TERMINAL_CONVERGENCE_WINDOW_SECONDS}s", "white")
        cprint(f"  Kelly Mode: {KELLY_FRACTION_MODE}", "white")
        cprint(f"  Binance Feed: {'API key set' if BINANCE_API_KEY else 'Public (no auth)'}", "white")
    
    if ML_DIRECTIONAL_LEAN_MODE:
        cprint("\nML Lean Mode:", "cyan")
        cprint(f"  Assets: {', '.join(ML_DIRECTIONAL_LEAN_ASSETS)}", "white")
        cprint(f"  Horizons: {', '.join(ML_DIRECTIONAL_LEAN_HORIZONS)}", "white")
        cprint(f"  Events Only: {'ON' if ML_DIRECTIONAL_LEAN_EVENTS_ONLY else 'OFF'}", "white")
        cprint(f"  Binance Symbols: {', '.join(ML_DIRECTIONAL_LEAN_BINANCE_SYMBOLS)}", "white")
        cprint(f"  ML Directional GTD Horizon: {ML_DIRECTIONAL_GTD_MAX_HORIZON_SECONDS}s", "white")
        cprint(
            f"  Min Entry Time Left: 15m={ML_DIRECTIONAL_MIN_SECONDS_TO_EXPIRY_15M}s, "
            f"1h={ML_DIRECTIONAL_MIN_SECONDS_TO_EXPIRY_1H}s",
            "white",
        )

    cprint("="*60 + "\n", "cyan")
