"""Background job schedulers (night audit + yield pricing + platform ops)."""
from __future__ import annotations

import asyncio
import logging
import os

from jobs.night_audit import run_due_night_audits
from services.revenue import run_due_yield_jobs

log = logging.getLogger("webclaw.jobs.scheduler")

_night_task: asyncio.Task | None = None
_yield_task: asyncio.Task | None = None
_ops_task: asyncio.Task | None = None
_NIGHT_STOP = asyncio.Event()
_YIELD_STOP = asyncio.Event()
_OPS_STOP = asyncio.Event()


def _night_interval_seconds() -> int:
    try:
        return max(60, int(os.environ.get("WEBCLAW_NIGHT_AUDIT_POLL_SECONDS", "300")))
    except Exception:
        return 300


def _yield_interval_seconds() -> int:
    try:
        return max(60, int(os.environ.get("WEBCLAW_YIELD_POLL_SECONDS", "900")))
    except Exception:
        return 900


async def _night_poll_loop() -> None:
    log.info(
        "Night audit scheduler started (interval=%ss)",
        _night_interval_seconds(),
    )
    try:
        await asyncio.wait_for(_NIGHT_STOP.wait(), timeout=20)
        return
    except asyncio.TimeoutError:
        pass

    while not _NIGHT_STOP.is_set():
        try:
            from admin_ops import is_job_paused, mark_job_run

            if is_job_paused("night_audit"):
                mark_job_run("night_audit", "paused", "skipped while paused")
            else:
                summary = await run_due_night_audits()
                ran = summary.get("ran") or []
                mark_job_run(
                    "night_audit",
                    "ok",
                    f"ran={len(ran)}" if ran else "idle",
                )
                if ran:
                    log.info("Night audit scheduler ran %s hotel(s)", len(ran))
        except Exception:
            log.exception("Night audit poll failed")
            try:
                from admin_ops import mark_job_run

                mark_job_run("night_audit", "error", "poll failed")
            except Exception:
                pass
        try:
            await asyncio.wait_for(_NIGHT_STOP.wait(), timeout=_night_interval_seconds())
            break
        except asyncio.TimeoutError:
            continue
    log.info("Night audit scheduler stopped")


async def _yield_poll_loop() -> None:
    log.info(
        "Yield pricing scheduler started (interval=%ss)",
        _yield_interval_seconds(),
    )
    try:
        await asyncio.wait_for(_YIELD_STOP.wait(), timeout=45)
        return
    except asyncio.TimeoutError:
        pass

    while not _YIELD_STOP.is_set():
        try:
            from admin_ops import is_job_paused, mark_job_run

            if is_job_paused("yield_pricing"):
                mark_job_run("yield_pricing", "paused", "skipped while paused")
            else:
                summary = await run_due_yield_jobs()
                ran = summary.get("ran") or []
                mark_job_run(
                    "yield_pricing",
                    "ok",
                    f"ran={len(ran)}" if ran else "idle",
                )
                if ran:
                    log.info("Yield pricing scheduler ran %s hotel(s)", len(ran))
        except Exception:
            log.exception("Yield pricing poll failed")
            try:
                from admin_ops import mark_job_run

                mark_job_run("yield_pricing", "error", "poll failed")
            except Exception:
                pass
        try:
            await asyncio.wait_for(_YIELD_STOP.wait(), timeout=_yield_interval_seconds())
            break
        except asyncio.TimeoutError:
            continue
    log.info("Yield pricing scheduler stopped")


def start_night_audit_scheduler() -> None:
    global _night_task
    if os.environ.get("WEBCLAW_NIGHT_AUDIT_SCHEDULER", "1").lower() in ("0", "false", "no"):
        log.info("Night audit scheduler disabled via WEBCLAW_NIGHT_AUDIT_SCHEDULER")
        return
    if _night_task and not _night_task.done():
        return
    _NIGHT_STOP.clear()
    _night_task = asyncio.create_task(_night_poll_loop(), name="night-audit-scheduler")


async def stop_night_audit_scheduler() -> None:
    global _night_task
    _NIGHT_STOP.set()
    if _night_task:
        try:
            await asyncio.wait_for(_night_task, timeout=5)
        except Exception:
            _night_task.cancel()
        _night_task = None


def _ops_interval_seconds() -> int:
    try:
        return max(60, int(os.environ.get("WEBCLAW_OPS_POLL_SECONDS", "300")))
    except Exception:
        return 300


async def _ops_poll_loop() -> None:
    log.info("Platform ops scheduler started (interval=%ss)", _ops_interval_seconds())
    try:
        await asyncio.wait_for(_OPS_STOP.wait(), timeout=30)
        return
    except asyncio.TimeoutError:
        pass

    while not _OPS_STOP.is_set():
        try:
            from admin_ops import (
                activate_due_announcements,
                check_sla_breaches,
                is_job_paused,
                mark_job_run,
                run_scheduled_backup_if_due,
            )
            from admin_platform import run_due_tenant_purges

            if is_job_paused("platform_ops"):
                mark_job_run("platform_ops", "paused", "skipped while paused")
            else:
                n = activate_due_announcements()
                if n:
                    log.info("Published %s scheduled announcement(s)", n)
                backup = run_scheduled_backup_if_due()
                if backup:
                    log.info("Auto backup completed: %s", backup.get("filename"))
                purged = run_due_tenant_purges()
                if purged:
                    log.info("Purged %s cancelled hotel(s) past retention", purged)
                breached = check_sla_breaches()
                if breached:
                    log.info("Marked %s support SLA breach(es)", breached)
                mark_job_run(
                    "platform_ops",
                    "ok",
                    f"ann={n or 0} backup={bool(backup)} purged={purged or 0} sla={breached or 0}",
                )
        except Exception:
            log.exception("Platform ops poll failed")
            try:
                from admin_ops import mark_job_run

                mark_job_run("platform_ops", "error", "poll failed")
            except Exception:
                pass
        try:
            await asyncio.wait_for(_OPS_STOP.wait(), timeout=_ops_interval_seconds())
            break
        except asyncio.TimeoutError:
            continue
    log.info("Platform ops scheduler stopped")


def start_ops_scheduler() -> None:
    global _ops_task
    if os.environ.get("WEBCLAW_OPS_SCHEDULER", "1").lower() in ("0", "false", "no"):
        log.info("Platform ops scheduler disabled via WEBCLAW_OPS_SCHEDULER")
        return
    if _ops_task and not _ops_task.done():
        return
    _OPS_STOP.clear()
    _ops_task = asyncio.create_task(_ops_poll_loop(), name="platform-ops-scheduler")


async def stop_ops_scheduler() -> None:
    global _ops_task
    _OPS_STOP.set()
    if _ops_task:
        try:
            await asyncio.wait_for(_ops_task, timeout=5)
        except Exception:
            _ops_task.cancel()
        _ops_task = None


def start_yield_scheduler() -> None:
    global _yield_task
    if os.environ.get("WEBCLAW_YIELD_SCHEDULER", "1").lower() in ("0", "false", "no"):
        log.info("Yield pricing scheduler disabled via WEBCLAW_YIELD_SCHEDULER")
        return
    if _yield_task and not _yield_task.done():
        return
    _YIELD_STOP.clear()
    _yield_task = asyncio.create_task(_yield_poll_loop(), name="yield-pricing-scheduler")


async def stop_yield_scheduler() -> None:
    global _yield_task
    _YIELD_STOP.set()
    if _yield_task:
        try:
            await asyncio.wait_for(_yield_task, timeout=5)
        except Exception:
            _yield_task.cancel()
        _yield_task = None
