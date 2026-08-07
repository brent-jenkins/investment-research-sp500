"""Fetch ^VIX daily close history for research use only. Not part of
production ingestion -- data.ingestion.py / config.yaml reference_tickers
are untouched by this. Written once, cached as CSV, per this project's
research reproducibility standard (external data backing a research
finding must be preserved locally, not re-fetched implicitly)."""
import yfinance as yf

df = yf.download("^VIX", start="2006-01-01", auto_adjust=False, progress=False)
df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
df = df.reset_index()
df.columns = [str(c).lower() for c in df.columns]
df = df[["date", "close"]].rename(columns={"close": "vix_close"})
df.to_csv("research/vix_ma_giveback_2026-08-07/vix_history.csv", index=False)
print(f"Rows: {len(df)}  Range: {df['date'].min()} -> {df['date'].max()}")
print(df.tail())
