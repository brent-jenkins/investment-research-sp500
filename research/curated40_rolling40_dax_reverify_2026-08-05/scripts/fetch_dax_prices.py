import env_setup

env_setup.patch_all()

from data.database import init_db
from data.ingestion import pull_history

init_db()

DAX_40 = [
    "ADS.DE", "AIR.PA", "ALV.DE", "BAS.DE", "BAYN.DE", "BEI.DE", "BMW.DE", "BNR.DE",
    "CBK.DE", "CON.DE", "DTG.DE", "DBK.DE", "DB1.DE", "DHL.DE", "DTE.DE", "EOAN.DE",
    "FRE.DE", "FME.DE", "G1A.DE", "HNR1.DE", "HEI.DE", "HEN3.DE", "IFX.DE", "MBG.DE",
    "MRK.DE", "MTX.DE", "MUV2.DE", "PAH3.DE", "QIA.DE", "RHM.DE", "RWE.DE", "SAP.DE",
    "G24.DE", "SIE.DE", "ENR.DE", "SHL.DE", "SY1.DE", "VOW3.DE", "VNA.DE", "ZAL.DE",
]
BENCHMARK = ["^GDAXI"]

tickers = DAX_40 + BENCHMARK
print(f"Fetching full history for {len(tickers)} tickers (days=8000, ~22 years)...", flush=True)
result = pull_history(tickers, days=8000)
for t, n in result.items():
    print(t, n, flush=True)
