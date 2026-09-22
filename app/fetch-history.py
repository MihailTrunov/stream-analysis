# fetch-history.py
# Fetch ~1500 M1 candles for an instrument from OANDA REST,
# include volume, save CSV.

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
from dotenv import load_dotenv

from oandapyV20 import API
from oandapyV20.endpoints.instruments import InstrumentsCandles

# ---- setup / config ----
load_dotenv()

OANDA_KEY   = os.getenv("OANDA_KEY")
ACCOUNT_ID  = os.getenv("OANDA_ACCOUNT")
ENVIRONMENT = os.getenv("OANDA_ENV", "live")     # "live" or "practice"
INSTRUMENT  = os.getenv("INSTRUMENT", "US30_USD")
OUT_DIR     = Path(os.getenv("OUT_DIR", "out"))

if not OANDA_KEY or not ACCOUNT_ID:
    raise RuntimeError("Missing OANDA_KEY or OANDA_ACCOUNT in .env")

OUT_DIR.mkdir(parents=True, exist_ok=True)

# non-interactive backend (saves PNG). If you want a pop-up chart, comment this out.
plt.switch_backend("Agg")

def iso_z(dt: datetime) -> str:
    # OANDA expects ISO8601 with 'Z' for UTC
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

def fetch_recent_m1(api: API, instrument: str, total: int = 1500) -> pd.DataFrame:
    """
    Try to fetch ~`total` 1-minute candles using a single from/to window.
    If fewer than requested are returned (e.g., due to market hours),
    you still get as many as available.
    """
    # ask for a bit more than needed to be safe
    minutes_needed = total + 60
    to_time = datetime.now(timezone.utc)
    from_time = to_time - timedelta(minutes=minutes_needed)

    params = {
        "granularity": "M1",
        "from": iso_z(from_time),
        "to": iso_z(to_time),
        "price": "M",   # midpoint candles; 'B' (bid) or 'A' (ask) are also valid
    }

    r = InstrumentsCandles(instrument=instrument, params=params)
    api.request(r)
    raw = r.response.get("candles", [])

    rows = []
    for c in raw:
        # only use completed candles (avoid the currently forming minute)
        if c.get("complete"):
            t = c["time"]
            o = float(c["mid"]["o"])
            h = float(c["mid"]["h"])
            l = float(c["mid"]["l"])
            cl = float(c["mid"]["c"])
            v = c.get("volume", 0)  # OANDA volume for CFDs = tick count (activity proxy)
            rows.append((t, o, h, l, cl, v))

    if not rows:
        return pd.DataFrame(columns=["time","o","h","l","c","v"]).set_index("time")

    df = pd.DataFrame(rows, columns=["time","o","h","l","c","v"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df.set_index("time", inplace=True)
    # keep most recent `total` rows
    if len(df) > total:
        df = df.tail(total)
    return df

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["ema45"] = df["c"].ewm(span=45, adjust=False).mean()
    return df

def save_csv(df: pd.DataFrame, instrument: str, out_dir: Path):
    csv_path = out_dir / f"{instrument}_M1_history.csv"
    df.to_csv(csv_path)
    print(f"✔ CSV saved: {csv_path}")

def save_png(df: pd.DataFrame, instrument: str, out_dir: Path):
    """
    Two-panel chart:
      - Top: price (M1) + EMA(45)
      - Bottom: volume (tick volume)
    X-axis labeled in Europe/London for readability.
    """
    if df.empty:
        print("No data to plot.")
        return

    # Convert index to Europe/London for display
    try:
        local = df.copy()
        local.index = local.index.tz_convert("Europe/London")
    except Exception:
        # if index tz isn't set for some reason
        local = df.copy()
        local.index = local.index.tz_localize("UTC").tz_convert("Europe/London")

    fig, (ax_price, ax_vol) = plt.subplots(2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios":[3,1]})

    # --- price panel (simple candle sticks via vlines for wicks & thick bodies) ---
    ax_price.vlines(local.index, local["l"], local["h"], linewidth=1)
    up = local["c"] >= local["o"]
    down = ~up
    ax_price.vlines(local.index[up],   local.loc[up, "o"],   local.loc[up, "c"], linewidth=6)
    ax_price.vlines(local.index[down], local.loc[down, "c"], local.loc[down, "o"], linewidth=6)

    # EMA(45)
    ax_price.plot(local.index, local["ema45"], linewidth=1.2, label="EMA(45)")
    ax_price.set_title(f"{instrument} — 1m with EMA(45) & Volume")
    ax_price.set_ylabel("Price")
    ax_price.legend(loc="upper left")

    # --- volume panel ---
    ax_vol.bar(local.index, local["v"], width=0.0008)  # width tuned for dense minutes
    ax_vol.set_ylabel("Volume\n(tick)")
    ax_vol.set_xlabel("Time (Europe/London)")

    fig.autofmt_xdate()
    plt.tight_layout()

    ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M")
    png_path = out_dir / f"{instrument}_M1_{ts}.png"
    plt.savefig(png_path, dpi=140)
    plt.close(fig)
    print(f"✔ PNG saved: {png_path}")

def main():
    api = API(access_token=OANDA_KEY, environment=ENVIRONMENT)

    print(f"📥 Fetching ~1500 M1 bars for {INSTRUMENT} ({ENVIRONMENT}) …")
    df = fetch_recent_m1(api, INSTRUMENT, total=1500)
    if df.empty:
        print("No candles returned (market closed or instrument unavailable).")
        return

    df = add_indicators(df)
    save_csv(df, INSTRUMENT, OUT_DIR)
    # save_png(df, INSTRUMENT, OUT_DIR)

if __name__ == "__main__":
    main()
