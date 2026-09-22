# fetch-stream.py
# Stream OANDA ticks (US30_USD) → aggregate 1m OHLC → EMA(45) → autosave chart & CSV

import os
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

from dotenv import load_dotenv
from oandapyV20 import API
from oandapyV20.endpoints.pricing import PricingStream

# ---------- setup ----------
load_dotenv()
OANDA_KEY = os.getenv("OANDA_KEY")
ACCOUNT_ID = os.getenv("OANDA_ACCOUNT")
if not OANDA_KEY or not ACCOUNT_ID:
    raise RuntimeError("Missing OANDA_KEY or OANDA_ACCOUNT in .env")

INSTRUMENT = os.getenv("INSTRUMENT", "US30_USD")   # change to BTC_USD on weekends if you like
ENVIRONMENT = os.getenv("OANDA_ENV", "live")       # "live" or "practice"

OUT_DIR = Path(os.getenv("OUT_DIR", "out"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

# live chart config (non-interactive backend is fine; comment next line if you want a popup window)
plt.switch_backend("Agg")

# in-memory bars as a DataFrame
bars = pd.DataFrame(columns=["minute","o","h","l","c"]).set_index("minute")
last_closed_minute = None

def add_tick_to_bars(price: float, ts: datetime):
    """Aggregate ticks into 1-minute OHLC. Returns True if a bar just closed."""
    global bars, last_closed_minute

    # normalize to minute
    m = ts.replace(second=0, microsecond=0, tzinfo=timezone.utc)

    # if we rolled into a new minute, we can consider previous one 'closed'
    bar_closed = False
    if len(bars) and m > bars.index[-1]:
        bar_closed = True
        last_closed_minute = bars.index[-1]

    if m not in bars.index:
        bars.loc[m, ["o","h","l","c"]] = [price, price, price, price]
    else:
        bars.loc[m, "h"] = max(bars.loc[m, "h"], price)
        bars.loc[m, "l"] = min(bars.loc[m, "l"], price)
        bars.loc[m, "c"] = price

    return bar_closed

def compute_ema(df: pd.DataFrame, length: int = 45) -> pd.Series:
    return df["c"].ewm(span=length, adjust=False).mean()

def save_csv(df: pd.DataFrame):
    csv_path = OUT_DIR / f"{INSTRUMENT}_M1.csv"
    df.to_csv(csv_path)

def save_chart(df: pd.DataFrame, title_note: str = ""):
    if df.empty:
        return
    # Convert index (UTC) to Europe/London for labels
    local = df.copy()
    local.index = local.index.tz_convert("Europe/London")

    # Compute EMA(45)
    local["ema45"] = compute_ema(local)

    # Basic candle plot (wicks + bodies) + EMA
    fig, ax = plt.subplots(figsize=(12, 6))

    # wicks
    ax.vlines(local.index, local["l"], local["h"], linewidth=1)
    # bodies (use a simple line thickness to emulate bodies; keeps matplotlib minimal)
    up = local["c"] >= local["o"]
    down = ~up
    ax.vlines(local.index[up], local.loc[up, "o"], local.loc[up, "c"], linewidth=6)
    ax.vlines(local.index[down], local.loc[down, "c"], local.loc[down, "o"], linewidth=6)

    # EMA
    ax.plot(local.index, local["ema45"], linewidth=1.2, label="EMA(45)")

    ax.set_title(f"{INSTRUMENT} — 1m with EMA(45) {title_note}")
    ax.set_xlabel("Time (Europe/London)")
    ax.set_ylabel("Price")
    ax.legend()
    fig.autofmt_xdate()
    plt.tight_layout()

    ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M")
    png_path = OUT_DIR / f"{INSTRUMENT}_{ts}.png"
    plt.savefig(png_path, dpi=140)
    plt.close(fig)

def stream():
    api = API(access_token=OANDA_KEY, environment=ENVIRONMENT)
    ps = PricingStream(accountID=ACCOUNT_ID, params={"instruments": INSTRUMENT})
    print(f"📡 Streaming {INSTRUMENT} ({ENVIRONMENT}) … Ctrl+C to stop")

    try:
        for msg in api.request(ps):
            now_utc = datetime.now(timezone.utc)

            # Heartbeats prove the connection is alive
            if msg.get("type") == "HEARTBEAT":
                # print("♥", msg["time"])   # uncomment for verbose
                continue

            if msg.get("type") == "PRICE":
                # Use mid price for smoother candles
                bid = float(msg["bids"][0]["price"])
                ask = float(msg["asks"][0]["price"])
                mid = (bid + ask) / 2.0

                ts = datetime.fromisoformat(msg["time"].replace("Z", "+00:00"))
                just_closed = add_tick_to_bars(mid, ts)

                # When a minute closes, save CSV and a chart of the last N bars
                if just_closed:
                    # Keep last ~500 bars (about 8 hours of 1m)
                    keep = 500
                    if len(bars) > keep:
                        trim = len(bars) - keep
                        trimmed = bars.iloc[trim:].copy()
                        bars.drop(bars.index[:trim], inplace=True)
                        # (optional) you could persist older bars to a separate file here

                    save_csv(bars)
                    # Save a chart of the last ~200 bars for readability
                    save_chart(bars.tail(200), title_note="(autosave)")

                # Optional: print the *current* building candle
                if len(bars):
                    print(INSTRUMENT, bars.iloc[-1].to_dict(), "now:", now_utc.strftime("%H:%M:%S UTC"))

    except KeyboardInterrupt:
        print("\n🛑 Stopping stream. Writing final CSV & chart …")
        save_csv(bars)
        save_chart(bars.tail(200), title_note="(final)")
        print(f"✔ Saved to {OUT_DIR.resolve()}")

if __name__ == "__main__":
    stream()
