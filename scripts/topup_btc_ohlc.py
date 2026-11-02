"""
Top-up BTC OHLC CSVs with a hybrid Binance pipeline:
1) data.binance.vision bulk history via binance_historical_data
2) Binance REST klines tail for the latest closed candles (includes today)

Usage:
    python scripts/topup_btc_ohlc.py
"""

import datetime
import glob
import json
import os
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request

import pandas as pd

# Allow using the local binance_historical_data source tree without installing
_LIB_PATH = os.path.join(os.path.dirname(__file__), "../../binance_historical_data/src")
if os.path.isdir(_LIB_PATH):
    sys.path.insert(0, os.path.abspath(_LIB_PATH))

from binance_historical_data import BinanceDataDumper  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────
TICKER = "BTCUSDT"
BOOTSTRAP_START = os.getenv("OHLC_BOOTSTRAP_START", "2018-01-01")
TIMEFRAMES = ["15m", "1h", "4h", "1d"]
DATA_DIR = os.path.join(os.path.dirname(__file__), "../data/ml/ohlc/btc")
BINANCE_DUMP_DIR = tempfile.mkdtemp(prefix="binance_dump_")
BINANCE_REST_KLINES_URL = "https://api.binance.com/api/v3/klines"

INTERVAL_TO_MS = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}

# Map timeframe → existing CSV filename pattern
CSV_MAP = {
    "15m": "btc_15m_data_2018_to_2026.csv",
    "1h": "btc_1h_data_2018_to_2026.csv",
    "4h": "btc_4h_data_2018_to_2026.csv",
    "1d": "btc_1d_data_2018_to_2026.csv",
}

COLUMNS = [
    "Open time",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "Close time",
    "Quote asset volume",
    "Number of trades",
    "Taker buy base asset volume",
    "Taker buy quote asset volume",
    "Ignore",
]


def _interval_delta(tf: str) -> pd.Timedelta:
    return pd.to_timedelta(INTERVAL_TO_MS[tf], unit="ms")


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=COLUMNS + ["__open_ts"])

    normalized = frame.copy()
    normalized = normalized.dropna(how="all")
    normalized.columns = COLUMNS
    normalized = normalized.dropna(subset=["Open time"])
    parsed_open_ts = pd.to_datetime(normalized["Open time"], utc=True, errors="coerce")
    numeric_open_time = pd.to_numeric(normalized["Open time"], errors="coerce")
    needs_epoch_parse = parsed_open_ts.isna() & numeric_open_time.notna()
    if needs_epoch_parse.any():
        parsed_open_ts.loc[needs_epoch_parse] = pd.to_datetime(
            numeric_open_time.loc[needs_epoch_parse],
            unit="ms",
            utc=True,
            errors="coerce",
        )
    normalized["__open_ts"] = parsed_open_ts
    normalized = normalized.dropna(subset=["__open_ts"])
    normalized = normalized.sort_values("__open_ts").drop_duplicates(subset=["__open_ts"], keep="last")
    normalized = normalized.reset_index(drop=True)
    return normalized


def _format_timestamps_for_csv(frame: pd.DataFrame, tf: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=COLUMNS)

    out = frame.copy()
    interval_ms = INTERVAL_TO_MS[tf]
    close_ts = out["__open_ts"] + pd.to_timedelta(interval_ms - 1, unit="ms")

    if tf == "1d":
        out["Open time"] = out["__open_ts"].dt.strftime("%Y-%m-%d %H:%M:%S.%f UTC")
        out["Close time"] = close_ts.dt.strftime("%Y-%m-%d %H:%M:%S.%f UTC")
    else:
        out["Open time"] = out["__open_ts"].dt.strftime("%Y-%m-%d %H:%M:%S.%f")
        out["Close time"] = close_ts.dt.strftime("%Y-%m-%d %H:%M:%S.%f")

    for col in [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "Quote asset volume",
        "Taker buy base asset volume",
        "Taker buy quote asset volume",
    ]:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    for col in ["Number of trades", "Ignore"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype("int64")

    out = out.drop(columns=["__open_ts"])
    return out[COLUMNS]


def _fetch_rest_klines(ticker: str, tf: str, start_open_ts: pd.Timestamp) -> pd.DataFrame:
    """Fetch klines from Binance REST starting at the given open timestamp (UTC)."""
    interval_ms = INTERVAL_TO_MS[tf]
    now_ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
    current_open_ms = (now_ms // interval_ms) * interval_ms
    latest_closed_open_ms = current_open_ms - interval_ms

    start_ms = int(start_open_ts.timestamp() * 1000)
    if start_ms > latest_closed_open_ms:
        return pd.DataFrame(columns=COLUMNS)

    all_rows = []
    next_start_ms = start_ms
    while next_start_ms <= latest_closed_open_ms:
        params = {
            "symbol": ticker,
            "interval": tf,
            "startTime": next_start_ms,
            "endTime": latest_closed_open_ms + interval_ms - 1,
            "limit": 1000,
        }
        url = f"{BINANCE_REST_KLINES_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "polymarket-bot-ohlc-topup"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        if not payload:
            break

        for row in payload:
            all_rows.append(row[:12])

        last_open_ms = int(payload[-1][0])
        candidate_next_start = last_open_ms + interval_ms
        if candidate_next_start <= next_start_ms:
            break
        next_start_ms = candidate_next_start

    if not all_rows:
        return pd.DataFrame(columns=COLUMNS)

    return pd.DataFrame(all_rows, columns=COLUMNS)


def load_dumped_csvs(dump_dir: str, ticker: str, tf: str) -> pd.DataFrame:
    """Collect all CSVs downloaded by binance_historical_data for this ticker+tf."""
    pattern = os.path.join(dump_dir, "**", f"{ticker}-{tf}-*.csv")
    files = sorted(glob.glob(pattern, recursive=True))
    if not files:
        return pd.DataFrame(columns=COLUMNS)

    frames = []
    for path in files:
        try:
            tmp = pd.read_csv(path, header=None, names=COLUMNS)
            frames.append(tmp)
        except Exception as exc:
            print(f"  WARNING: could not read {path}: {exc}")

    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat(frames, ignore_index=True)


def topup_timeframe(tf: str):
    csv_path = os.path.join(DATA_DIR, CSV_MAP[tf])
    print(f"\n{'=' * 60}")
    print(f"  Timeframe: {tf}  →  {CSV_MAP[tf]}")

    bootstrap = not os.path.exists(csv_path)
    if bootstrap:
        # Fresh clone: no CSV yet -> download full history from BOOTSTRAP_START.
        os.makedirs(DATA_DIR, exist_ok=True)
        print(f"  No CSV yet, bootstrapping from {BOOTSTRAP_START}")
        existing_df = pd.DataFrame(columns=COLUMNS)
        existing_rows = 0
        last_open_ts = pd.Timestamp(BOOTSTRAP_START, tz="UTC") - _interval_delta(tf)
    else:
        existing_raw = pd.read_csv(csv_path, header=0)
        existing_df = _normalize_frame(existing_raw)
        if existing_df.empty:
            print("  ERROR: existing CSV has no valid rows after normalization.")
            return
        existing_rows = len(existing_df)
        last_open_ts = existing_df["__open_ts"].iloc[-1]
    print(f"  Existing rows:         {existing_rows:,}")
    print(f"  Existing through:      {last_open_ts}")

    # 1) Bulk pull from data.binance.vision (day-level granularity)
    interval_delta = _interval_delta(tf)
    bulk_start_date = (last_open_ts + interval_delta).date()
    bulk_end_date = datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)

    bulk_df = pd.DataFrame(columns=COLUMNS)
    if bulk_start_date <= bulk_end_date:
        print(f"  Bulk fetch (vision):   {bulk_start_date} → {bulk_end_date}")
        tf_dump_dir = os.path.join(BINANCE_DUMP_DIR, tf)
        os.makedirs(tf_dump_dir, exist_ok=True)

        try:
            dumper = BinanceDataDumper(
                path_dir_where_to_dump=tf_dump_dir,
                asset_class="spot",
                data_type="klines",
                data_frequency=tf,
            )
            dumper.dump_data(
                tickers=[TICKER],
                date_start=bulk_start_date,
                date_end=bulk_end_date,
                is_to_update_existing=True,
            )
            bulk_df = load_dumped_csvs(tf_dump_dir, TICKER, tf)
            print(f"  Bulk rows raw:         {len(bulk_df):,}")
        except Exception as exc:
            print(f"  WARNING: bulk fetch failed, continuing with REST tail: {exc}")
    else:
        print("  Bulk fetch (vision):   skipped (already at least yesterday)")

    # 2) REST tail from newest merged point to latest closed candle (includes today)
    merged_for_tail = _normalize_frame(pd.concat([existing_df[COLUMNS], bulk_df], ignore_index=True))
    if merged_for_tail.empty:
        print("  ERROR: no data downloaded (bulk fetch failed?)")
        return
    tail_start_ts = merged_for_tail["__open_ts"].iloc[-1] + interval_delta
    print(f"  REST tail from:        {tail_start_ts}")
    rest_df = _fetch_rest_klines(TICKER, tf, tail_start_ts)
    print(f"  REST rows raw:         {len(rest_df):,}")

    # 3) Merge + normalize + dedupe on parsed timestamp
    merged = _normalize_frame(pd.concat([existing_df[COLUMNS], bulk_df, rest_df], ignore_index=True))
    net_new = len(merged) - existing_rows
    print(f"  Final merged rows:     {len(merged):,}  ({net_new:+,} net)")

    # 4) Safe rewrite
    to_save = _format_timestamps_for_csv(merged, tf)
    backup_path = csv_path + ".bak"
    if not bootstrap:
        shutil.copy2(csv_path, backup_path)
    try:
        to_save.to_csv(csv_path, index=False)
    except Exception:
        shutil.copy2(backup_path, csv_path)
        raise
    finally:
        if os.path.exists(backup_path):
            os.remove(backup_path)

    print(f"  Saved → {csv_path}")
    print(f"  Last candle open:      {merged.iloc[-1]['__open_ts']}")


def main():
    print(f"BTC OHLC Top-Up  |  {datetime.datetime.now(datetime.timezone.utc).date()} UTC")
    print(f"Temp dump dir: {BINANCE_DUMP_DIR}")
    for tf in TIMEFRAMES:
        topup_timeframe(tf)

    shutil.rmtree(BINANCE_DUMP_DIR, ignore_errors=True)
    print("\nDone.")


if __name__ == "__main__":
    main()
