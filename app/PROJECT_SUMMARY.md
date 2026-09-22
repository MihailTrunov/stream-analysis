# 🧠 US30 EMA-45 Trend Leg Analysis System  
### Project Summary — October 2025

---

## 🎯 Objective
This project builds a semi-automated system for analyzing **US30 (Dow Jones Industrial Average)** 1-minute price data to detect and study **trend “legs”** — sustained directional price movements identified through **EMA-45** cross logic.

The ultimate goal is to identify **reversal zones**, **intraday timing patterns**, and **market rhythm** by aggregating and analyzing these directional legs over time.

---

## 📊 Data Overview

### Source
- **Instrument:** US30_USD (Dow Jones Index CFD)
- **Interval:** 1-minute candles  
- **Period covered:** April → October 2025  
- **Trading window used:** ~12:00 – 20:00 UTC (London +1 in summer)
- **Provider:** OANDA REST API (historical data)

---

## ⚙️ Trend Leg Definition

Each **trend leg** is defined as:
- Starting when price **crosses EMA-45** and stays on one side,  
- Ending when it **crosses back** to the opposite side.

### Detection Filters
| Parameter | Condition | Purpose |
|------------|------------|---------|
| Duration | ≥ 30 bars (30 minutes) | Avoid short-lived spikes |
| Net move | ≥ 70 points | Ensure meaningful movement |
| Retracement tolerance | ≤ 35 points | Allow small counter-moves |

### Data Fields (per leg)
| Field | Description |
|-------|--------------|
| `dir` | Direction relative to EMA-45 (UP / DOWN) |
| `start_time`, `end_time` | UTC timestamps |
| `bars` | Number of 1-minute candles between crossings |
| `net_pts` | Price difference between start and end closes |
| `span_pts` | High-low range within leg |
| `efficiency_%` | ( \|net_pts\| ÷ span_pts × 100 ) — trend cleanliness |
| `start_time_london`, `end_time_london` | Converted to Europe/London (BST/GMT) |

---

## 🧮 Data Processing Steps

1. **Leg Extraction**  
   - Full April–October dataset processed using EMA-45 crossing logic.  
   - Result saved as `EMA45-Defined_Legs__Apr_Oct_2025__Full_Dataset_.csv`.

2. **Timezone Enrichment**  
   - Added `start_time_london` and `end_time_london` columns for local-time analysis.  
   - Output saved as `EMA45-Defined_Legs__Apr_Oct_2025__with_London.csv`.

3. **Frequency Aggregation**
   - Counted new leg starts by **10-minute** and **5-minute** time buckets (London local time).  
   - Created frequency tables and bar charts to identify clustering of new trend initiations.

---

## 📈 Key Findings (as of latest analysis)

| London Time | Observation |
|--------------|--------------|
| **08:00 – 09:00** | Clear increase in leg starts — coincides with London market open. |
| **14:30 – 15:00** | Another strong spike — aligns with New York cash open (09:30 ET). |
| **17:00 – 18:30** | Moderate second wave of activity. |
| **11:00 – 13:30** | Noticeable lull — reflects midday drift. |

These clusters indicate that major **trend initiations** often occur near session openings, while mid-session behavior is more range-bound.

---

## 📂 Files Generated

| File | Description |
|------|--------------|
| `US30_USD_M1_history_long.csv` | Raw 1-minute OHLC data (April–October 2025) |
| `EMA45-Defined_Legs__Apr_Oct_2025__Full_Dataset_.csv` | Extracted legs (UTC only) |
| `EMA45-Defined_Legs__Apr_Oct_2025__with_London.csv` | Legs dataset with UTC + London times |
| `Leg_Start_Frequency_5m.csv` | Aggregated 5-minute frequency table (optional export) |

---

## 🧭 Current Stage — *Pattern Discovery*

- The system successfully identifies and classifies trend legs.  
- London-time 5-minute distribution analysis complete.  
- Visual correlation between market opens and leg frequency confirmed.

---

## 🚀 Next Steps

1. **Reversal Pairing** — Match UP→DOWN and DOWN→UP legs to locate reversal zones.  
2. **Session Segmentation** — Compare London, US, and post-US trading session behavior.  
3. **Statistical Profiling** — Analyze distributions of efficiency, duration, and magnitude.  
4. **Automated Streaming** — Connect to OANDA live data stream for ongoing leg updates.  
5. **Visualization Dashboard** — Display leg structure, EMA, and reversal markers interactively.

---

## 🧩 Current Deliverable
The latest working dataset:
> **`EMA45-Defined_Legs__Apr_Oct_2025__with_London.csv`**

contains all legs with both UTC and London timestamps, ready for statistical and visual exploration.

---

*Prepared collaboratively in October 2025  
for continued quantitative analysis of US30 intraday structure.*

## Query Example

fetch-history-range.py  --instrument US30_USD  --granularity M1  --from 2025-01-01T00:00Z  --to 2025-03-31T20:00Z  --append out/US30_USD_M1_history_long.csv  --session "06:00-16:00" --session-tz "America/New_York"  --weekdays-only 