# fetch-history-range.py
# Pull large windows of OHLCV from OANDA v20 REST with paging (max 5000 candles per call).
# Usage examples:
#   python fetch-history-range.py --granularity M1 --hours 72 --instrument US30_USD --append out/US30_USD_M1_history.csv --png
#   python fetch-history-range.py --granularity M5 --from 2025-10-01T00:00Z --to 2025-10-08T00:00Z --instrument US30_USD

import os
import time
import math
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
from dotenv import load_dotenv
import matplotlib.pyplot as plt

from oandapyV20 import API
from oandapyV20.endpoints.instruments import InstrumentsCandles
from zoneinfo import ZoneInfo  # Python 3.9+; if older, use pytz


# ---------- config from env ----------
load_dotenv()
OANDA_KEY   = os.getenv("OANDA_KEY")
ACCOUNT_ID  = os.getenv("OANDA_ACCOUNT")
ENVIRONMENT = os.getenv("OANDA_ENV", "live")  # "live" | "practice"

if not OANDA_KEY or not ACCOUNT_ID:
    raise RuntimeError("Missing OANDA_KEY or OANDA_ACCOUNT in .env")

# ---------- helpers ----------
def iso_z(dt: datetime) -> str:
    """Return ISO-8601 UTC with Z suffix."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")

def parse_iso(s: str) -> datetime:
    """Parse ISO string with Z or offset, return tz-aware UTC."""
    if s.endswith("Z"):
        s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def max_minutes_per_call(granularity: str) -> int:
    """OANDA returns max 5000 candles per request; convert to minutes by granularity."""
    # Map common granularities to minutes per candle
    gmap = {
        "S5": 5/60, "S10": 10/60, "S15": 15/60, "S30": 30/60,
        "M1": 1, "M2": 2, "M4": 4, "M5": 5, "M10": 10, "M15": 15, "M30": 30,
        "H1": 60, "H2": 120, "H3": 180, "H4": 240, "H6": 360, "H8": 480, "H12": 720,
        "D": 1440, "W": 10080, "M": 43200  # month ~30 days
    }
    if granularity not in gmap:
        raise ValueError(f"Unsupported granularity: {granularity}")
    mins_per_candle = gmap[granularity]
    return math.floor(5000 * mins_per_candle)

def fetch_chunk(api: API, instrument: str, granularity: str, start: datetime, end: datetime, price: str = "M"):
    params = {
        "granularity": granularity,
        "from": iso_z(start),
        "to": iso_z(end),
        "price": price  # "M" midpoint; "B" bid; "A" ask
    }
    r = InstrumentsCandles(instrument=instrument, params=params)
    data = api.request(r)
    raw = data.get("candles", [])
    rows = []
    for c in raw:
        if c.get("complete"):
            t = c["time"]
            if price == "M":
                o = float(c["mid"]["o"]); h = float(c["mid"]["h"]); l = float(c["mid"]["l"]); cl = float(c["mid"]["c"])
            elif price == "B":
                o = float(c["bid"]["o"]); h = float(c["bid"]["h"]); l = float(c["bid"]["l"]); cl = float(c["bid"]["c"])
            else:
                o = float(c["ask"]["o"]); h = float(c["ask"]["h"]); l = float(c["ask"]["l"]); cl = float(c["ask"]["c"])
            v = int(c.get("volume", 0))  # OANDA volume for CFDs/FX = tick count
            rows.append((t, o, h, l, cl, v))
    df = pd.DataFrame(rows, columns=["time","o","h","l","c","v"])
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.set_index("time").sort_index()
    return df

def fetch_paged(api: API, instrument: str, granularity: str, start: datetime, end: datetime, price: str = "M", sleep_sec: float = 0.2):
    """Page through time with <=5000-candle chunks."""
    per_call_minutes = max_minutes_per_call(granularity)
    df_all = []
    cur = start
    while cur < end:
        nxt = min(end, cur + timedelta(minutes=per_call_minutes))
        df = fetch_chunk(api, instrument, granularity, cur, nxt, price=price)
        if not df.empty:
            df_all.append(df)
        cur = nxt
        time.sleep(sleep_sec)  # be polite to the API
    if df_all:
        out = pd.concat(df_all).sort_index()
        out = out[~out.index.duplicated(keep="last")]
        return out
    return pd.DataFrame(columns=["o","h","l","c","v"])

def merge_dedupe(existing: pd.DataFrame, new: pd.DataFrame):
    if existing is None or existing.empty:
        return new
    all_df = pd.concat([existing, new]).sort_index()
    all_df = all_df[~all_df.index.duplicated(keep="last")]
    return all_df

def save_csv(df: pd.DataFrame, path: Path):
    df.to_csv(path)

def save_png(df: pd.DataFrame, instrument: str, out_path: Path):
    if df.empty:
        return
    # compute EMA(45) on close
    ema = df["c"].ewm(span=45, adjust=False).mean()
    # localize for x-axis readability
    local = df.copy()
    try:
        local.index = local.index.tz_convert("Europe/London")
    except Exception:
        local.index = local.index.tz_localize("UTC").tz_convert("Europe/London")

    plt.switch_backend("Agg")
    fig, (axp, axv) = plt.subplots(2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios":[3,1]})

    # wicks
    axp.vlines(local.index, local["l"], local["h"], linewidth=1)
    # bodies
    up = local["c"] >= local["o"]; down = ~up
    axp.vlines(local.index[up],   local.loc[up,"o"], local.loc[up,"c"], linewidth=6)
    axp.vlines(local.index[down], local.loc[down,"c"], local.loc[down,"o"], linewidth=6)

    axp.plot(local.index, ema.reindex(df.index).values, linewidth=1.2, label="EMA(45)")
    axp.set_title(f"{instrument} — {len(df)} bars ({df.index[0]} → {df.index[-1]})")
    axp.set_ylabel("Price")
    axp.legend(loc="upper left")

    axv.bar(local.index, local["v"].astype(int), width=0.0008)
    axv.set_ylabel("Volume (tick)")
    axv.set_xlabel("Time (Europe/London)")

    fig.autofmt_xdate(); plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close(fig)

# ---------- CLI ----------
def main():
    p = argparse.ArgumentParser(description="Fetch large OHLCV windows from OANDA with paging.")
    p.add_argument("--instrument", default=os.getenv("INSTRUMENT","US30_USD"))
    p.add_argument("--granularity", default="M1", help="e.g., M1,M5,M15,H1,D")
    p.add_argument("--hours", type=int, default=None, help="Lookback hours (alternative to --from/--to)")
    p.add_argument("--from", dest="from_iso", default=None, help="Start ISO e.g. 2025-10-01T00:00Z")
    p.add_argument("--to", dest="to_iso", default=None, help="End ISO e.g. 2025-10-08T00:00Z (default now UTC)")
    p.add_argument("--price", default="M", choices=["M","B","A"], help="M=mid, B=bid, A=ask")
    p.add_argument("--out", default=None, help="CSV path to write; default auto-named in out/")
    p.add_argument("--append", default=None, help="Existing CSV to merge with and overwrite (dedupe by time)")
    p.add_argument("--png", action="store_true", help="Also render a PNG with EMA(45) + volume")
    p.add_argument("--sleep", type=float, default=0.5, help="Delay between paged requests (seconds)")
    p.add_argument("--session", default=None,
               help='Time ranges like "09:30-16:00" or multiple: "09:30-11:30,13:00-16:00"')
    p.add_argument("--session-tz", default="America/New_York",
               help='Timezone used to evaluate --session (default America/New_York)')
    p.add_argument("--weekdays-only", action="store_true",
               help="Keep only Monday–Friday in the filtered output")

    args = p.parse_args()

    instrument = args.instrument
    gran = args.granularity.upper()
    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Determine time window
    now_utc = datetime.now(timezone.utc)
    if args.from_iso:
        start = parse_iso(args.from_iso)
    elif args.hours:
        start = now_utc - timedelta(hours=args.hours)
    else:
        # default: 24h
        start = now_utc - timedelta(hours=24)

    end = parse_iso(args.to_iso) if args.to_iso else now_utc

    # Build output paths
    if args.out:
        csv_path = Path(args.out)
    else:
        csv_path = out_dir / f"{instrument}_{gran}_{start.strftime('%Y%m%dT%H%MZ')}_{end.strftime('%Y%m%dT%H%MZ')}.csv"

    append_path = Path(args.append) if args.append else None
    png_path = csv_path.with_suffix(".png")

    # Fetch
    api = API(access_token=OANDA_KEY, environment=ENVIRONMENT)
    print(f"📥 Fetching {instrument} {gran} from {iso_z(start)} to {iso_z(end)} (env={ENVIRONMENT})")
    df = fetch_paged(api, instrument, gran, start, end, price=args.price, sleep_sec=args.sleep)

    if df.empty:
        print("No data returned (market closed or range too narrow).")
        return

    # Merge with existing (if requested)
    if append_path and append_path.exists():
        existing = pd.read_csv(append_path, parse_dates=["time"]).set_index("time")
        if existing.index.tz is None:
            existing.index = existing.index.tz_localize("UTC")
        merged = merge_dedupe(existing, df)
        
        print(f"✔ Merged & saved: {append_path.resolve()}")
        # also write the separate fetch to csv_path if you want both; here we skip to avoid duplication
        final_df = merged
    else:
        # write the fetched window
        # ensure volume integer
        df["v"] = df["v"].astype(int)
        # reset index to column named 'time' for CSV
        df_out = df.reset_index()
        
        print(f"✔ Saved window: {csv_path.resolve()}")
        final_df = df
    
    # After you computed `final_df` (the concatenated/paged DataFrame in UTC):
    if args.session:
      before = len(final_df)
      final_df = filter_by_sessions(final_df, args.session, args.session_tz, args.weekdays_only)
      after = len(final_df)
      print(f"⏱ session filter {args.session} {args.session_tz} → kept {after}/{before} rows")

    if append_path:
      save_csv(final_df, append_path)
    else:
      save_csv(final_df, csv_path)
    

    # Optional PNG
    if args.png:
        save_png(final_df, instrument, png_path)
        print(f"✔ PNG saved: {png_path.resolve()}")

def _parse_hhmm(s: str):
    hh, mm = s.split(":")
    return int(hh), int(mm)

# def pre_save_filter(df: pd.DataFrame, session_str: str, session_tz: str, weekdays_only: bool):
#     if args.session:
#       before = len(df)
#       filtered_df = filter_by_sessions(df, session_str, session_tz, weekdays_only)
#       after = len(filtered_df)
#       print(f"⏱ session filter {args.session} {session_tz} → kept {after}/{before} rows")
#       return filtered_df

def filter_by_sessions(df: pd.DataFrame, session_str: str, session_tz: str, weekdays_only: bool):
    """
    Keep only rows whose local time in `session_tz` falls inside any of the given
    daily time windows. Supports multiple ranges separated by commas.
    Handles DST by converting index to session timezone daily.
    """
    if df.empty or not session_str:
        return df

    tz = ZoneInfo(session_tz)
    local = df.copy()
    # Ensure tz-aware UTC index
    if local.index.tz is None:
        local.index = local.index.tz_localize("UTC")
    local.index = local.index.tz_convert(tz)

    # Build mask across one or more ranges
    mask = pd.Series(False, index=local.index)

    for rng in [r.strip() for r in session_str.split(",") if r.strip()]:
        start_s, end_s = rng.split("-")
        # between_time works with 'HH:MM' strings
        if start_s <= end_s:
            # Simple daytime window
            sub = local.between_time(start_s, end_s, inclusive="both")
            mask.loc[sub.index] = True
        else:
            # Overnight window (e.g., 22:00-02:00) -> two halves
            sub1 = local.between_time(start_s, "23:59", inclusive="both")
            sub2 = local.between_time("00:00", end_s, inclusive="both")
            mask.loc[sub1.index] = True
            mask.loc[sub2.index] = True

    if weekdays_only:
        # Monday=0 ... Sunday=6
        mask &= local.index.weekday < 5

    # Apply mask on the original (UTC) indexed frame
    return df.loc[mask.index[mask]]


if __name__ == "__main__":
    main()
