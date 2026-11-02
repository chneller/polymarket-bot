# Wallet Copy Strategy

Copy trades from top Polymarket traders by tracking their proxy wallets and mirroring their BUY/SELL activity.

## Overview

- **Leaderboard or manual** — Track wallets from the CRYPTO leaderboard (by PnL) or a fixed list
- **Copy BUYs** — When a tracked wallet buys, the bot places a matching BUY (configurable size)
- **Mirror SELLs** — When a tracked wallet sells a position we copied, the bot mirrors the exit
- **Filters** — Crypto-only, min trade size, max copy delay, per-wallet poll throttle

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `TRACKED_WALLETS` | — | Comma-separated proxy addresses (`0xabc...,0xdef...`) |
| `WALLET_COPY_USE_LEADERBOARD` | `true` | Merge top N from leaderboard into tracked list |
| `WALLET_COPY_LEADERBOARD_TOP_N` | `5` | How many from leaderboard |
| `WALLET_COPY_LEADERBOARD_CATEGORY` | `CRYPTO` | Leaderboard category |
| `WALLET_COPY_LEADERBOARD_PERIOD` | `MONTH` | DAY, WEEK, MONTH, ALL |
| `WALLET_COPY_SIZE_USD` | from ORDER_SIZE_USD | Fixed USD per copy (use ≥5 for high-price markets; Polymarket min 5 shares) |
| `WALLET_COPY_SIZE_MULTIPLIER` | `1.0` | Scale vs tracked trade size |
| `WALLET_COPY_MAX_DELAY_SECONDS` | `120` | Ignore trades older than this |
| `WALLET_COPY_MIN_TRADE_USD` | `10` | Don't copy smaller trades |
| `WALLET_COPY_CRYPTO_ONLY` | `true` | Only copy crypto markets |
| `WALLET_COPY_COOLDOWN_SECONDS` | `60` | Per-token cooldown |
| `WALLET_COPY_MIN_WALLET_POLL_SECONDS` | `2.0` | Min seconds between polls per wallet |

## Wallet Rotation

The **wallet rotation** module automatically replaces inactive tracked wallets with scored leaderboard candidates. This addresses the main risk: tracked wallets stop trading or underperform.

### When to Use

- You rely on manual `TRACKED_WALLETS` and they sometimes go inactive
- You want steady gains from active traders, not lucky one-off leaders
- You prefer automated replacement over manual config edits

### How It Works

1. **Inactivity detection** — Each tracked wallet is checked for recent trades (default: within 48 hours)
2. **Candidate discovery** — Fetches DAY + MONTH leaderboards, unions by wallet
3. **Scoring** — Candidates are scored by:
   - Daily and monthly leaderboard rank (stability proxy)
   - Crypto % and short-term (5m/15m/1h/daily) trade share
   - Recent activity (days since last trade, trades/day)
4. **Replacement** — Inactive wallets are replaced with top-scored candidates (max 2 per cycle)
5. **Persistence** — Tracked wallets and rotation cooldown are saved to SQLite; they survive restarts

### Rotation Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `WALLET_ROTATION_ENABLED` | `false` | Enable rotation (replaces leaderboard merge when on) |
| `WALLET_ROTATION_REFRESH_INTERVAL_SECONDS` | `600` | Min seconds between rotation cycles (10 min) |
| `WALLET_ROTATION_INACTIVITY_THRESHOLD_HOURS` | `48` | Hours since last trade to consider inactive |
| `WALLET_ROTATION_MAX_REPLACEMENTS_PER_CYCLE` | `2` | Max wallets replaced per cycle |
| `WALLET_ROTATION_MIN_TRACKED_WALLETS` | `1` | Never drop below this many |
| `WALLET_ROTATION_CANDIDATE_POOL_SIZE` | `30` | Leaderboard entries to consider |
| `WALLET_ROTATION_MIN_TRADES` | `20` | Min trades for a candidate |
| `WALLET_ROTATION_MIN_CRYPTO_PCT` | `70` | Min % of trades in crypto |
| `WALLET_ROTATION_MIN_SHORTTERM_PCT` | `40` | Min % in fast markets (5m/15m/1h/daily) |
| `WALLET_ROTATION_MAX_DAYS_SINCE_LAST_TRADE` | `7` | Max days inactive for candidates |
| `WALLET_ROTATION_MIN_TRADES_PER_DAY` | `0.5` | Min activity density |
| `WALLET_ROTATION_REMOVED_COOLDOWN_HOURS` | `24` | Don't re-add recently removed wallets |

### Avoiding MM/Spread and Volume-Farmer Wallets

**Do not copy** from spread-strategy, airdrop volume farmers, or market-maker wallets:

- **MM/Spread-like** — Balanced buys/sells (35–65% sell rate), very high activity (≥15 trades/day). These are market-making, not directional alpha.
- **Volume-farmer-like** — Very high trades/day (≥25), small avg size (≤$10), many different tokens (≥20). These trade for airdrop volume, not conviction.

**Rotation** automatically excludes MM/volume-farmer candidates. For **manual** `TRACKED_WALLETS`, run the analysis script before adding:

```bash
TRACKED_WALLETS=0xabc,0xdef python scripts/analyze_wallets.py
```

Look for `❌ AVOID` and `⛔ MM/Spread-like` or `⛔ Volume-farmer-like` flags. Remove those wallets from your config.

### Scoring Criteria (Steady Gains, Not Lucky Shots)

**Hard filters** (must pass):

- Recent activity within threshold
- Minimum trades count
- Minimum trades/day
- Minimum crypto % and short-term %
- **Not** MM/Spread-like or volume-farmer-like

**Score components** (weighted):

- Daily leaderboard rank
- Monthly leaderboard rank (stability)
- Crypto specialization
- Fast-market specialization

**Anti-lucky-shot** — Combined DAY + MONTH evidence required for high scores.

### Blocked Wallets (hedge/MM)

Manually flag wallets to never track or copy (e.g. hedgers, market makers):

- **`data/blocked_wallets.txt`** — One address per line; `#` comments allowed
- **`WALLET_COPY_BLOCKED_WALLETS`** — Env override, comma-separated

Blocked wallets are excluded from rotation candidates, leaderboard merge, and copy.

### Persistence

- **Tracked wallets** — Saved to `wallet_copy_state` table in `BOT_STATE_DB`
- **Rotation cooldown** — Recently removed wallets are stored so they aren't re-added immediately
- **On restart** — Loads persisted state; config `TRACKED_WALLETS` is used only when no persisted data exists

### TUI Visibility

- **Strategies table** — Shows tracked wallets (shortened) and rotation blurb, e.g. `3: 0x1979...c9d 0x1d00...313 | rot -2+1`
- **Activity log** — Rotation events: `[ROTATION] 2 inactive: ...`, `[ROTATION] +0xnew1... score=0.85`

### Enabling Rotation

```bash
# In your config (e.g. settings.wallet_a.live)
WALLET_ROTATION_ENABLED=true
WALLET_COPY_USE_LEADERBOARD=false   # Rotation replaces leaderboard merge
TRACKED_WALLETS=0xabc...,0xdef...   # Initial seed (or leave empty to start from leaderboard)
```

## Analysis Scripts

**Analyze wallets** — Infer strategy (BTC vs multi-crypto, 5m vs 15m, avg size):

```bash
python scripts/analyze_wallets.py
TRACKED_WALLETS=0xabc...,0xdef... python scripts/analyze_wallets.py
```

**Discover and score wallets** — Find candidates for manual config or rotation tuning:

```bash
python scripts/discover_wallets.py
# Options: TOP_N=10, MIN_CRYPTO_PCT=80, OUTPUT_CONFIG=./config/settings.discovered.example
```

**Reverse-engineer** — Deep playbook (price distribution, outcome preference, horizon mix):

```bash
TRACKED_WALLETS=0xabc...,0xdef... python scripts/reverse_engineer_wallets.py
```

## Related Docs

- [REVERSE_ENGINEER_REPORT.md](REVERSE_ENGINEER_REPORT.md) — Sample output from reverse-engineer script
