"""Pre-arrival notification scheduler (24h before check-in)."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, timedelta

from notifications.context import find_pre_arrival_candidates
from notifications.engine import dispatch_trigger

log = logging.getLogger("webclaw.notifications.scheduler")

_task: asyncio.Task | None = None
_STOP = asyncio.Event()


def _interval_seconds() -> int:
    try:
        return max(60, int(os.environ.get("WEBCLAW_NOTIFY_POLL_SECONDS", "900")))
    except Exception:
        return 900


def tomorrow_iso() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


async def run_pre_arrival_once() -> dict:
    """Send pre-arrival messages for reservations checking in tomorrow."""
    target = tomorrow_iso()
    candidates = find_pre_arrival_candidates(target)
    results = []
    for row in candidates:
        tid = row.get("tenant_id")
        rid = row.get("reservation_id")
        if not tid or not rid:
            continue
        try:
            res = await dispatch_trigger(
                tenant_id=str(tid),
                trigger_key="pre_arrival",
                reservation_id=str(rid),
            )
            results.append({"tenant_id": tid, "reservation_id": rid, "result": res})
        except Exception as exc:
            log.exception("pre-arrival failed for %s", rid)
            results.append(
                {"tenant_id": tid, "reservation_id": rid, "error": str(exc)}
            )
    return {
        "status": "ok",
        "check_in_date": target,
        "candidates": len(candidates),
        "results": results,
    }


async def _poll_loop() -> None:
    log.info(
        "Pre-arrival notification scheduler started (interval=%ss)",
        _interval_seconds(),
    )
    # Short initial delay so the API finishes booting
    try:
        await asyncio.wait_for(_STOP.wait(), timeout=15)
        return
    except asyncio.TimeoutError:
        pass

    while not _STOP.is_set():
        try:
            summary = await run_pre_arrival_once()
            if summary.get("candidates"):
                log.info(
                    "Pre-arrival run: %s candidates for %s",
                    summary.get("candidates"),
                    summary.get("check_in_date"),
                )
        except Exception:
            log.exception("Pre-arrival poll failed")
        try:
            await asyncio.wait_for(_STOP.wait(), timeout=_interval_seconds())
            break
        except asyncio.TimeoutError:
            continue
    log.info("Pre-arrival notification scheduler stopped")


def start_scheduler() -> None:
    global _task
    if os.environ.get("WEBCLAW_NOTIFY_SCHEDULER", "1").lower() in ("0", "false", "no"):
        log.info("Pre-arrival scheduler disabled via WEBCLAW_NOTIFY_SCHEDULER")
        return
    if _task and not _task.done():
        return
    _STOP.clear()
    _task = asyncio.create_task(_poll_loop(), name="pre-arrival-notifier")


async def stop_scheduler() -> None:
    global _task
    _STOP.set()
    if _task:
        try:
            await asyncio.wait_for(_task, timeout=5)
        except Exception:
            _task.cancel()
        _task = None
