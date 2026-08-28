"""In-process scheduler for the background job.

APScheduler rather than Celery/RQ deliberately: the brief's stack must be free
and require no extra infrastructure, and a single periodic rollup does not
justify a broker.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.jobs.rollup_and_alerts import JOB_NAME, run_job

log = logging.getLogger(__name__)

#: Every 15 minutes. Frequent enough that alerts are timely, rare enough that it
#: is never on the request path.
INTERVAL_MINUTES = 15


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_job,
        trigger="interval",
        minutes=INTERVAL_MINUTES,
        id=JOB_NAME,
        # If the process was busy or asleep, run once on resume rather than
        # firing every missed interval at once.
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    log.info("scheduler started: %s every %d minutes", JOB_NAME, INTERVAL_MINUTES)
    return scheduler


def stop_scheduler(scheduler: BackgroundScheduler) -> None:
    scheduler.shutdown(wait=False)
    log.info("scheduler stopped")
