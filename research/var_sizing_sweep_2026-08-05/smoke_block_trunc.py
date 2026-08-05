import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')
sys.path.insert(0, r'c:\GitHub\investment-research-sp500\research\var_sizing_sweep_2026-08-05')
import pandas as pd, yaml
import analysis.universe as universe
from analysis.backtest import precompute_ticker, get_price_df
from var_sizing_engine import simulate_var_sizing_variant

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

end_full = pd.Timestamp("2019-06-30")
candidate_tickers = universe.get_live_stock_candidates(as_of=end_full.date())[:40]
gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
gspc_df.index = pd.to_datetime(gspc_df.index)
gspc_close_full = gspc_df["close"].astype(float)

caches = []
for ticker in candidate_tickers:
    c = precompute_ticker(ticker, CONFIG)
    caches.append(c)  # full, unsliced history -- block truncation happens inside engine

block_start = pd.Timestamp("2017-01-01")
block_end = pd.Timestamp("2017-06-30")
gspc_close = gspc_close_full[(gspc_close_full.index <= block_end + pd.Timedelta(days=5))]

trades = simulate_var_sizing_variant(
    caches, CONFIG, gspc_close, 0.05, 0.5,
    var_confidence_override=None, scaling_exponent=0.5,
    block_start=block_start, block_end=block_end,
)
print("n trades:", len(trades))
bad = [t for t in trades if pd.Timestamp(t["exit_date"]) > block_end or pd.Timestamp(t["entry_date"]) < block_start]
print("trades outside block window:", len(bad))
eod = [t for t in trades if t["exit_reason"] == "END_OF_DATA"]
print("END_OF_DATA exits:", len(eod), "sample exit dates:", sorted(set(str(t["exit_date"]) for t in eod)))
