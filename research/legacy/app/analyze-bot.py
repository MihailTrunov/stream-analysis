# analyze_bot.py
import os, json
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI
from datetime import datetime

load_dotenv()

# --- CONFIG ---
DATA_FILE = "./out/US30_USD_M1_history.csv"
OUT_FILE  = "../out/analysis_latest.json"
MODEL     = "gpt-5"
BARS      = 200   # number of most recent bars to send for context

# --- Load data ---
df = pd.read_csv(DATA_FILE, parse_dates=["time"])
if len(df) > BARS:
    df = df.tail(BARS)

import numpy as np

# keep only the last N bars
if len(df) > BARS:
    df = df.tail(BARS).copy()

# ensure 'time' is ISO8601 strings and replace NaN with None
df["time"] = pd.to_datetime(df["time"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
df = df.replace({np.nan: None})

bars_records = df.to_dict(orient="records")

payload = {
    "instrument": "US30_USD",
    "granularity": "M1",
    "bars": bars_records
}

# --- Prepare OpenAI client ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Create a system prompt (the "personality" of the model) ---
system_prompt = """You are a quantitative trading analyst.
Analyze the provided OHLCV data for trend, momentum, and potential setups.
Return a concise JSON response with:
- trend_direction ("uptrend"|"downtrend"|"sideways")
- momentum_strength ("strong"|"moderate"|"weak")
- possible_action (e.g. "buy retracement", "short breakdown", "stay flat")
- key_levels: {"support": [...], "resistance": [...]}
- suggested_entry, stop_loss, take_profit (approximate)
- commentary: short textual explanation
"""

# --- Ask GPT for analysis ---
response = client.chat.completions.create(
    model=MODEL,
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload)}
    ],
    temperature=0.3
)

analysis_text = response.choices[0].message.content
print("🔍 GPT-5 response:\n", analysis_text)

# Save the structured response
timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
output = {"timestamp": timestamp, "analysis": analysis_text}

with open(OUT_FILE, "w") as f:
    json.dump(output, f, indent=2)
print(f"✔ Analysis saved to {OUT_FILE}")
