# bs-p Native Engine — Deployment Guide

From smoke test to live trading on a single wallet profile (example ID `WALLET_A`).

---

## 0. Build the Engine

Already done, but for reference (and for fresh machines / after bs-p updates):

```bash
cd polymarket-bot
./scripts/build_native.sh
```

Verify:

```bash
./venv/bin/python -c "from src.native.pmkernel import NATIVE_AVAILABLE; print(NATIVE_AVAILABLE)"
# True
```

On Linux VPS later, the same Makefile handles `.so` + AVX-512 auto-detection.

---

## 1. Create the WALLET_A Config File

Copy the template below to `config/settings.wallet_a.live`:

```bash
cp config/settings.wallet_a.live.example config/settings.wallet_a.live
```

This config focuses on **spread + orderbook imbalance** only, with conservative
bs-p parameters for a small bankroll.  Everything else is disabled.

---

## 2. Unit Tests

Run the full suite before any trading:

```bash
cd polymarket-bot
./venv/bin/python -m pytest tests/unit/ -v
```

Expected: 30/31 pass (1 pre-existing wallet_copy test failure, unrelated).

Key tests to watch:
- `test_native_available` — library loads
- `TestAdaptiveKelly::test_native_vs_fallback_parity` — C matches Python
- `TestCalculateQuotes::test_inventory_shifts_quotes` — A-S quoting works
- `TestKellyIntegration::test_kelly_size_with_inventory` — inventory scaling

---

## 3. Paper Trading (48 hours minimum)

### 3a. Start paper mode

```bash
BOT_PUBLIC_CONFIG_FILE=./config/settings.wallet_a.live \
BOT_WALLET_ID=WALLET_A \
PAPER_TRADING=true \
./venv/bin/python -m src.bot --strategy all
```

The `PAPER_TRADING=true` env var overrides the config file value.

### 3b. What to monitor

In the TUI dashboard:
- **Risk Engine panel** should show `Engine: NATIVE`
- **Portfolio Delta/Gamma** should stay near zero with few positions
- **Execution Health** should show both market/user WebSockets healthy in live mode
- **Open Positions** should show mark-to-market unrealized PnL for active positions
- **Strategy rows**: `spread` and `orderbook_imbalance` should show signals

In Telegram:
- Startup message confirms "Engine: libpmkernel (native)"
- Fill alerts show `[AS]` (Avellaneda-Stoikov) for spread strategy

### 3c. What to check after 48h

```
Questions to answer:
├── Are spread quotes tighter than the raw bid+1¢ / ask logic?
│   → Check signal metadata: entry_price vs best_bid, exit_price vs best_ask
├── Is Kelly sizing smaller when holding inventory?
│   → Check logs for "native_sized=True" and "inventory_scale < 1.0"
├── Did any trades get rejected by shock test?
│   → Telegram: "Shock Test Rejected" alerts (some rejections = healthy)
├── Is the OBI strategy using native microstructure?
│   → Requires ENABLE_WEBSOCKET_FEED=true (set in config)
└── Any crashes or segfaults?
    → If yes, set NATIVE_ENGINE_ENABLED=false and report
```

---

## 4. Shadow Mode for Spread Strategy (Optional, Recommended)

If you want extra confidence, you can run a shadow comparison before going live.
Add this to the config:

```env
# In settings.wallet_a.live, temporarily:
PAPER_TRADING=true
```

The spread strategy already logs `[AS]` vs `[manual]` tags.  Compare the
quotes in the activity log:
- A-S quotes should be tighter (smaller spread) when inventory is zero
- A-S quotes should widen asymmetrically when you're long

---

## 5. Go Live on WALLET_A Wallet

### 5a. Pre-flight checklist

```
□ Paper traded 48h+ with no crashes
□ Unit tests pass
□ PAPER_TRADING is set to false in config
□ WALLET_A wallet is funded with the bankroll you intend to risk
□ Telegram alerts are configured and receiving
□ ADAPTIVE_RISK_ENABLED=true
□ NATIVE_ENGINE_ENABLED=true (default)
□ You've read the spread strategy's signal log and understand the quotes
```

### 5b. Launch

```bash
BOT_PUBLIC_CONFIG_FILE=./config/settings.wallet_a.live \
BOT_WALLET_ID=WALLET_A \
./venv/bin/python -m src.bot --strategy all
```

### 5c. First 24h live — watch closely

- Keep Telegram notifications on
- Check TUI every few hours
- If `|net_delta| > 0.3` alert fires, check if positions are correlated
- If shock test rejects > 50% of trades, `QUOTING_GAMMA` is too high — lower to 0.7

---

## 6. Parameter Tuning Schedule

| Bankroll     | QUOTING_GAMMA | KELLY_FRACTION_MODE | QUOTING_K | Action                    |
|-------------|---------------|---------------------|-----------|---------------------------|
| Small (starting)     | 1.0           | quarter             | 2.0       | Starting (conservative)   |
| $500+       | 0.7           | quarter             | 2.0       | Reduce gamma              |
| $750+       | 0.5           | half                | 2.5       | Switch to half Kelly      |
| $1,000+     | 0.4           | half                | 3.0       | k tracks fill rate data   |
| $2,000+     | 0.3           | half                | calibrate | Consider VPS              |

Update gamma in the config file and restart the bot.  No rebuild needed.

---

## 7. Expanding to Other Wallets

Once WALLET_A is profitable for 1+ week:

1. Create `config/settings.wallet_b.live` with same bs-p params
2. Adjust `ORDER_SIZE_USD` and `MAX_POSITION_USD` for that wallet's bankroll
3. Run as a second process:

```bash
BOT_PUBLIC_CONFIG_FILE=./config/settings.wallet_b.live \
BOT_WALLET_ID=WALLET_B \
./venv/bin/python -m src.bot --strategy all
```

Both processes share the same `libpmkernel.dylib` — no separate builds needed.

---

## 8. Emergency Procedures

### Kill switch — disable native engine instantly

```bash
# Add to environment or config:
NATIVE_ENGINE_ENABLED=false
```

All functions fall back to pure-Python. No restart needed if you set it in
the config file and the bot re-reads on next scan.  To be safe, restart:

```bash
# Ctrl+C the bot, then relaunch with:
NATIVE_ENGINE_ENABLED=false BOT_PUBLIC_CONFIG_FILE=./config/settings.wallet_a.live \
BOT_WALLET_ID=WALLET_A ./venv/bin/python -m src.bot --strategy all
```

### Segfault or crash

1. Check the terminal output for the crash location
2. Set `NATIVE_ENGINE_ENABLED=false` and restart immediately
3. The bot runs identically to before bs-p integration
4. Report the crash with the stack trace

### Greeks alert firing repeatedly

High `|net_delta|` means the portfolio is directionally exposed.  This isn't
a bug — it means you're long or short a cluster of correlated markets.

- Check which positions are driving delta (TUI positions panel)
- Consider manually closing some to reduce exposure
- Or wait — delta decays as markets approach resolution

---

## 9. Monitoring Checklist (Daily)

```
□ Check Telegram daily summary: delta, gamma, P&L
□ Verify engine status: NATIVE (not PYTHON fallback)
□ Review shock test rejection rate (>50% = params too tight)
□ Check Kelly inventory scale on largest positions
□ Verify no "Balance stale" warnings blocking buys
□ Verify user WebSocket is healthy and not spamming parse errors
□ Check open-position unrealized PnL against expected live marks
□ Review fill rate — are A-S quotes getting filled?
```

---

## 10. What's Not Needed Yet

| Feature                        | Status    | When                                    |
|-------------------------------|-----------|------------------------------------------|
| VPS co-location               | Skip      | Until bankroll > $2k and latency matters |
| Correlation matrix for Greeks | Skip      | Until trading 5+ correlated BTC markets  |
| Ring buffer (SPSC)            | Skip      | Future architecture, not needed now      |
| A/B wallet split              | Optional  | After 1 week live if you want comparison |
| Cross-asset latency arb       | Disabled  | Needs VPS for edge                       |
