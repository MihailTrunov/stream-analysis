#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
US30 Zone Discovery — Improved (ATR-adaptive, de-overlapped)
------------------------------------------------------------
- Loads 1-minute OHLCV CSV with columns: time, o, h, l, c, v
- Filters a target month
- Resamples to 15-minute candles
- Detects swings with ATR-adaptive window
- Clusters swing prices by ATR-scaled distance
- Scores by touches + recency (time-decay) and penalizes width
- Prunes with non-maximum suppression to avoid overlap / crowding
- Outputs:
  * zones CSV
  * chart PNG
  * printed table
Author: ChatGPT (GPT-5 Thinking)
"""

import argparse
import os
from datetime import datetime
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# -----------------------------
# Parameters (defaults are sensible for US30)
# -----------------------------
DEFAULT_RESAMPLE = "15T"
DEFAULT_ATR_LEN = 14         # ATR length on 15-min
DEFAULT_SCALE = 18.0         # ATR -> swing window scaling (larger = fewer swings)
DEFAULT_DIST_ALPHA = 0.9     # ATR multiplier for clustering distance
DEFAULT_MINSEP_ALPHA = 0.75  # ATR multiplier for min separation in NMS
DEFAULT_TOP_N = 6            # cap number of zones kept
DEFAULT_DECAY = 0.08         # recency decay lambda (per day)


# -----------------------------
# Core utilities
# -----------------------------
def load_minute_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.rename(columns={"time": "datetime", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
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

def compute_atr(df_tf: pd.DataFrame, n: int = DEFAULT_ATR_LEN) -> pd.Series:
    high = df_tf["high"]
    low = df_tf["low"]
    close = df_tf["close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def detect_swings(df_tf: pd.DataFrame, atr: pd.Series, min_window: int = 3, max_window: int = 12, scale: float = DEFAULT_SCALE):
    # Window grows with ATR -> fewer swings in volatile regimes
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

def cluster_levels(swings: pd.DataFrame, atr_val: float, dist_alpha: float = DEFAULT_DIST_ALPHA):
    if swings.empty:
        return []
    dist = max(20.0, float(atr_val) * float(dist_alpha))
    pts = swings[["price","time"]].copy().sort_values("price").reset_index(drop=True)

    clusters = []
    bucket_prices = [float(pts.iloc[0]["price"])]
    bucket_times  = [pd.Timestamp(pts.iloc[0]["time"])]
    for i in range(1, len(pts)):
        p = float(pts.iloc[i]["price"])
        if abs(p - np.mean(bucket_prices)) <= dist:
            bucket_prices.append(p); bucket_times.append(pd.Timestamp(pts.iloc[i]["time"]))
        else:
            center = np.mean(bucket_prices)
            span = max(dist/2, (max(bucket_prices) - min(bucket_prices))/2)
            clusters.append({
                "center": center,
                "lower": center - span,
                "upper": center + span,
                "touches": len(bucket_prices),
                "times": bucket_times.copy()
            })
            bucket_prices = [p]; bucket_times = [pd.Timestamp(pts.iloc[i]["time"])]

    # flush last bucket
    center = np.mean(bucket_prices)
    span = max(dist/2, (max(bucket_prices) - min(bucket_prices))/2)
    clusters.append({
        "center": center,
        "lower": center - span,
        "upper": center + span,
        "touches": len(bucket_prices),
        "times": bucket_times.copy()
    })
    return clusters

def score_zones(zones: list, df_tf: pd.DataFrame, decay_lambda: float = DEFAULT_DECAY):
    if not zones:
        return zones
    now_ts = df_tf.index.max().to_pydatetime()
    for z in zones:
        ages = [(now_ts - t.to_pydatetime()).total_seconds()/(3600*24) for t in z["times"]]
        rec = float(np.mean([np.exp(-decay_lambda*a) for a in ages])) if ages else 0.0
        z["recency"] = rec
        z["width"] = z["upper"] - z["lower"]
        # touches + recency, small penalty for excessive width
        z["score_raw"] = (0.7*z["touches"]) + (0.8*rec*10) - 0.003*z["width"]
    # z-score normalize for robust sorting
    s = np.array([z["score_raw"] for z in zones])
    if s.std() > 1e-9:
        s_norm = (s - s.mean()) / s.std()
    else:
        s_norm = s
    for i, z in enumerate(zones):
        z["score"] = float(s_norm[i])
    return zones

def nms_prune(zones: list, min_sep: float):
    # Keep best-scored zones, drop those that overlap or are too close
    zones_sorted = sorted(zones, key=lambda z: z["score"], reverse=True)
    kept = []
    for z in zones_sorted:
        if not kept:
            kept.append(z); continue
        too_close = any(abs(z["center"] - k["center"]) < min_sep for k in kept)
        overlaps = any((z["lower"] <= k["upper"]) and (z["upper"] >= k["lower"]) for k in kept)
        if not too_close and not overlaps:
            kept.append(z)
    return kept

def compute_zones(df_tf: pd.DataFrame,
                  atr_len: int = DEFAULT_ATR_LEN,
                  scale: float = DEFAULT_SCALE,
                  dist_alpha: float = DEFAULT_DIST_ALPHA,
                  minsep_alpha: float = DEFAULT_MINSEP_ALPHA,
                  top_n: int = DEFAULT_TOP_N,
                  decay_lambda: float = DEFAULT_DECAY):
    atr = compute_atr(df_tf, n=atr_len)
    atr_val = float(atr.dropna().iloc[-1]) if atr.notna().any() else 80.0
    swings, swing_window, last_atr = detect_swings(df_tf, atr, scale=scale)
    zones = cluster_levels(swings, atr_val, dist_alpha=dist_alpha)
    zones = score_zones(zones, df_tf, decay_lambda=decay_lambda)
    min_sep = max(30.0, atr_val * minsep_alpha)
    zones = nms_prune(zones, min_sep=min_sep)
    zones = sorted(zones, key=lambda z: z["score"], reverse=True)[:top_n]
    meta = {"atr_val": atr_val, "swing_window": swing_window, "kept": len(zones)}
    return zones, meta

def zones_to_dataframe(zones: list) -> pd.DataFrame:
    return pd.DataFrame([{
        "zone_center": round(z["center"], 2),
        "lower": round(z["lower"], 2),
        "upper": round(z["upper"], 2),
        "touches": z["touches"],
        "width": round(z["upper"] - z["lower"], 1),
        "score": round(z["score"], 3)
    } for z in sorted(zones, key=lambda z: z["center"])])


# -----------------------------
# Plotting
# -----------------------------
def plot_zones(df_tf: pd.DataFrame, zones: list, title: str, outfile_png: str = None):
    plt.figure(figsize=(14, 6))
    plt.plot(df_tf.index, df_tf["close"], label="Price")
    for z in zones:
        plt.axhspan(z["lower"], z["upper"], alpha=0.25)
    plt.title(title)
    plt.xlabel("Date"); plt.ylabel("Price"); plt.legend(); plt.tight_layout()
    if outfile_png:
        plt.savefig(outfile_png, dpi=140)
    plt.show()


# -----------------------------
# Main CLI
# -----------------------------
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=str, required=True, help="Path to 1-minute OHLCV CSV (time,o,h,l,c,v)")
    p.add_argument("--year", type=int, required=True, help="Target year (e.g., 2025)")
    p.add_argument("--month", type=int, required=True, help="Target month number (1-12)")
    p.add_argument("--resample", type=str, default=DEFAULT_RESAMPLE, help="Resample timeframe (default: 15T)")
    p.add_argument("--top_n", type=int, default=DEFAULT_TOP_N, help="Max zones to keep")
    p.add_argument("--atr_len", type=int, default=DEFAULT_ATR_LEN, help="ATR length (default 14)")
    p.add_argument("--scale", type=float, default=DEFAULT_SCALE, help="ATR -> swing-window scale (higher=fewer swings)")
    p.add_argument("--dist_alpha", type=float, default=DEFAULT_DIST_ALPHA, help="ATR multiplier for clustering distance")
    p.add_argument("--minsep_alpha", type=float, default=DEFAULT_MINSEP_ALPHA, help="ATR multiplier for NMS min separation")
    p.add_argument("--decay", type=float, default=DEFAULT_DECAY, help="Recency decay lambda (per day)")
    p.add_argument("--outprefix", type=str, default="zones_output", help="Prefix for output files (CSV/PNG)")
    args = p.parse_args()

    df_min = load_minute_csv(args.csv)
    df_month = filter_month(df_min, args.year, args.month)
    if df_month.empty:
        raise SystemExit("No data in the selected month.")

    df_tf = resample_ohlc(df_month, tf=args.resample)

    zones, meta = compute_zones(
        df_tf,
        atr_len=args.atr_len,
        scale=args.scale,
        dist_alpha=args.dist_alpha,
        minsep_alpha=args.minsep_alpha,
        top_n=args.top_n,
        decay_lambda=args.decay,
    )

    out_csv = f"{args.outprefix}_{args.year}-{args.month:02d}.csv"
    out_png = f"{args.outprefix}_{args.year}-{args.month:02d}.png"

    # Save CSV + Plot
    zdf = zones_to_dataframe(zones)
    zdf.to_csv(out_csv, index=False)
    title = f"US30 — {args.year}-{args.month:02d} (Improved Zones)\nATR≈{meta['atr_val']:.1f} | swing window={meta['swing_window']} | kept={meta['kept']}"
    plot_zones(df_tf, zones, title=title, outfile_png=out_png)

    # Print summary
    print("\n" + title)
    print(zdf.to_string(index=False))
    print(f"\nSaved: {os.path.abspath(out_csv)}")
    print(f"Saved: {os.path.abspath(out_png)}")


if __name__ == "__main__":
    main()
