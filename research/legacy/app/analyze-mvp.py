# analyze-mvp.py
#
# MVP analyzer:
# - Loads last 24h of 1m OHLCV
# - Builds a compact tiered payload:
#     ctx (24h compressed to 5m), recent (last 30m @ 1m), EMA45 tail (6h @ 3m)*
# - Sends to OpenAI API
# - Writes out/analysis_latest.json and prints a concise summary
#
# *If you prefer EMA 2h @ 1m (~120 points), switch the marked block below.

import os, json
from datetime import timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

# -------- config --------
load_dotenv()

DATA_FILE = os.getenv("HISTORY_CSV", "out/US30_USD_M1_history.csv")  # or out/US30_USD_M1.csv
INSTRUMENT = os.getenv("INSTRUMENT", "US30_USD")
OUT_DIR = Path(os.getenv("OUT_DIR", "out"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")  # cost-effective starter
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY in .env")

DEC = int(os.getenv("PRICE_DECIMALS", "1"))  # rounding for token savings
CTX_RULE = os.getenv("CTX_RULE", "5min")     # "5min" or "3min"
CTX_HOURS = int(os.getenv("CTX_HOURS", "24"))
RECENT_MIN = int(os.getenv("RECENT_MIN", "30"))
EMA_TAIL_MODE = os.getenv("EMA_TAIL_MODE", "6h_3m")  # "6h_3m" or "2h_1m"
EMA_LEN = int(os.getenv("EMA_LEN", "120"))           # target number of EMA points

def load_m1(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=["time"])
    if df.empty:
        raise RuntimeError(f"No rows in {csv_path}")
    # enforce expected columns + types
    for col in ["o","h","l","c"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "v" in df.columns:
        df["v"] = pd.to_numeric(df["v"], errors="coerce").fillna(0).astype(int)
    else:
        df["v"] = 0
    df = df.dropna(subset=["o","h","l","c"])
    df = df.sort_values("time").set_index("time")
    # Ensure tz-aware UTC index
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df

def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    o = df["o"].resample(rule).first()
    h = df["h"].resample(rule).max()
    l = df["l"].resample(rule).min()
    c = df["c"].resample(rule).last()
    v = df["v"].resample(rule).sum()
    out = pd.concat([o,h,l,c,v], axis=1).dropna()
    out.columns = ["o","h","l","c","v"]
    return out

def round_df(x: pd.DataFrame, dec: int) -> pd.DataFrame:
    y = x.copy()
    for col in ["o","h","l","c"]:
        if col in y.columns:
            y[col] = y[col].round(dec)
    if "v" in y.columns:
        y["v"] = y["v"].astype(int)
    return y

def to_records(x: pd.DataFrame):
    # ISO minute stamps, Z suffix, compact field names
    t = x.index.strftime("%Y-%m-%dT%H:%MZ")
    recs = [
        {"t": t_i, "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": int(v)}
        for t_i, (o,h,l,c,v) in zip(t, x[["o","h","l","c","v"]].itertuples(index=False))
    ]
    return recs

def build_payload(df_m1: pd.DataFrame) -> dict:
    end = df_m1.index.max()
    start_ctx = end - timedelta(hours=CTX_HOURS)
    ctx_source = df_m1.loc[start_ctx:end]
    ctx_bars_df = resample_ohlcv(ctx_source, CTX_RULE)
    ctx_bars_df = round_df(ctx_bars_df, DEC)

    recent_df = df_m1.loc[end - timedelta(minutes=RECENT_MIN): end]
    recent_df = round_df(recent_df, DEC)

    # EMA(45) on 1m
    ema = df_m1["c"].ewm(span=45, adjust=False).mean()

    if EMA_TAIL_MODE == "6h_3m":
        ema_window = ema.loc[end - timedelta(hours=6): end].dropna()
        ema_resampled = ema_window.resample("3min").last().dropna()
        ema_vals = ema_resampled.tail(EMA_LEN).round(DEC).tolist()
        ema_t0 = ema_resampled.tail(EMA_LEN).index[0].strftime("%Y-%m-%dT%H:%MZ") if len(ema_resampled) else None
        ema_agg = "3m"
    else:  # "2h_1m"
        ema_window = ema.loc[end - timedelta(hours=2): end].dropna()
        ema_vals = ema_window.tail(EMA_LEN).round(DEC).tolist()
        ema_t0 = ema_window.tail(EMA_LEN).index[0].strftime("%Y-%m-%dT%H:%MZ") if len(ema_window) else None
        ema_agg = "1m"

    payload = {
        "sym": INSTRUMENT,
        "tz": "UTC",
        "ctx": {"agg": CTX_RULE, "bars": to_records(ctx_bars_df)},
        "recent": {"agg": "1m", "bars": to_records(recent_df)},
        "ind": {
            "ema45_1m": {
                "agg": ema_agg,
                "len": len(ema_vals),
                "t0": ema_t0,
                "vals": ema_vals
            }
        },
        "meta": {"dec": DEC, "v_is_tick": True}
    }
    return payload

def call_openai(payload: dict) -> str:
    client = OpenAI(api_key=OPENAI_API_KEY)
    system_prompt = (
        "You are a quantitative trading analyst. Use ctx (5m bars, last 24h) "
        "to determine regime & S/R; use recent (1m bars, last 30m) to propose "
        "actionable entries with SL/TP. Use ind.ema45_1m as a trend filter. "
        "Volume is tick volume. Respond strictly as compact JSON with keys: "
        "trend_direction, momentum_strength, possible_action, key_levels "
        "(support,resistance arrays), suggested_entry, stop_loss, take_profit, "
        "commentary."
    )

    user_json = json.dumps(payload, separators=(",",":"))  # compact
    resp = client.chat.completions.create(
        model=MODEL,
        # temperature=0.2,
        messages=[
            {"role":"system","content":system_prompt},
            {"role":"user","content":user_json}
        ]
    )
    return resp.choices[0].message.content

def normalize_json(s: str) -> dict:
    # Try to coerce model output into JSON
    try:
        return json.loads(s)
    except Exception:
        # strip code fences if present, then retry
        s2 = s.strip()
        if s2.startswith("```"):
            s2 = s2.strip("`")
            if s2.startswith("json"):
                s2 = s2[4:]
        s2 = s2.strip()
        return json.loads(s2)

def main():
    df = load_m1(DATA_FILE)
    payload = build_payload(df)
    raw = call_openai(payload)
    result = normalize_json(raw)

    # Save JSON result
    out_json = OUT_DIR / "analysis_latest.json"
    with open(out_json, "w") as f:
        json.dump(result, f, indent=2)
    print(f"✔ analysis saved → {out_json}")

    # Print a tiny human summary
    td = result.get("trend_direction","?")
    ms = result.get("momentum_strength","?")
    act = result.get("possible_action","?")
    entry = result.get("suggested_entry","?")
    sl = result.get("stop_loss","?")
    tp = result.get("take_profit","?")
    print(f"\n{INSTRUMENT} → trend: {td}, momentum: {ms}, action: {act}")
    print(f"Entry ~ {entry} | SL ~ {sl} | TP ~ {tp}")
    print(f"Note: {result.get('commentary','')}\n")

if __name__ == "__main__":
    main()
