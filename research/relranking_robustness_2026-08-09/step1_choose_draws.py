"""Choose 20 paired perturbation draws, deterministically, from the full-
history Pool 2 CLEAN cache: 10 drop_ticker (random tickers, fixed seed), 6
shift_start (a spread of small day offsets), 4 shuffle_tiebreak (random
admission-order seeds) -- the same three perturbation types the measurement
toolkit's own noise-floor instrument used, applied here to the pool the
relative-ranking study actually used (Pool 2 CLEAN), not the broader pool the
original toolkit used.
"""
import json
import pickle
import random

OUT_DIR = "research/relranking_robustness_2026-08-09"

with open(f"{OUT_DIR}/_cache_full_history.pkl", "rb") as fh:
    data = pickle.load(fh)
tickers = sorted(data["candidate_tickers"])
print(f"{len(tickers)} candidate tickers available")

rng = random.Random(20260809)
drop_tickers = rng.sample(tickers, 10)

draws = []
for i, t in enumerate(drop_tickers):
    draws.append({"draw_id": f"drop{i+1:02d}_{t}", "ptype": "drop_ticker", "param": t})
for days in (1, 2, 3, 5, 7, 10):
    draws.append({"draw_id": f"shift{days}", "ptype": "shift_start", "param": days})
for seed in (1, 2, 3, 4):
    draws.append({"draw_id": f"shuffle{seed}", "ptype": "shuffle_tiebreak", "param": seed})

print(f"{len(draws)} draws chosen:")
for d in draws:
    print(" ", d)

with open(f"{OUT_DIR}/draws.json", "w") as fh:
    json.dump(draws, fh, indent=2)
print(f"Wrote {OUT_DIR}/draws.json")
