import json
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

for name in ["curated40_result.json", "full500_result.json", "rolling40_result.json", "dax_result.json",
             "curated40_no_momentum_floor_result.json"]:
    p = OUTPUT_DIR / name
    if p.exists():
        with open(p) as f:
            d = json.load(f)
        print(f"=== {name} ===")
        print(json.dumps(d.get("test_metrics", d), indent=2, default=str))
        print()
    else:
        print(f"=== {name} === NOT YET PRESENT")
