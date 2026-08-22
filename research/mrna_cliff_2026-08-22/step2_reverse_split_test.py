"""Direct test of the reverse-split hypothesis, independent of .splits metadata.

A reverse split conserves market cap and DIVIDES share count and volume by the
factor. A genuine repricing on news does neither. Read-only.
"""
import pandas as pd, yfinance as yf
pd.set_option("display.width", 220)

t = yf.Ticker("MRNA")
info = t.info or {}
for k in ("shortName", "quoteType", "exchange", "currency", "marketCap",
          "sharesOutstanding", "impliedSharesOutstanding", "floatShares",
          "previousClose", "regularMarketPrice", "fiftyTwoWeekHigh", "fiftyTwoWeekLow"):
    print(f"{k:28s} {info.get(k)}")

print()
print("=== implied market cap either side of the cliff ===")
so = info.get("sharesOutstanding")
if so:
    print(f"sharesOutstanding (today)   {so:,}")
    print(f"  x 62.96 (2026-08-18 close) = ${so*62.96/1e9:,.2f}B")
    print(f"  x 174.38 (2026-08-19 close) = ${so*174.38/1e9:,.2f}B")
    print("  A reverse split conserves market cap; only ONE of these can be right for")
    print("  a constant share count, so a jump here means the share count did NOT change.")

print()
print("=== volume: baseline vs cliff day ===")
df = yf.download("MRNA", start="2026-06-01", end="2026-08-22",
                 auto_adjust=False, progress=False)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)
pre = df.loc[:"2026-08-18", "Volume"]
print(f"median daily volume 2026-06-01..2026-08-18 : {pre.median():,.0f}")
print(f"2026-08-19 volume                          : {df.loc['2026-08-19','Volume']:,.0f}")
print(f"ratio                                       : {df.loc['2026-08-19','Volume']/pre.median():.1f}x")
print("  Reverse split => volume FALLS by the factor (~0.36x). Observed direction is the opposite.")

print()
print("=== OHLC shape on the cliff day ===")
row = df.loc["2026-08-19"]
prev = df.loc["2026-08-18"]
print(f"prev close        {prev['Close']:.2f}")
print(f"open              {row['Open']:.2f}   (gap {row['Open']/prev['Close']:.4f}x)")
print(f"low               {row['Low']:.2f}")
print(f"high              {row['High']:.2f}")
print(f"close             {row['Close']:.2f}")
print(f"intraday range    {row['High']/row['Low']:.4f}x  ({(row['High']/row['Low']-1)*100:.1f}%)")
print("  A rebasing scales O/H/L/C by ONE factor, leaving intraday range normal.")
print(f"  Normal MRNA intraday range (median, 2026-06..08-18): "
      f"{(df.loc[:'2026-08-18','High']/df.loc[:'2026-08-18','Low']).median():.4f}x")
