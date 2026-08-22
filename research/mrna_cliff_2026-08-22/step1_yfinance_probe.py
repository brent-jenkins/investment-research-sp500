"""Part 1 (a)-(d): classify the MRNA 2026-08-18 -> 2026-08-19 close cliff.

Read-only. Fetches straight from yfinance; touches no database.
"""
import sys
import pandas as pd
import yfinance as yf

pd.set_option("display.width", 200)
pd.set_option("display.max_rows", 200)

TICKER = "MRNA"

t = yf.Ticker(TICKER)

print("=" * 78)
print("(a) .splits  — full history")
print("=" * 78)
splits = t.splits
if splits is None or len(splits) == 0:
    print("EMPTY — no split events at all in yfinance's record for this ticker.")
else:
    print(splits.to_string())
    print()
    print("--- splits in 2026 ---")
    s26 = splits[splits.index >= "2026-01-01"]
    print("NONE in 2026" if len(s26) == 0 else s26.to_string())
    print()
    print("--- splits within +/-30d of 2026-08-19 ---")
    lo, hi = pd.Timestamp("2026-07-20"), pd.Timestamp("2026-09-18")
    idx = splits.index
    if getattr(idx, "tz", None) is not None:
        lo, hi = lo.tz_localize(idx.tz), hi.tz_localize(idx.tz)
    near = splits[(idx >= lo) & (idx <= hi)]
    print("NONE" if len(near) == 0 else near.to_string())

print()
print("=" * 78)
print("actions (splits + dividends together) since 2026-01-01")
print("=" * 78)
try:
    acts = t.actions
    if acts is None or len(acts) == 0:
        print("EMPTY")
    else:
        print(acts[acts.index >= "2026-01-01"].to_string())
except Exception as e:  # pragma: no cover - diagnostic
    print(f"actions unavailable: {e!r}")

print()
print("=" * 78)
print("capital gains / other distributions")
print("=" * 78)
for attr in ("capital_gains", "dividends"):
    try:
        v = getattr(t, attr)
        if v is None or len(v) == 0:
            print(f"{attr}: EMPTY")
        else:
            print(f"{attr}: last 5 ->")
            print(v.tail(5).to_string())
    except Exception as e:  # pragma: no cover
        print(f"{attr}: unavailable ({e!r})")

print()
print("=" * 78)
print("(b)+(d) FRESH pull, auto_adjust=False  — close vs adj_close around the cliff")
print("=" * 78)
df = yf.download(
    TICKER,
    start="2026-07-25",
    end="2026-08-22",
    auto_adjust=False,
    actions=True,
    progress=False,
)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)
print(df.to_string())

print()
print("--- consecutive-day ratios (fresh pull) ---")
out = pd.DataFrame(index=df.index)
for col in ("Close", "Adj Close"):
    if col in df.columns:
        out[f"{col} ratio"] = (df[col] / df[col].shift(1)).round(4)
out["Close"] = df["Close"].round(2)
if "Adj Close" in df.columns:
    out["Adj Close"] = df["Adj Close"].round(2)
print(out.to_string())
