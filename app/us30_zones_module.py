"""
us30_zones_module.py
--------------------
Reusable module for support/resistance zone discovery on US30 (or similar indices)
from 1-minute OHLCV data, using an ATR-adaptive, de-overlapped approach.

Key features
- ATR-adaptive swing detection (on resampled timeframe, default 15m)
- ATR-scaled distance clustering of swing prices
- Recency- and touch-weighted scoring with width penalty
- Non-maximum suppression to avoid overlapping/near-duplicate zones
- Clean API functions + optional CLI entry point

Data requirements
- CSV columns: time, o, h, l, c, v
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# -----------------------------
# Defaults (tuned for US30)
# -----------------------------
DEFAULT_RESAMPLE = "15T"
DEFAULT_ATR_LEN = 14          # ATR length on the resampled timeframe
DEFAULT_SCALE = 18.0          # ATR -> swing window scaling (higher = fewer swings)
DEFAULT_DIST_ALPHA = 0.9      # ATR multiplier for clustering distance
DEFAULT_MINSEP_ALPHA = 0.75   # ATR multiplier for NMS separation
DEFAULT_TOP_N = 6             # number of final zones to keep
DEFAULT_DECAY = 0.08          # recency decay (per day)

@dataclass
class Zone:
    center: float
    lower: float
    upper: float
    touches: int
    score: float
    times: List[pd.Timestamp]

    @property
    def width(self) -> float:
        return float(self.upper - self.lower)


# -----------------------------
# I/O & preprocessing
# -----------------------------
def load_minute_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.rename(columns={
        "time": "datetime", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"
    })
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    return df

def filter_month(df: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    start = pd.Timestamp(year=year, month=month, day=1)
    end = (start + pd.offsets.MonthEnd(1))
    return df.loc[start:end]

def resample_ohlc(df: pd.DataFrame, tf: str = DEFAULT_RESAMPLE) -> pd.DataFrame:
    return df.resample(tf).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum"
    }).dropna()


# -----------------------------
# Core calculations
# -----------------------------
def compute_atr(df_tf: pd.DataFrame, n: int = DEFAULT_ATR_LEN) -> pd.Series:
    high = df_tf["high"]; low = df_tf["low"]; close = df_tf["close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def detect_swings(df_tf: pd.DataFrame, atr: pd.Series,
                  min_window: int = 3, max_window: int = 12, scale: float = DEFAULT_SCALE
                  ) -> Tuple[pd.DataFrame, int, float]:
    last_atr = atr.dropna().iloc[-1] if atr.notna().any() else 60.0
    window = int(np.clip(round(float(last_atr) / float(scale)), min_window, max_window))
    roll_max = df_tf["high"].rolling(window=window*2+1, center=True).max()
    roll_min = df_tf["low"].rolling(window=window*2+1, center=True).min()
    is_high = df_tf["high"] == roll_max
    is_low  = df_tf["low"]  == roll_min

    highs = df_tf.loc[is_high, ["high","volume"]].copy()
    highs.rename(columns={"high":"price"}, inplace=True); highs["type"] = "high"
    lows = df_tf.loc[is_low, ["low","volume"]].copy()
    lows.rename(columns={"low":"price"}, inplace=True); lows["type"] = "low"

    swings = pd.concat([highs, lows]).sort_index()
    swings["time"] = swings.index
    return swings, window, float(last_atr)

def cluster_levels(swings: pd.DataFrame, atr_val: float, dist_alpha: float = DEFAULT_DIST_ALPHA) -> List[Zone]:
    if swings.empty:
        return []
    dist = max(20.0, float(atr_val) * float(dist_alpha))
    pts = swings[["price","time"]].copy().sort_values("price").reset_index(drop=True)

    zones: List[Zone] = []
    bucket_prices = [float(pts.iloc[0]["price"])]
    bucket_times  = [pd.Timestamp(pts.iloc[0]["time"])]
    for i in range(1, len(pts)):
        p = float(pts.iloc[i]["price"])
        if abs(p - np.mean(bucket_prices)) <= dist:
            bucket_prices.append(p); bucket_times.append(pd.Timestamp(pts.iloc[i]["time"]))
        else:
            center = float(np.mean(bucket_prices))
            span = max(dist/2, (max(bucket_prices) - min(bucket_prices))/2)
            zones.append(Zone(center=center, lower=center-span, upper=center+span,
                              touches=len(bucket_prices), score=0.0, times=bucket_times.copy()))
            bucket_prices = [p]; bucket_times = [pd.Timestamp(pts.iloc[i]["time"])]
    # flush last bucket
    center = float(np.mean(bucket_prices))
    span = max(dist/2, (max(bucket_prices) - min(bucket_prices))/2)
    zones.append(Zone(center=center, lower=center-span, upper=center+span,
                      touches=len(bucket_prices), score=0.0, times=bucket_times.copy()))
    return zones

def score_zones(zones: List[Zone], df_tf: pd.DataFrame, decay_lambda: float = DEFAULT_DECAY) -> List[Zone]:
    if not zones:
        return zones
    now_ts = df_tf.index.max().to_pydatetime()
    # touches + recency, penalize width slightly
    raw_scores = []
    for z in zones:
        ages = [(now_ts - t.to_pydatetime()).total_seconds()/(3600*24) for t in z.times]
        recency = float(np.mean([np.exp(-decay_lambda*a) for a in ages])) if ages else 0.0
        raw = (0.7*z.touches) + (0.8*recency*10) - 0.003*z.width
        raw_scores.append(raw)
        z.score = raw
    s = np.array(raw_scores)
    if s.std() > 1e-9:
        s = (s - s.mean()) / s.std()
    # store normalized
    for z, sc in zip(zones, s):
        z.score = float(sc)
    return zones

def nms_prune(zones: List[Zone], min_sep: float) -> List[Zone]:
    zones_sorted = sorted(zones, key=lambda z: z.score, reverse=True)
    kept: List[Zone] = []
    for z in zones_sorted:
        if not kept:
            kept.append(z); continue
        too_close = any(abs(z.center - k.center) < min_sep for k in kept)
        overlaps = any((z.lower <= k.upper) and (z.upper >= k.lower) for k in kept)
        if not too_close and not overlaps:
            kept.append(z)
    return kept


# -----------------------------
# Public API
# -----------------------------
def compute_zones(df_tf: pd.DataFrame,
                  atr_len: int = DEFAULT_ATR_LEN,
                  scale: float = DEFAULT_SCALE,
                  dist_alpha: float = DEFAULT_DIST_ALPHA,
                  minsep_alpha: float = DEFAULT_MINSEP_ALPHA,
                  top_n: int = DEFAULT_TOP_N,
                  decay_lambda: float = DEFAULT_DECAY
                  ) -> Tuple[List[Zone], Dict[str, float]]:
    atr = compute_atr(df_tf, n=atr_len)
    atr_val = float(atr.dropna().iloc[-1]) if atr.notna().any() else 80.0
    swings, swing_window, _ = detect_swings(df_tf, atr, scale=scale)
    zones = cluster_levels(swings, atr_val, dist_alpha=dist_alpha)
    zones = score_zones(zones, df_tf, decay_lambda=decay_lambda)
    min_sep = max(30.0, atr_val * minsep_alpha)
    zones = nms_prune(zones, min_sep=min_sep)
    zones = sorted(zones, key=lambda z: z.score, reverse=True)[:top_n]
    meta = {"atr_val": atr_val, "swing_window": swing_window, "kept": len(zones)}
    return zones, meta

def zones_to_dataframe(zones: List[Zone]) -> pd.DataFrame:
    return pd.DataFrame([{
        "zone_center": round(z.center, 2),
        "lower": round(z.lower, 2),
        "upper": round(z.upper, 2),
        "touches": z.touches,
        "width": round(z.width, 1),
        "score": round(z.score, 3)
    } for z in sorted(zones, key=lambda z: z.center)])

def analyze_month(csv_path: str, year: int, month: int,
                  resample_tf: str = DEFAULT_RESAMPLE,
                  **kwargs) -> Tuple[pd.DataFrame, Dict[str, float], pd.DataFrame]:
    df_min = load_minute_csv(csv_path)
    df_month = filter_month(df_min, year, month)
    if df_month.empty:
        raise ValueError("No data found in the selected month.")
    df_tf = resample_ohlc(df_month, tf=resample_tf)
    zones, meta = compute_zones(df_tf, **kwargs)
    return df_tf, meta, zones_to_dataframe(zones)

def plot_zones(df_tf: pd.DataFrame, zones_df: pd.DataFrame, title: str, save_path: Optional[str] = None):
    plt.figure(figsize=(14, 6))
    plt.plot(df_tf.index, df_tf["close"], label="Price")
    for _, z in zones_df.iterrows():
        plt.axhspan(z["lower"], z["upper"], alpha=0.25)
    plt.title(title); plt.xlabel("Date"); plt.ylabel("Price"); plt.legend(); plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=140)
    plt.show()


# -----------------------------
# Optional CLI
# -----------------------------
def _cli():
    p = argparse.ArgumentParser(description="US30 zone discovery (improved)")
    p.add_argument("--csv", required=True, help="Path to 1-min CSV (time,o,h,l,c,v)")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--month", type=int, required=True)
    p.add_argument("--resample", type=str, default=DEFAULT_RESAMPLE)
    p.add_argument("--top_n", type=int, default=DEFAULT_TOP_N)
    p.add_argument("--atr_len", type=int, default=DEFAULT_ATR_LEN)
    p.add_argument("--scale", type=float, default=DEFAULT_SCALE)
    p.add_argument("--dist_alpha", type=float, default=DEFAULT_DIST_ALPHA)
    p.add_argument("--minsep_alpha", type=float, default=DEFAULT_MINSEP_ALPHA)
    p.add_argument("--decay", type=float, default=DEFAULT_DECAY)
    p.add_argument("--outprefix", type=str, default="zones")
    args = p.parse_args()

    df_tf, meta, zdf = analyze_month(
        args.csv, args.year, args.month, resample_tf=args.resample,
        top_n=args.top_n, atr_len=args.atr_len, scale=args.scale,
        dist_alpha=args.dist_alpha, minsep_alpha=args.minsep_alpha, decay_lambda=args.decay
    )
    csv_out = f"{args.outprefix}_{args.year}-{args.month:02d}.csv"
    png_out = f"{args.outprefix}_{args.year}-{args.month:02d}.png"
    zdf.to_csv(csv_out, index=False)
    title = f"US30 — {args.year}-{args.month:02d} (Improved Zones) | ATR≈{meta['atr_val']:.1f}, window={meta['swing_window']}, kept={meta['kept']}"
    plot_zones(df_tf, zdf, title, save_path=png_out)
    print(title)
    print(zdf.to_string(index=False))
    print(f"Saved: {csv_out}\nSaved: {png_out}")


if __name__ == "__main__":
    _cli()
