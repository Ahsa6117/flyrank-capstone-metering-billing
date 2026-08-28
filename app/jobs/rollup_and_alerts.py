"""Background job: usage rollups + 80%/100% quota alerts.

Requirement S3 asks for slow/bulk work off the request path, **with retries and a
failure alert**. Both are here:

* Three attempts with exponential backoff.
* After the final failure the job writes a ``job_runs`` row with status
  ``failed`` and logs at ERROR -- a real failure alert, not a silent pass.

Alerts are idempotent by construction: the unique index on
(tenant, usage_type, threshold, period) means a re-run cannot duplicate one --
the same discipline that protects metering.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.periods import now, period_start
from app.db import SessionLocal
from app.models import JobRun, UsageAlert
from app.repositories import TenantRepository, UsageRepository

log = logging.getLogger(__name__)

JOB_NAME = "rollup_and_alerts"
ALERT_THRESHOLDS = (80, 100)
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 2


def run_job(max_attempts: int = MAX_ATTEMPTS, sleep=time.sleep) -> dict[str, Any]:
    """Run the job with retries. Never raises -- it reports."""
    started = now()
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            summary = _do_work()
        except Exception as exc:  # noqa: BLE001 - the retry boundary
            last_error = exc
            log.warning(
                "%s attempt %d/%d failed: %s",
                JOB_NAME,
                attempt,
                max_attempts,
                type(exc).__name__,
            )
            if attempt < max_attempts:
                sleep(BACKOFF_BASE_SECONDS ** attempt)
            continue

        _record_run("success", attempt, str(summary), started)
        log.info("%s succeeded on attempt %d: %s", JOB_NAME, attempt, summary)
        return {"status": "success", "attempts": attempt, **summary}

    # Every attempt failed: this is the failure alert path.
    detail = f"{type(last_error).__name__}: {last_error}"
    _record_run("failed", max_attempts, detail, started)
    log.error(
        "ALERT: background job %s failed after %d attempts: %s",
        JOB_NAME,
        max_attempts,
        detail,
    )
    return {"status": "failed", "attempts": max_attempts, "error": detail}


def _do_work() -> dict[str, Any]:
    """Recompute rollups and emit threshold alerts for every tenant."""
    session = SessionLocal()
    try:
        tenants_repo = TenantRepository(session)
        usage_repo = UsageRepository(session)
        current_period = period_start()

        tenants_seen = 0
        alerts_created = 0

        for tenant in tenants_repo.list_all():
            tenants_seen += 1
            rollup = usage_repo.rollup(tenant.id)
            plan = tenants_repo.get_plan(tenant.plan_code)
            if plan is None:  # pragma: no cover
                continue

            checks = (
                ("api_calls", rollup.api_calls, plan.quota_api_calls),
                ("tokens", rollup.total_tokens, plan.quota_tokens),
            )

            for usage_type, used, limit in checks:
                if limit <= 0:  # pragma: no cover - defensive
                    continue
                percent = (used * 100) // limit
                for threshold in ALERT_THRESHOLDS:
                    if percent >= threshold and _emit_alert(
                        session,
                        tenant_id=tenant.id,
                        usage_type=usage_type,
                        threshold=threshold,
                        used=used,
                        limit=limit,
                        period=current_period,
                    ):
                        alerts_created += 1

        session.commit()
        return {
            "tenants_processed": tenants_seen,
            "alerts_created": alerts_created,
            "period_start": current_period.isoformat(),
        }
    finally:
        session.close()


def _emit_alert(
    session,
    *,
    tenant_id: str,
    usage_type: str,
    threshold: int,
    used: int,
    limit: int,
    period,
) -> bool:
    """Write one alert, or nothing if it already exists. Returns True if written."""
    alert = UsageAlert(
        id=f"alert_{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        usage_type=usage_type,
        threshold_percent=threshold,
        period_start=period,
        message=(
            f"Tenant {tenant_id} has used {used} of {limit} {usage_type} "
            f"this period ({threshold}% threshold reached)."
        ),
    )
    try:
        with session.begin_nested():
            session.add(alert)
        return True
    except IntegrityError:
        # Already alerted for this tenant/type/threshold/period. Exactly the
        # behaviour we want on a re-run.
        return False


def _record_run(status: str, attempts: int, detail: str, started) -> None:
    session = SessionLocal()
    try:
        session.add(
            JobRun(
                id=f"job_{uuid.uuid4().hex}",
                job_name=JOB_NAME,
                status=status,
                attempts=attempts,
                detail=detail[:2000],
                started_at=started,
                finished_at=now(),
            )
        )
        session.commit()
    except Exception:  # pragma: no cover - never let bookkeeping mask the job
        session.rollback()
        log.exception("could not record job run")
    finally:
        session.close()
