"""Point 5, sharpened: is the trade-level serial correlation a SEQUENCE effect
or shared market conditions?

The regression control in diagnostic_b.py removes the concurrent INDEX move but
cannot remove the deeper problem: with ~12 concurrent positions and a 149-day
mean hold, consecutive trades by exit date have massively OVERLAPPING holding
periods. They are largely the same market period measured twice.

The decisive test is therefore not a control variable but a subset: restrict to
consecutive pairs that do NOT overlap in time (trade i+1 enters strictly after
trade i exits). If the correlation survives there it is a sequence effect and
the pausing proposal has a basis. If it vanishes, it was overlap.
"""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
REF = ROOT / "research/exit_mechanics_prereg_2026-08-11"

out = {}
for pool in ("pool2_clean", "pool1"):
    t = pd.read_csv(REF / f"trades_{pool}_s3.0.csv")
    t["entry_date"] = pd.to_datetime(t["entry_date"]); t["exit_date"] = pd.to_datetime(t["exit_date"])
    t = t.sort_values("exit_date").reset_index(drop=True)
    r = t["pnl_pct"].to_numpy() * 100.0

    # overlap fraction of consecutive pairs
    ov = [(t.entry_date[i+1] <= t.exit_date[i]) for i in range(len(t)-1)]
    ov = np.array(ov)
    pairs_all = [(r[i], r[i+1]) for i in range(len(t)-1)]
    pairs_non = [(r[i], r[i+1]) for i in range(len(t)-1) if not ov[i]]

    def corr(ps):
        if len(ps) < 20: return None, len(ps)
        a = np.array([p[0] for p in ps]); b = np.array([p[1] for p in ps])
        return round(float(np.corrcoef(a,b)[0,1]), 4), len(ps)

    ca, na = corr(pairs_all)
    cn, nn = corr(pairs_non)

    # mean pairwise calendar overlap in days, consecutive pairs
    ovd = [max(0, (min(t.exit_date[i], t.exit_date[i+1]) - max(t.entry_date[i], t.entry_date[i+1])).days)
           for i in range(len(t)-1)]

    # win-rate conditioning restricted to non-overlapping successors
    nxt_after_loss = [r[i+1] for i in range(len(t)-1) if not ov[i] and r[i] <= 0]
    nxt_after_win  = [r[i+1] for i in range(len(t)-1) if not ov[i] and r[i] > 0]
    def wr(x): return round(100.0*float((np.array(x)>0).mean()),2) if len(x) else None

    out[pool] = {
        "consecutive_pairs": len(pairs_all),
        "pairs_overlapping_in_time": int(ov.sum()),
        "pct_pairs_overlapping": round(100.0*float(ov.mean()),1),
        "mean_overlap_days_consecutive_pairs": round(float(np.mean(ovd)),1),
        "lag1_corr_ALL_pairs": ca,
        "lag1_corr_NON_OVERLAPPING_pairs": cn,
        "n_non_overlapping": nn,
        "unconditional_win_pct": round(100.0*float((r>0).mean()),2),
        "win_pct_after_loss_nonoverlap": wr(nxt_after_loss),
        "n_after_loss_nonoverlap": len(nxt_after_loss),
        "win_pct_after_win_nonoverlap": wr(nxt_after_win),
        "n_after_win_nonoverlap": len(nxt_after_win),
    }

print(json.dumps(out, indent=2))
(HERE / "diagnostic_b2_overlap.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
