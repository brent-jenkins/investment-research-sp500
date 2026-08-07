"""Diagnostic only -- no backtest engine involved. Checks whether VIX's
long-run distribution over the real 2007-2026 backtest window supports
min(1, 20/VIX) as a sensible (not overfit) centering constant, and
quantifies how often/how hard the dampener actually bites."""
import pandas as pd

df = pd.read_csv("research/vix_ma_giveback_2026-08-07/vix_history.csv", parse_dates=["date"])
window = df[(df["date"] >= "2007-01-01") & (df["date"] <= "2026-08-06")].copy()

print(f"n={len(window)}")
print(window["vix_close"].describe())
print()

for pct in [10, 20, 25, 30, 40, 50, 60, 70, 80, 90, 95, 99]:
    print(f"  p{pct}: {window['vix_close'].quantile(pct/100):.2f}")

window["dampener"] = (20.0 / window["vix_close"]).clip(upper=1.0)
print()
print("dampener (min(1, 20/VIX)) distribution:")
print(window["dampener"].describe())
print(f"  fraction of days dampener == 1.0 (no effect): {(window['dampener'] >= 0.999).mean():.1%}")
print(f"  fraction of days dampener < 0.5 (size more than halved): {(window['dampener'] < 0.5).mean():.1%}")
print(f"  fraction of days dampener < 0.25: {(window['dampener'] < 0.25).mean():.1%}")

# Era breakdown, since coverage/regime collinearity matters project-wide.
window["era"] = pd.cut(
    window["date"],
    bins=[pd.Timestamp("2007-01-01"), pd.Timestamp("2010-01-01"), pd.Timestamp("2020-01-01"), pd.Timestamp("2026-12-31")],
    labels=["2007-2009 GFC", "2010-2019 bull", "2020-2026 recent"],
)
print()
print(window.groupby("era", observed=True)["vix_close"].agg(["mean", "median", "count"]))
print()
print(window.groupby("era", observed=True)["dampener"].mean())
