"""Standalone scheduler daemon.

Run as a separate systemd service alongside the Streamlit web UI so that all
APScheduler jobs fire on their configured UTC schedule even when no user has
visited the dashboard since the last server restart.

Streamlit does not execute main.py until a browser connects, so relying on the
in-process scheduler misses jobs if the server restarts without a page visit.
This daemon solves that by running the scheduler in an independent process.

Usage (systemd handles this automatically — see investment-research-scheduler.service):
    python3.11 scheduler_daemon.py
"""

import logging
import sys
import time
from pathlib import Path

# Ensure the project root is on sys.path regardless of working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    from data.database import init_db
    from data.scheduler import start_scheduler

    logger.info("Scheduler daemon starting — initialising database...")
    init_db()

    logger.info("Starting APScheduler...")
    start_scheduler()
    logger.info("Scheduler daemon running. Jobs will fire on their configured UTC schedule.")

    # APScheduler runs jobs in background threads; keep this process alive.
    while True:
        time.sleep(60)
