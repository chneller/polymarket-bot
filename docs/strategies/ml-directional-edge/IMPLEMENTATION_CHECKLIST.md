# ML Directional Edge Implementation Checklist
 
 Use this as the operational runbook for `ml_directional`.
 
 ## Current Context Anchor
 
 - [x] Live OHLC parity is implemented through `BinanceFeed.get_recent_ohlcv(...)` and `FeatureBuilder.build_training_schema_runtime_row(...)`
 - [x] OHLC-trained artifacts are live-compatible with the current strategy path
 - [x] Micro-trained artifacts are intentionally blocked live until true `micro_*` parity exists
 - [x] SQLite indexes were added for the Binance microstructure collector tables
 - [x] Overlap comparison currently favors OHLC-only over OHLC + micro on the March-April 2026 window
 - [x] Complete the richer OHLC upgrade cycle end-to-end:
   - [x] Train upgraded `15m_full`
   - [x] Train upgraded `1h_full`
   - [x] Train upgraded `4h_full`
   - [x] Train upgraded `1d_full`
   - [x] Compare all four artifacts and pick live-testing candidates
   - [ ] Revisit microstructure only after the upgraded OHLC baseline is fully evaluated
 - [x] Current favorite live-testing candidates are `15m_full` first and `1h_full` second
 
 ## 1. Data Pipeline
 
 - [x] Confirm OHLCV CSVs exist under `data/ml/ohlc/btc/`
 - [x] Normalize CSV schema through `src/ml/data_loader.py`
 - [x] Build leakage-aware multi-timeframe features in `src/ml/features.py`
 - [x] Expand candle-derived features with ATR, momentum, volatility, EMA/SMA distances, EMA/SMA slopes, RSI, breakout distance, and volume z-score
 - [x] Add collector for raw microstructure events in `src/ml/collectors/binance_depth.py`
 - [x] Start collector:
   - [x] `./.venv/bin/python -m scripts.collect_binance_microstructure --symbol btcusdt`
- [ ] On the VPS:
  - [ ] Set timezone to `UTC`
  - [ ] Enable `chrony`
  - [ ] Verify `timedatectl`
  - [ ] Verify `chronyc tracking`
- [ ] Benchmark `VPS -> Binance` connectivity before committing to the region
- [ ] Keep collector running for 2-4 weeks before microstructure-enhanced training
- [x] Verify collector quality periodically:
  - [x] `./.venv/bin/python -m scripts.check_microstructure_quality --db-path data/ml/collectors/binance_microstructure.sqlite`
  - [x] Confirm `depth_snapshots`, `agg_trades`, and `funding_open_interest` continue increasing
  - [ ] Keep re-checking freshness while the collector stays online

 ## 2. Baseline Training
 
 - [x] Train upgraded 15m OHLC-only full-history artifact:
   - [x] `./.venv/bin/python -m scripts.train_ml_directional --target-timeframe 15m --artifact-path data/ml/artifacts/ml_directional_15m_ohlc_full.pkl`
 - [x] Train upgraded 1h OHLC-only full-history artifact:
   - [x] `./.venv/bin/python -m scripts.train_ml_directional --target-timeframe 1h --artifact-path data/ml/artifacts/ml_directional_1h_ohlc_full.pkl`
 - [x] Train upgraded 4h OHLC-only full-history artifact:
   - [x] `./.venv/bin/python -m scripts.train_ml_directional --target-timeframe 4h --artifact-path data/ml/artifacts/ml_directional_4h_ohlc_full.pkl`
 - [x] Train upgraded 1d OHLC-only full-history artifact:
   - [x] `./.venv/bin/python -m scripts.train_ml_directional --target-timeframe 1d --artifact-path data/ml/artifacts/ml_directional_1d_ohlc_full.pkl`
 - [x] Confirm artifacts exist:
   - [x] `data/ml/artifacts/ml_directional_15m_ohlc_full.pkl`
   - [x] `data/ml/artifacts/ml_directional_1h_ohlc_full.pkl`
   - [x] `data/ml/artifacts/ml_directional_4h_ohlc_full.pkl`
   - [x] `data/ml/artifacts/ml_directional_1d_ohlc_full.pkl`
 - [x] Review printed summary:
   - [x] Average accuracy
   - [x] Worst fold accuracy
   - [x] Average Brier score
   - [x] Average net EV per trade
   - [x] Profit factor
 - [x] Compare upgraded OHLC artifacts across horizons:
   - [x] `15m_full` vs `1h_full` vs `4h_full` vs `1d_full`
   - [x] Choose favorites for paper / tiny-size live testing
 - [x] Current ranking:
   - [x] `15m_full` — strongest current candidate
   - [x] `1h_full` — second-best candidate
   - [x] `4h_full` — viable research artifact, weaker than 15m/1h
   - [x] `1d_full` — not a current live candidate
 - [x] Train 1h recent-window challengers with the same baseline 1h OHLC recipe:
   - [x] `./.venv/bin/python -m scripts.train_ml_directional --target-timeframe 1h --start-date 2022-01-01 --artifact-path data/ml/artifacts/ml_directional_1h_ohlc_2022_2026.pkl`
   - [x] `./.venv/bin/python -m scripts.train_ml_directional --target-timeframe 1h --start-date 2024-01-01 --artifact-path data/ml/artifacts/ml_directional_1h_ohlc_2024_2026.pkl`
 - [x] Confirm recent-window challenger artifacts exist:
   - [x] `data/ml/artifacts/ml_directional_1h_ohlc_2022_2026.pkl`
   - [x] `data/ml/artifacts/ml_directional_1h_ohlc_2024_2026.pkl`
 - [x] Compare 1h challengers against the full-history 1h baseline:
   - [x] `1h_full` — accuracy `0.700`, Brier `0.198`, net EV/trade `0.440`, profit factor `2.60`
   - [x] `1h_2022_2026` — accuracy `0.691`, Brier `0.202`, net EV/trade `0.413`, profit factor `2.44`
   - [x] `1h_2024_2026` — accuracy `0.679`, Brier `0.207`, net EV/trade `0.392`, profit factor `2.30`
 - [x] Keep `1h_full` as the preferred 1h artifact until a later challenger beats it on calibration / EV / robustness
 - [ ] Treat current trainer output as baseline research only:
   - [ ] Calibration is still manual / follow-up work
   - [ ] Final untouched holdout is still follow-up work
   - [ ] Promotion gates are still manual / follow-up work

 ## 3. Microstructure-Enhanced Training
 
 - [ ] Only after enough collector history exists:
   - [ ] `./.venv/bin/python -m scripts.train_ml_directional --target-timeframe 15m --include-microstructure --microstructure-db data/ml/collectors/binance_microstructure.sqlite`
 - [ ] Compare results vs upgraded OHLC-only baseline
 - [ ] Keep microstructure features only if they improve post-friction results
 - [ ] Do not promote a micro artifact to live until `micro_*` runtime parity exists

## 4. Replay Backtest

- [ ] Status for now: waiting for a trained artifact worth evaluating

## 5. Profile Config

- [x] Add ML settings to profile config such as `config/settings.wallet_a.live`
 - [ ] Before paper trading, set:
   - [ ] `PAPER_TRADING=true`
   - [ ] `ML_DIRECTIONAL_ENABLED=true`
   - [ ] `ML_DIRECTIONAL_MODEL_PATH_15M=./data/ml/artifacts/ml_directional_15m_ohlc_full.pkl`
   - [ ] `ML_DIRECTIONAL_MODEL_PATH_1H=./data/ml/artifacts/ml_directional_1h_ohlc_full.pkl`
  - [ ] Keep `.env` for secrets only

## 6. Paper Trading

 - [x] Current recommended candidate for cautious paper / tiny-size live testing:
   - [x] Primary: `data/ml/artifacts/ml_directional_15m_ohlc_full.pkl`
   - [x] Secondary: `data/ml/artifacts/ml_directional_1h_ohlc_full.pkl`
   - [x] Dedicated paper profile: `config/settings.ml_paper.env`
 - [ ] Run:
   - [ ] Set `ML_DIRECTIONAL_MODEL_PATH_15M=./data/ml/artifacts/ml_directional_15m_ohlc_full.pkl`
   - [ ] Set `ML_DIRECTIONAL_MODEL_PATH_1H=./data/ml/artifacts/ml_directional_1h_ohlc_full.pkl`
   - [ ] `BOT_PUBLIC_CONFIG_FILE=./config/settings.ml_paper.env BOT_WALLET_ID=WALLET_A ./.venv/bin/python -m src.bot --strategy ml_directional`
 - [ ] Confirm runtime behavior:
   - [ ] Strategy loads artifact successfully
   - [ ] Only 15m/1h BTC Up/Down markets are considered
   - [ ] Signals include model probability and edge metadata
  - [ ] Feed staleness / rolling accuracy / Brier halts work
  - [ ] Artifact is OHLC-trained or otherwise live-compatible with runtime parity
  - [ ] Prefer paper mode first, then tiny-size live only after paper stability is acceptable

## 7. Promotion Gates

- [ ] Collect at least 200 resolved paper trades
- [ ] Accuracy >= 53% on 15m and >= 52% on 1h
- [ ] Net EV > 0 after frictions
- [ ] No catastrophic 50-trade window below 48% accuracy
- [ ] Only then consider enabling live capital
