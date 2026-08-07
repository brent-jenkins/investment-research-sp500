"""Follow-up to step2: user confirmed short (7/14/30d) SP500 MA windows
show a mean-reversion pattern (below-MA periods had BETTER forward
returns) -- pivot to longer, classic trend-filter windows (100/150/200d)
and pick one sensible definition, same diagnostic method as before."""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import pandas as pd

from data.ingestion import get_price_df

START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-06")

gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
gspc_df.index = pd.to_datetime(gspc_df.index)
gspc_close_full = gspc_df["close"].astype(float)
gspc_close = gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= END)]

sp = gspc_close.to_frame("close")
windows = (100, 150, 200)
for w in windows:
    sp[f"ma_{w}"] = sp["close"].rolling(w).mean()
    sp[f"above_ma_{w}"] = sp["close"] > sp[f"ma_{w}"]
sp = sp.dropna()

for w in windows:
    print(f"  above MA{w}: {sp[f'above_ma_{w}'].mean():.1%} of days")

print(f"  agreement MA100 vs MA200: {(sp['above_ma_100'] == sp['above_ma_200']).mean():.1%}")
print(f"  agreement MA150 vs MA200: {(sp['above_ma_150'] == sp['above_ma_200']).mean():.1%}")
print(f"  agreement MA100 vs MA150: {(sp['above_ma_100'] == sp['above_ma_150']).mean():.1%}")

for horizon in (20, 60, 120):
    fwd = sp["close"].shift(-horizon) / sp["close"] - 1
    for w in windows:
        above = fwd[sp[f"above_ma_{w}"]]
        below = fwd[~sp[f"above_ma_{w}"]]
        print(f"  MA{w}, fwd{horizon}d return: above={above.mean()*100:.2f}% (n={above.count()}) "
              f"vs below={below.mean()*100:.2f}% (n={below.count()})")

# How often does the regime flip (fewer flips = more usable as a stable
# gate, not a rapidly-oscillating one that would churn admission order).
for w in windows:
    flips = (sp[f"above_ma_{w}"] != sp[f"above_ma_{w}"].shift(1)).sum()
    print(f"  MA{w}: {flips} regime flips over {len(sp)} days (avg regime length {len(sp)/max(flips,1):.1f} days)")

sp.to_csv("research/vix_ma_giveback_2026-08-07/sp500_long_ma_regime_diagnostic.csv")
