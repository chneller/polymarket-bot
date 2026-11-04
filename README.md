# Polymarket Trading Bot

[![Alpha](https://img.shields.io/badge/phase-alpha-orange)](https://github.com/chneller/polymarket-bot)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-3776ab?logo=python&logoColor=white)](https://python.org)
[![Polymarket](https://img.shields.io/badge/Polymarket-CLOB-6366f1)](https://polymarket.com)
[![Binance](https://img.shields.io/badge/Binance-WebSocket-f0b90b?logo=binance)](https://binance.com)
[![bs-p](https://img.shields.io/badge/bs--p-Native-8b5cf6)](https://github.com/lubluniky/bs-p)
[![SQLite](https://img.shields.io/badge/SQLite-3-003b57?logo=sqlite&logoColor=white)](https://sqlite.org)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-26a5e4?logo=telegram&logoColor=white)](https://telegram.org)

---

An open-source algorithmic trading framework for [Polymarket](https://polymarket.com) prediction markets, focused on short-horizon crypto Up/Down markets. It uses the [bs-p](https://github.com/lubluniky/bs-p) native math engine for dynamic sizing, Greeks and risk, and ships with live trading, paper trading, backtesting, wallet copy, telegram bot and an offline ML pipeline.

> **Disclaimer:** This is experimental software provided for educational and research purposes. It is **not financial advice**. Trading prediction markets involves substantial risk of loss; you use this software **entirely at your own risk**. Check that prediction-market trading is legal where you live before using it.

## Features

- **bs-p Native Engine** — C math library (Avellaneda-Stoikov quoting, inventory-aware Kelly, portfolio Greeks, shock testing) loaded via ctypes with automatic pure-Python fallback
- **13 Trading Strategies** — from wallet-copy to ML-driven directional edge
- **Binance BTC Feed** — real-time VWAP, volatility, and price velocity for 5-min BTC markets
- **Adaptive Risk Manager** — bankroll-proportional limits, drawdown throttling, portfolio Greeks monitoring, pre-trade shock testing, auto-halt
- **Authenticated User WebSocket** — near-real-time order and trade lifecycle updates in live mode, with REST sync fallback
- **Multi-Wallet Profiles** — run several wallets concurrently from one shared config file (e.g. `BOT_WALLET_ID=WALLET_A`, `WALLET_B`)
- **Strategy Analytics** — per-strategy P&L, Sharpe ratio, win rate, streaks, auto-disable losers
- **Telegram Alerts** — fills, risk events, Greeks threshold alerts, daily summaries
- **Inventory-Aware Kelly Sizing** — position sizes shrink as existing inventory grows, preventing over-concentration
- **Paper Trading** — full simulation mode, zero risk
- **Order Lifecycle** — tracking, duplicate prevention, stale cleanup, graceful shutdown cancellation
- **Live TUI Execution View** — execution health, open orders, recent fills, and live open-position unrealized PnL
- **Wallet Analysis** — reverse-engineer tracked wallets to infer strategies (markets, sizing, horizons)
- **Backtesting** — PolyBackTest API integration for historical 5m/15m Up/Down markets; replay strategies like terminal_convergence
- **Offline ML Pipeline** — `src/ml/` package for OHLCV loading, feature engineering (ATR, momentum, EMA/SMA distances/slopes, RSI, volume z-score), walk-forward training with LightGBM, artifact export, and dedicated replay backtests
- **Multi-Artifact ML** — 6 trained artifacts across 15m/1h/4h/1d horizons (OHLC-only + microstructure-overlap variants); `15m_ohlc_full` is the primary live candidate

## Quick Start

### 1. Set Up Virtual Environment

```bash
cd polymarket-bot
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

`requirements.txt` pins **[py-clob-client-v2](https://pypi.org/project/py-clob-client-v2/)** for Polymarket’s CLOB API. The older `py-clob-client` (v1) package is not used. Behavior (auth flow, `PartialCreateOrderOptions`, batch `PostOrdersV2Args`, `OrderPayload` cancels) is summarized in **[docs/clob-v2-sdk.md](docs/clob-v2-sdk.md)**.

### 2. Build the Native Math Engine (Optional)

```bash
./scripts/build_native.sh
```

This compiles `libpmkernel` from the vendored C source in `c_src/` and installs it to `lib/`.
Requires only a C compiler (`cc` / `clang` / `gcc`). If the library isn't found at runtime,
all functions fall back to pure-Python automatically.

Verify:

```bash
./venv/bin/python -c "from src.native.pmkernel import NATIVE_AVAILABLE; print(NATIVE_AVAILABLE)"
# True
```

### 3. Configure Environment

```bash
cp env.example .env
```

Edit `.env` with your credentials:

| Variable | Required | Description |
|----------|----------|-------------|
| `POLYMARKET_PRIVATE_KEY` | Yes | Wallet private key (MetaMask, Binance Web3, etc.) |
| `POLYMARKET_PROXY_ADDRESS` | Yes | Polymarket proxy wallet (found on profile page) |
| `BINANCE_API_KEY` | No | For cross-asset BTC strategies |
| `BINANCE_SECRET_KEY` | No | For cross-asset BTC strategies |
| `POLYBACKTEST_API_KEY` | No | For backtesting (see [Backtesting](docs/backtesting/README.md)) |
| `TELEGRAM_BOT_TOKEN` | No | For push notifications (see Telegram Setup) |
| `TELEGRAM_CHAT_ID` | No | Your Telegram chat ID |

Optional (recommended for multi-wallet):

- Use one shared non-secret config file via `BOT_PUBLIC_CONFIG_FILE`.
- Keep per-wallet secrets in `.env`:
  - `POLYMARKET_PRIVATE_KEY_<ID>`
  - `POLYMARKET_PROXY_ADDRESS_<ID>`
  - `SIGNATURE_TYPE_<ID>`

### 4. Run the Bot

```bash
# Paper test with recommended BTC 5-min strategies (default)
./venv/bin/python -m src.bot

# Run specific strategy
./venv/bin/python -m src.bot --strategy cross_asset

# Create local (git-ignored) profiles from the template first:
#   cp config/settings.ml_live.example.env config/settings.ml_paper.env
#   cp config/settings.ml_live.example.env config/settings.ml_live.env
# Run the ML directional strategy (paper, recommended first)
BOT_PUBLIC_CONFIG_FILE=./config/settings.ml_paper.env BOT_WALLET_ID=WALLET_A \
  ./.venv/bin/python -m src.bot --strategy ml_directional

# Run ML directional live (after paper validation)
BOT_PUBLIC_CONFIG_FILE=./config/settings.ml_live.env BOT_WALLET_ID=WALLET_A \
  ./.venv/bin/python -m src.bot --strategy ml_directional

# Run all non-disabled strategies
./venv/bin/python -m src.bot --strategy all

# List available strategies
./venv/bin/python -m src.bot --list-strategies

# Force paper mode
./venv/bin/python -m src.bot --paper

# bs-p spread + OBI with native engine (paper first)
BOT_PUBLIC_CONFIG_FILE=./config/settings.wallet_a.live BOT_WALLET_ID=WALLET_A PAPER_TRADING=true ./venv/bin/python -m src.bot --strategy all

# Multi-wallet live (two processes)
BOT_PUBLIC_CONFIG_FILE=./config/settings.wallet_a.live BOT_WALLET_ID=WALLET_A ./venv/bin/python -m src.bot --strategy all
BOT_PUBLIC_CONFIG_FILE=./config/settings.wallet_b.live BOT_WALLET_ID=WALLET_B ./venv/bin/python -m src.bot --strategy all
```

On macOS you can prefix long-running commands with `caffeinate -i` to prevent idle sleep.

## Configuration

Configuration can be layered:

1. shared non-secret file via `BOT_PUBLIC_CONFIG_FILE` (optional)
2. private `.env` (secrets)
3. shell env vars (highest priority)

Key settings:

### Trading Parameters

| Setting | Default | Description |
|---------|---------|-------------|
| `PAPER_TRADING` | `true` | Set `false` for live trading |
| `ORDER_SIZE_USD` | `10` | Base order size (adaptive risk may override) |
| `MAX_POSITION_USD` | `100` | Max position per market |
| `DAILY_LOSS_LIMIT_USD` | `50` | Circuit breaker |
| `SCAN_INTERVAL_SECONDS` | `5` | Time between strategy scans |

### bs-p Native Engine

| Setting | Default | Description |
|---------|---------|-------------|
| `NATIVE_ENGINE_ENABLED` | `true` | Master kill-switch (false = pure-Python fallback) |
| `PMKERNEL_LIB_PATH` | auto | Path to `libpmkernel.dylib/.so` |
| `QUOTING_GAMMA` | `1.0` | Risk aversion (higher = wider spreads, safer) |
| `QUOTING_K` | `2.0` | Liquidity/arrival rate (higher = tighter) |
| `QUOTING_TAU_DEFAULT` | `0.05` | Default time-to-resolution (fraction of day) |
| `GREEKS_DELTA_ALERT` | `0.3` | Telegram alert if `|net_delta|` exceeds this |
| `GREEKS_GAMMA_ALERT` | `0.2` | Telegram alert if `|net_gamma|` exceeds this |

### Adaptive Risk (Phase 3)

When `ADAPTIVE_RISK_ENABLED=true`, limits scale with current balance and drawdown state:

| Metric | % of Bankroll | Example (~$300 bankroll) |
|--------|---------------|-----------|
| Max per trade | 3% | ~$9.12 |
| Max per market | 12% | ~$36.48 |
| Max total exposure | 30% | ~$91.20 |
| Daily loss limit | 6% | ~$18.24 |
| Drawdown throttle | configurable | Reduces size progressively |
| Drawdown halt | configurable | Full stop |
| Balance floor | 70% | ~$212.80 |

Related controls:
- `ADAPTIVE_MAX_POSITION_PCT`
- `ADAPTIVE_MAX_EXPOSURE_PCT`
- `ADAPTIVE_MAX_SINGLE_TRADE_PCT`
- `ADAPTIVE_DAILY_LOSS_LIMIT_PCT`
- `ADAPTIVE_DRAWDOWN_THROTTLE_PCT`
- `ADAPTIVE_DRAWDOWN_HALT_PCT`
- `ADAPTIVE_MIN_BALANCE_FLOOR_PCT`
- `BALANCE_STALE_BLOCK_BUYS` / `BALANCE_STALE_MAX_SECONDS` (safety gate when balance refresh is stale)

### Strategy Selection

| Setting | Default | Description |
|---------|---------|-------------|
| `DISABLED_STRATEGIES` | `spread,arbitrage,favorite_longshot,cross_platform_arbitrage` | Comma-separated disabled list |
| `ADAPTIVE_RISK_ENABLED` | `true` | Bankroll-proportional risk limits |

### ML Directional

The `ml_directional` strategy is split into:

- **Offline research/training** — `src/ml/data_loader.py`, `src/ml/features.py`, `src/ml/train.py`, `src/ml/backtest.py`
- **Live execution** — `src/strategies/ml_directional_strategy.py`
- **Data top-up** — `scripts/topup_btc_ohlc.py` (hybrid Binance bulk + REST tail for all timeframes)
- **Training CLI** — `scripts/train_ml_directional.py`

#### Trained Artifacts

Model artifacts (`.pkl`) and BTC OHLC CSVs are **not shipped** in this repository. Download the data and train locally with the commands below; artifacts are written to `data/ml/artifacts/` (git-ignored) and the strategy loads `ML_DIRECTIONAL_MODEL_PATH` (default `data/ml/artifacts/ml_directional_latest.pkl`).

Typical artifact names: `ml_directional_15m_ohlc_full.pkl`, `ml_directional_1h_ohlc_full.pkl`, `ml_directional_4h_ohlc_full.pkl`, `ml_directional_1d_ohlc_full.pkl`, `ml_directional_15m_micro_overlap.pkl`. Always validate your own models with walk-forward backtests and paper trading; past results do not guarantee future performance.

**Current live candidates:** `15m_ohlc_full` (primary) → `1h_ohlc_full` (secondary).  
Recent-window 1h challengers were trained with the same 1h OHLC feature recipe and walk-forward defaults, but neither beat `1h_ohlc_full` on accuracy, Brier, net EV/trade, or profit factor.  
Microstructure artifacts are intentionally blocked from live deployment until `micro_*` runtime parity is built.

#### Feature Stack (OHLC-only models)

Each training row is built from multi-timeframe OHLCV candles (15m + 1h + 4h + 1d for the 15m target):

- **Price features** — returns, VWAP, log-price, breakout distance from rolling high/low
- **Momentum** — rolling momentum over multiple windows
- **Volatility** — ATR (Average True Range), realized volatility
- **Trend** — EMA/SMA distances from price, EMA/SMA slopes
- **Oscillators** — RSI
- **Volume** — volume z-score
- **Higher-timeframe context** — same feature set from 1h, 4h, 1d candles merged into each 15m row

Live inference reconstructs the same schema from `BinanceFeed.get_recent_ohlcv()` + `FeatureBuilder.build_training_schema_runtime_row()` — no feature gap between training and runtime.

#### Training Commands

```bash
# Download (first run) or top up BTC OHLC data from Binance (all timeframes)
pip install binance-historical-data
./.venv/bin/python scripts/topup_btc_ohlc.py

# Train 15m full-history artifact (primary)
./.venv/bin/python -m scripts.train_ml_directional --target-timeframe 15m \
  --artifact-path data/ml/artifacts/ml_directional_15m_ohlc_full.pkl

# Train 1h full-history artifact
./.venv/bin/python -m scripts.train_ml_directional --target-timeframe 1h \
  --artifact-path data/ml/artifacts/ml_directional_1h_ohlc_full.pkl

# Train with microstructure (only when collector has 2-4 weeks of data)
./.venv/bin/python -m scripts.train_ml_directional --target-timeframe 15m \
  --include-microstructure \
  --microstructure-db data/ml/collectors/binance_microstructure.sqlite \
  --artifact-path data/ml/artifacts/ml_directional_15m_micro_overlap.pkl
```

The trainer uses **walk-forward expanding windows** with a 6-month minimum train window and a 2-row embargo between folds (leakage prevention).

#### Key Config Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `ML_DIRECTIONAL_ENABLED` | `false` | Master switch for the strategy |
| `ML_DIRECTIONAL_MODEL_PATH_15M` | — | Artifact path for 15m markets |
| `ML_DIRECTIONAL_MODEL_PATH_1H` | — | Artifact path for 1h markets |
| `ML_DIRECTIONAL_ENABLED_HORIZONS` | `15m,1h` | Polymarket horizons the strategy trades |
| `ML_DIRECTIONAL_MIN_PROBABILITY` | `0.53` | Minimum model confidence to emit a signal |
| `ML_DIRECTIONAL_MIN_EDGE` | `0.03` | Minimum model-vs-market edge after friction |
| `ML_DIRECTIONAL_MAKER_OFFSET` | `0.005` | Resting bid improvement for maker-first fills |
| `ML_DIRECTIONAL_SIGNAL_COOLDOWN_SECONDS` | `45` | Cooldown between signals on the same market |
| `ML_DIRECTIONAL_ROLLING_ACCURACY_WINDOW` | `100` | Rolling window for live accuracy monitoring |
| `ML_DIRECTIONAL_MIN_ROLLING_ACCURACY` | `0.51` | Auto-halt if rolling accuracy drops below this |
| `ML_DIRECTIONAL_BRIER_WINDOW` | `50` | Window for rolling Brier score monitoring |
| `ML_DIRECTIONAL_MAX_ROLLING_BRIER` | `0.26` | Auto-halt if Brier score exceeds this |
| `ML_DIRECTIONAL_LEAN_MODE` | `false` | Restrict scanning to BTC crypto markets only |

#### Promotion Gates (before live capital)

1. Collect ≥200 resolved paper trades
2. Rolling accuracy ≥53% on 15m, ≥52% on 1h
3. Net EV > 0 after frictions
4. No 50-trade window below 48% accuracy

See [ML Directional Edge docs](docs/strategies/ml-directional-edge/README.md) and [Implementation Checklist](docs/strategies/ml-directional-edge/IMPLEMENTATION_CHECKLIST.md).

## Strategies

### Active Strategies

#### 1. Cross-Asset Latency Arbitrage ✅ `cross_asset`
**Primary edge.** Binance BTC price moves hit Polymarket 5-min markets with multi-second lag:
- Real-time Binance BTC/USDT VWAP + volatility
- Detects price moves before Polymarket reprices
- Trades the lag for consistent small profits
- Requires: `BINANCE_API_KEY` (or works with public feed)

#### 2. Terminal Convergence ✅ `terminal_convergence`
Buy near-certain outcomes in the last 60 seconds before expiry:
- 85%+ win rate on markets already trading at extreme prices
- Small edge per trade, high frequency on 5-min markets
- Minimal capital at risk per position

#### 3. Spread Farming ✅ `spread` (bs-p Avellaneda-Stoikov)
**Primary edge with bs-p.** Theoretically optimal bid/ask quoting that accounts for inventory, volatility, and market depth:
- `calculate_quotes_logit` computes inventory-aware optimal bid/ask (prices skew when you hold a position)
- Implied belief volatility bootstrapped from observed market spreads on startup
- Risk aversion (`gamma`) scales inversely with bankroll growth
- Falls back to manual bid+improvement logic when native engine unavailable
- Requires: bs-p native engine built (`./scripts/build_native.sh`)

#### 4. Orderbook Imbalance ✅ `orderbook_imbalance` (bs-p enhanced)
Detect bid/ask pressure with native microstructure analysis:
- bs-p `order_book_microstructure_batch` computes proper OBI, VWAP mid, and directional pressure from real orderbook depth
- Confirms direction with Binance BTC momentum
- VWAP mid-price replaces naive `(bid+ask)/2` for more accurate reference
- Falls back to spread-asymmetry heuristic when orderbook data unavailable
- Requires: `ENABLE_WEBSOCKET_FEED=true` for real orderbook data

#### 4. Stink Bid ✅ `stink_bid`
Passive asymmetric bets — $1 bids for potential 100x:
- Places 1¢ limit bids on high-volume markets
- Fills on panic sells and fat-finger trades
- Perfect for small accounts — high optionality, low capital

#### 5. Wallet Copy ✅ `wallet_copy`
Copy trades from top Polymarket traders:
- Tracks wallets from leaderboard (by PnL) or manual list
- Polls their trades via Data API, copies new BUYs with configurable size
- Filters: crypto-only, min trade size, max copy delay, per-wallet poll throttle
- `TRACKED_WALLETS` format: comma-separated addresses (`0xabc...,0xdef...`)
- **Wallet rotation** (optional): auto-replaces inactive tracked wallets with scored leaderboard candidates; persists to DB across restarts. See [Wallet Copy docs](docs/strategies/wallet-copy/README.md).
- Run: `./venv/bin/python -m src.bot --strategy wallet_copy`

**Analyze wallets before copying** — use the analysis script to infer a wallet's strategy (BTC vs multi-crypto, 5m vs 15m, avg size, etc.):

```bash
python scripts/analyze_wallets.py
# Custom wallets:
TRACKED_WALLETS=0xabc...,0xdef... python scripts/analyze_wallets.py
# If you hit proxy errors:
unset http_proxy https_proxy; python scripts/analyze_wallets.py
```

**Reverse-engineer top wallets** — deep analysis: price distribution, size vs conviction, outcome preference (UP vs DOWN), inferred playbook:

```bash
TRACKED_WALLETS=0xabc...,0xdef... python scripts/reverse_engineer_wallets.py
```

#### 6. ML Directional Edge ✅ `ml_directional`
Model-driven directional trading for 15m and 1h BTC Up/Down markets:
- Offline pipeline in `src/ml/` loads OHLCV data, engineers features (ATR, momentum, EMA/SMA, RSI, volume z-score), trains LightGBM via walk-forward expanding windows, and exports `.pkl` artifacts
- **6 trained artifacts** covering 15m/1h/4h/1d horizons (OHLC-only and microstructure-overlap variants)
- **Current primary candidate:** `ml_directional_15m_ohlc_full.pkl` (accuracy 0.720, net EV/trade 0.479, profit factor 2.85)
- Live strategy loads artifact, reconstructs training-schema features from Binance REST OHLCV, and emits maker-first signals
- Runtime feature parity: `BinanceFeed.get_recent_ohlcv()` + `FeatureBuilder.build_training_schema_runtime_row()` — no training/inference gap
- Safety: rolling accuracy/Brier halts, feed-staleness halts, per-market signal cooldowns
- **Lean mode** (`ML_DIRECTIONAL_LEAN_MODE=true`) restricts scanning to BTC-only crypto markets
- Lean mode also narrows Binance symbols at feed construction time, and can be reused by single-strategy `terminal_convergence` runs for faster startup
- Profile configs: `config/settings.ml_paper.env` (paper), `config/settings.ml_live.env` (live)
- Docs: [ML Directional Edge](docs/strategies/ml-directional-edge/README.md)
- Checklist: [Implementation Checklist](docs/strategies/ml-directional-edge/IMPLEMENTATION_CHECKLIST.md)

#### 7. Late Money ✅ `late_money`
Follow informed traders near expiration:
- Tracks price velocity in final hours
- 40% of volume occurs in last minute (more informed)
- Follows sharp late moves with momentum

### Advanced Strategies (Phase 5)

#### 6. VPIN / Smart Money ✅ `vpin`
Detect and follow informed trader flow using volume imbalance:
- Buckets trades into time windows, classifies buyer/seller-initiated
- Computes VPIN (Volume-Synchronized Probability of Informed Trading)
- When VPIN spikes → someone with information is positioning → follow them
- Based on Easley, López de Prado & O'Hara (2012)

#### 7. Sentiment ✅ `sentiment`
Trade news sentiment divergences vs market price:
- Polls CryptoPanic for real-time crypto headlines
- Keyword-based sentiment scoring (bullish/bearish lexicon)
- Matches news to Polymarket markets by asset
- Trades when sentiment diverges from current price (2-10 min lag)
- Optional: `CRYPTOPANIC_API_KEY` for premium feed

#### 8. Combinatorial Arbitrage ✅ `combinatorial_arb`
Exploit logical pricing violations across related markets:
- Parses market questions to extract thresholds ("BTC > $100k")
- Groups by asset + direction, checks monotonicity constraints
- P(BTC > $100k) must be ≤ P(BTC > $90k) — violations = free money
- $40M+ extracted from Polymarket via this edge (Milionis et al. 2024)

### Backtesting

Download historical Polymarket Up/Down data and backtest strategies (e.g. terminal_convergence):

```bash
# 1. Add POLYBACKTEST_API_KEY to .env (get key at polybacktest.com)
# 2. Download data (free plan: 50×5m, 50×15m, 24×1h, 24×4h, 5×24h markets)
./venv/bin/python -m scripts.download_polybacktest --types 5m,15m

# 3. Run backtest
./venv/bin/python -m scripts.run_backtest --strategy terminal_convergence --market-type 5m
```

See [docs/backtesting/](docs/backtesting/) for full documentation.

### Strategy Modes

```bash
# Recommended: BTC 5-min + advanced strategies (default)
./venv/bin/python -m src.bot --strategy btc_5min

# Just cross-asset latency arb
./venv/bin/python -m src.bot --strategy cross_asset

# Just VPIN smart money detection
./venv/bin/python -m src.bot --strategy vpin

# Just sentiment-driven trading
./venv/bin/python -m src.bot --strategy sentiment

# All non-disabled strategies (12 total, 8 active)
./venv/bin/python -m src.bot --strategy all
```

## Wallet-Copy Troubleshooting

- **`not enough balance / allowance` while TUI balance looks high**:
  - This usually means low **spendable collateral**, not low total portfolio value.
  - Enable `ALLOWANCE_DIAGNOSTICS_ENABLED=true` to log allowance snapshots.
- **`AttributeError ... tick_size` during live order placement**:
  - The client builds typed `PartialCreateOrderOptions` (`tick_size`, `neg_risk`) for **py-clob-client-v2**.
  - After upgrading the SDK, confirm `create_and_post_order` / `PartialCreateOrderOptions` still match upstream; see [docs/clob-v2-sdk.md](docs/clob-v2-sdk.md).
- **Repeated 401 on fill checks**:
  - Credential refresh is automatic, but network/latency can still slow loops.
  - Tune `TRADE_FETCH_TIMEOUT_SECONDS` and `BALANCE_FETCH_TIMEOUT_SECONDS`.
- **User WebSocket JSON parse noise (`Expecting value`)**:
  - The user stream now ignores blank / heartbeat frames such as `PING`, `PONG`, and `{}`.
  - If repeated parse errors continue, capture the raw frame payload before changing trading logic.
- **Adaptive risk with stale balance**:
  - With `BALANCE_STALE_BLOCK_BUYS=true`, new BUYs are blocked if balance is stale too long.
  - This prevents oversizing on outdated balance data.

## Telegram Alerts

Get push notifications for every fill, risk event, and daily summary.

### Setup (2 minutes)

1. Open Telegram → message `@BotFather` → `/newbot` → copy the **bot token**
2. Start a chat with your new bot (send it any message)
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
4. Find `"chat":{"id":123456789}` in the JSON — that's your **chat ID**
5. Add to `.env`:

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=123456789
TELEGRAM_ALERTS_ENABLED=true
```

### What You'll Receive

- 🟢 **Fill alerts** — strategy, side, price, size, market name
- ⚠️ **Throttle alerts** — when drawdown triggers size reduction
- 🛑 **Halt alerts** — when trading is auto-stopped
- 🚀 **Startup** — balance, active strategies, mode
- 📊 **Shutdown summary** — runtime, fills, net P&L

## Analytics & Risk

### Strategy Analytics (Phase 3)

The bot tracks per-strategy performance in real time:
- **Win rate** — percentage of profitable trades
- **Net P&L** — cumulative profit/loss
- **Sharpe ratio** — risk-adjusted return
- **Max drawdown** — worst peak-to-trough
- **Streaks** — consecutive wins/losses
- **Health score** — 0.0 (dead) to 1.0 (perfect)
- **Auto-disable** — strategies scoring below threshold are paused automatically

On shutdown, a **scorecard** is printed showing every strategy's metrics.

### Adaptive Risk Manager (Phase 3 + Phase 6)

When enabled, all risk limits scale dynamically with your balance:
- Lose money → limits tighten automatically
- Make money → limits expand proportionally
- **Drawdown throttle** at -5% → trade sizes cut to 50%
- **Drawdown halt** at -12% → all trading stopped
- **Portfolio Greeks** — net delta/gamma computed every scan cycle (Phase 6)
- **Pre-trade shock test** — simulates +-5%/+-10% probability shocks before each trade; rejects if worst-case PnL exceeds 50% of remaining daily loss budget (Phase 6)
- **Balance floor** at 70% of starting balance → nuclear stop
- Never touch disabled strategies regardless of performance

### Order Management

- Full lifecycle: PENDING → OPEN → PARTIAL → FILLED / CANCELLED
- Live mode prefers the authenticated user WebSocket for order/trade reconciliation, then falls back to REST polling + exchange sync
- SQLite persistence (survives restarts)
- Duplicate prevention (won't double-order same token+side)
- Stale order auto-cleanup after timeout
- Graceful shutdown cancels all active orders
- Exchange sync distinguishes fills from cancels
- Per-order metadata persists `post_only`, `fee_rate_bps`, and expiration for reconciliation; **v2 `OrderArgs` does not take `fee_rate_bps`** (the live client omits it when talking to the SDK—see [docs/clob-v2-sdk.md](docs/clob-v2-sdk.md))

### TUI Dashboard

The live dashboard now exposes execution and position state that matters in production:
- **Execution Health** — market WS, user WS, Binance connectivity, event freshness, balance/positions staleness
- **Open Positions** — entry, mark, size, unrealized PnL, and PnL %
- **Open Orders / Recent Fills** — current working orders and latest confirmed fills
- **Portfolio** — session PnL plus live unrealized PnL

Open-position PnL is mark-to-market from the latest live market data:
- mid-price when real quotes are available
- otherwise last traded price as fallback

## Project Structure

```
polymarket-bot/
├── c_src/                        # Vendored C source for libpmkernel (from bs-p)
│   ├── kernel.c, kernel.h        # Sigmoid, logit, A-S quoting
│   ├── analytics.c, analytics.h  # Kelly, Greeks, OBI, shock testing
│   └── README.md                 # Attribution
├── Makefile                      # Builds libpmkernel from c_src
├── scripts/
│   ├── analyze_wallets.py              # Reverse-engineer wallets to infer strategies
│   ├── reverse_engineer_wallets.py     # Deep wallet analysis: size, conviction, playbook
│   ├── build_native.sh                 # Build libpmkernel from c_src → lib/
│   ├── download_polybacktest.py        # Download PolyBackTest data for backtesting
│   ├── run_backtest.py                 # Run backtest on downloaded data
│   ├── train_ml_directional.py         # Train ML artifacts (OHLC or +microstructure)
│   ├── topup_btc_ohlc.py               # Hybrid Binance bulk + REST tail OHLC top-up
│   ├── backtest_ml_directional.py      # Replay backtest for ML directional artifacts
│   ├── collect_binance_microstructure.py  # Start depth/trades/OI microstructure collector
│   ├── check_microstructure_quality.py # Verify collector freshness and row counts
│   ├── discover_wallets.py             # Auto-discover high-PnL wallets from leaderboard
│   └── setup_ml_collector_vps.sh       # VPS setup guide for microstructure collector
├── lib/                          # Compiled native library (gitignored)
│   └── libpmkernel.dylib         # macOS — or .so on Linux
├── src/
│   ├── bot.py                    # Main orchestrator
│   ├── client.py                 # Polymarket CLOB client (py-clob-client-v2)
│   ├── config.py                 # All configuration (incl. bs-p params)
│   ├── order_manager.py          # Order lifecycle
│   ├── risk_manager.py           # Adaptive risk + portfolio Greeks + shock testing
│   ├── persistence.py            # SQLite state store
│   ├── dashboard.py              # TUI with Risk Engine, execution, and open-position PnL panels
│   ├── websocket_feed.py         # Polymarket market + authenticated user WebSockets
│   ├── logging_utils.py          # Colored console output
│   ├── native/                   # bs-p FFI bridge
│   │   ├── __init__.py
│   │   └── pmkernel.py           # ctypes wrapper (sigmoid, logit, quotes, kelly, greeks)
│   ├── feeds/
│   │   └── binance_ws.py         # Binance BTC/USDT real-time feed
│   ├── sizing/
│   │   └── kelly.py              # Inventory-aware Kelly sizing (bs-p enhanced)
│   ├── backtest/                 # PolyBackTest backtesting module
│   │   ├── polybacktest_client.py
│   │   ├── downloader.py
│   │   ├── store.py
│   │   ├── replay_feed.py
│   │   ├── mappers.py
│   │   └── engine.py
│   ├── analytics/
│   │   └── strategy_tracker.py   # Per-strategy P&L, Sharpe, health
│   ├── alerts/
│   │   └── telegram.py           # Telegram notifications (incl. Greeks alerts)
│   ├── ml/                           # Offline ML pipeline
│   │   ├── data_loader.py            # OHLCV + microstructure loaders
│   │   ├── features.py               # Feature engineering + runtime parity
│   │   ├── train.py                  # Walk-forward trainer (LightGBM)
│   │   ├── model.py                  # ModelArtifact, LightGBMBinaryClassifier
│   │   ├── evaluate.py               # Fold metrics, Brier, profit factor
│   │   ├── backtest.py               # ML replay backtest harness
│   │   └── collectors/               # Binance microstructure collector
│   └── strategies/
│       ├── base_strategy.py              # Abstract base class
│       ├── spread_strategy.py            # Avellaneda-Stoikov quoting (bs-p)
│       ├── orderbook_imbalance_strategy.py   # Native OBI + VWAP mid (bs-p)
│       ├── cross_asset_strategy.py       # Binance → Polymarket latency arb
│       ├── terminal_convergence_strategy.py  # Near-expiry convergence
│       ├── ml_directional_strategy.py    # ML-driven directional trading
│       ├── wallet_copy_strategy.py       # Copy top traders
│       └── ...                           # + 6 more strategies
├── tests/
│   └── unit/
│       ├── test_native_engine.py         # bs-p bridge + parity tests
│       └── ...
├── data/ml/
│   ├── ohlc/btc/                     # BTC OHLC CSVs (15m, 1h, 4h, 1d)
│   ├── artifacts/                    # Trained .pkl artifacts
│   │   ├── ml_directional_15m_ohlc_full.pkl   # Primary live candidate
│   │   ├── ml_directional_1h_ohlc_full.pkl    # Secondary candidate
│   │   ├── ml_directional_4h_ohlc_full.pkl
│   │   ├── ml_directional_1d_ohlc_full.pkl
│   │   ├── ml_directional_15m_ohlc_overlap.pkl
│   │   └── ml_directional_15m_micro_overlap.pkl
│   └── collectors/                   # SQLite from microstructure collector
├── config/
│   ├── settings.ml_live.example.env              # ML directional profile template (copy, then edit)
│   └── settings.ml_live.recommended.example.env  # Conservative recommended variant
├── docs/
│   ├── strategies/
│   │   └── ml-directional-edge/
│   │       ├── README.md                 # Full training/backtest workflow
│   │       └── IMPLEMENTATION_CHECKLIST.md  # Operational runbook + promotion gates
│   ├── bs-p/
│   │   ├── bs-p_integration_plan_*.md    # Full integration plan
│   │   └── deployment-guide.md           # Testing → paper → live guide
│   └── backtesting/
├── env.example
├── requirements.txt
└── README.md
```

## Development Phases

| Phase | Status | Description |
|-------|--------|-------------|
| 1 | ✅ Complete | Core bot, 5 strategies, WebSocket, risk management |
| 2 | ✅ Complete | Binance feed, Kelly sizing, 3 BTC 5-min strategies, vol-regime |
| 3 | ✅ Complete | Strategy analytics, adaptive risk, drawdown throttling |
| 4 | ✅ Complete | Telegram alerts, order lifecycle fixes, deployment hardening |
| 5 | ✅ Complete | VPIN smart money, sentiment pipeline, combinatorial arb, wallet copy |
| 6 | ✅ Complete | bs-p native engine: A-S quoting, inventory Kelly, portfolio Greeks, shock testing |
| ML | ✅ Artifacts trained | 15m/1h/4h/1d OHLC artifacts trained; 15m primary candidate; paper deployment next |

## Adding New Strategies

1. Create `src/strategies/my_strategy.py`:

```python
from .base_strategy import BaseStrategy, Signal, SignalType, MarketData

class MyStrategy(BaseStrategy):
    name = "my_strategy"
    description = "My custom trading strategy"

    def analyze(self, market_data: list[MarketData]) -> list[Signal]:
        signals = []
        # Your analysis logic here
        return signals

    def execute(self, signals: list[Signal], order_manager) -> list[dict]:
        results = []
        # Your execution logic here
        return results
```

2. Register in `src/strategies/__init__.py`:

```python
from .my_strategy import MyStrategy

AVAILABLE_STRATEGIES = {
    # ... existing strategies
    "my_strategy": MyStrategy,
}
```

3. Run:

```bash
python -m src.bot --strategy my_strategy
```

## Risk Warning

⚠️ **This bot trades real money when `PAPER_TRADING=false`.**

- Not financial advice. Use at your own risk; the authors accept no liability for losses.
- Always paper trade first and start with small position sizes.
- The adaptive risk manager reduces risk, but no system is foolproof.
- Polymarket charges **taker fees** on most market categories (see [Trading costs](#trading-costs)).
- Never commit `.env`, private keys or API credentials. Use a dedicated trading wallet holding only funds you can afford to lose.

## Trading costs

Fees follow Polymarket's [documented formula](https://docs.polymarket.com/trading/fees), implemented in `src/fees.py`:

```
fee (USDC) = shares × feeRate × p × (1 − p)
```

- **Makers pay no fees**; only takers do. Fees are rounded to 5 decimals.
- The per-market `feeRate` is read from the Gamma market's `feeSchedule` (`feesEnabled=false` → no fee).
- Fallback rates by category (override with `FEE_RATE_<CATEGORY>`, default category `FEE_DEFAULT_CATEGORY=crypto`): crypto 0.07; sports, economics, culture, weather, other and combos 0.05; finance, politics, mentions and tech 0.04; geopolitics 0.
- Example: 100 crypto shares at $0.50 cost $1.75 in taker fees (3.5% of the $50 notional).
- `TRADING_FEE_RATE` is **deprecated**. If you set it explicitly it is applied as a legacy flat fraction of notional; leave it unset to use the formula.

## Roadmap / Current research

We are currently researching **JEV**, a data-driven approach to short-horizon Polymarket markets (such as hourly crypto Up/Down markets). The work starts with a recorder that collects public market data for offline research and backtesting, with the goal of combining data-fed decision making with deterministic risk guardrails. Nothing from this research is part of the bot yet.

## Contributing

Contributions are welcome:

1. Fork the repo and create a feature branch.
2. Keep secrets out of commits (`.env`, keys and personal config files are git-ignored).
3. Run the tests with `pytest` before opening a pull request.
4. Describe the change and how you tested it (paper mode preferred).

For bugs or ideas, please open an issue.

## API Documentation

- [Polymarket CLOB API](https://docs.polymarket.com)
- [Polymarket WebSocket](https://docs.polymarket.com/market-data/realtime-data)
- [CLOB SDK v2 notes in this repo](docs/clob-v2-sdk.md)
- [Binance WebSocket](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [bs-p native engine](docs/engines/bs-p/deployment-guide.md) — Avellaneda-Stoikov quoting, Kelly sizing, portfolio Greeks
- [Backtesting](docs/backtesting/README.md) — PolyBackTest API, data download, strategy replay

## License

Released under the [MIT License](LICENSE). Provided "as is", without warranty of any kind. Use at your own risk.
