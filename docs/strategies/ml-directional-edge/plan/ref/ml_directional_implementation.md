# ML Directional Edge Implementation Guide

This document operationalizes the roadmap into an execution checklist.

Use this as the runbook for research -> backtest -> paper -> live.

## 1) Data Pipeline

- [x] Build the offline feature computation pipeline using existing BTC OHLCV in `data/binance/ohlc/btc`
- [x] Source dataset: `bitcoin-historical-datasets-2018-2024` (Kaggle)
- [x] Newer OHLCV data can be downloaded periodically via Kaggle API (keys in env)
- [x] Dataset URL: `https://www.kaggle.com/datasets/novandraanugrah/bitcoin-historical-datasets-2018-2024/`
- [ ] For microstructure features (OBI, CVD, liquidations), collect from Binance WebSocket for 2-4 weeks before training. Start collection immediately.
- [x] Validate 15m/1h/4h CSV schema (`timestamp,open,high,low,close,volume`)
- [ ] Start microstructure collector:
  - [ ] `python -m scripts.collect_binance_microstructure --symbol btcusdt`
  - [ ] Keep running for at least 2-4 weeks
- [ ] Verify collector DB growth:
  - [x] `binance_depth` rows increasing
  - [x] `binance_aggtrades` rows increasing
  - [x] `binance_liquidations` rows increasing
  - [x] Run quality check: `python -m scripts.check_microstructure_quality --db-path data/ml/binance_microstructure.sqlite`

## 2) Model Training and Validation

- [ ] Train logistic baseline:
  - [ ] `python -m scripts.train_ml_directional --model logistic`
- [ ] Train LightGBM baseline:
  - [ ] `python -m scripts.train_ml_directional --model lightgbm`
- [ ] Confirm outputs in `data/ml/models/latest/`:
  - [ ] `model.pkl`
  - [ ] `artifact.json`
  - [ ] `fold_metrics.csv`
  - [ ] `summary.txt`
- [ ] Validation gates (go/no-go):
  - [ ] Fold accuracy > 53% on 15m target
  - [ ] No fold below 50.5%
  - [ ] Brier score < 0.25
  - [ ] Profit factor > 1.3 in friction-aware sim

## 3) Friction-Aware Backtesting

- [ ] Run replay:
  - [ ] `python -m scripts.backtest_ml_directional --model-dir data/ml/models/latest`
- [ ] Confirm friction assumptions:
  - [ ] Maker threshold >= 3 cents
  - [ ] Taker threshold >= 4.5 cents near 50c
  - [ ] Slippage penalty included
  - [ ] Missed fill/adverse selection buffer included
- [ ] Backtest gates:
  - [ ] Net EV per trade > 0
  - [ ] Profit factor > 1.2
  - [ ] Max drawdown <= 15%

## 4) Bot Integration

- [ ] Enable strategy:
  - [ ] Set profile values in `config/settings.wallet_a.live` (or your active profile file), not `.env`:
    - [ ] `ML_DIRECTIONAL_ENABLED=true`
    - [ ] `ML_DIRECTIONAL_MODEL_DIR=./data/ml/models/latest`
- [ ] Run strategy in paper mode:
  - [ ] `./venv/bin/python -m src.bot --strategy ml_directional`
- [ ] Verify runtime behavior:
  - [ ] Model loads at startup
  - [ ] Only BTC 15m/1h Up/Down markets considered
  - [ ] Signals include model probability + edge metadata
  - [ ] Circuit breakers trigger on low rolling accuracy / high Brier

## 5) Paper Trading Promotion Gates

- [ ] Accumulate >= 200 resolved trades
- [ ] Evaluate logs:
  - [ ] `python -m scripts.evaluate_ml_paper_trades --db-path data/bot_state.sqlite`
- [ ] Promotion criteria:
  - [ ] Accuracy >= 53%
  - [ ] Brier <= 0.25
  - [ ] Net EV > 0 after frictions
  - [ ] No catastrophic 50-trade window (accuracy < 48%)

## 6) Live Deployment (Conservative)

- [ ] Generate live profile:
  - [ ] `python -m scripts.prepare_ml_live_profile`
- [ ] Start with quarter-Kelly:
  - [ ] `KELLY_FRACTION_MODE=quarter`
  - [ ] Max single trade <= 3% bankroll
  - [ ] Max per-market exposure <= 5% bankroll
  - [ ] Max directional aggregate <= 10% bankroll
- [ ] Live rollout sequence:
  - [ ] Week 1: tiny bankroll ($500-$1k)
  - [ ] No scaling before 200+ live trades
  - [ ] Promote to half-Kelly only if accuracy and Brier gates remain green

## Fee Reality Notes (as of Mar 6, 2026)

- [ ] Confirm fee assumptions with current docs before each deployment
- [ ] All crypto horizons now have taker fees (5m/15m/1h/4h/daily/weekly)
- [ ] Use maker-first execution by default
- [ ] Keep dynamic `feeRateBps` handling enabled in client

## Kill-Switch Conditions

- [ ] Rolling accuracy < 51% over 100 trades
- [ ] Rolling Brier > 0.26 over 50 trades
- [ ] 10 consecutive losses
- [ ] Binance or Polymarket feed desync > 10s
- [ ] Unexpected fee schedule change

