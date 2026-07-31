"""Daily auto-evaluation scheduler.

One asyncio background task that wakes up daily at a configured HKT hour and
inserts a `run_all` job for every tenant. Checks every 60 s so a changed
schedule_hour takes effect within a minute — no restart needed.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from . import keystore
from .db import connect, q
from . import jobs

_task: asyncio.Task | None = None


def _get_config() -> tuple[bool, int]:
    with connect() as conn:
        enabled = conn.execute(
            "SELECT value FROM settings WHERE key = 'schedule_enabled'").fetchone()
        hour = conn.execute(
            "SELECT value FROM settings WHERE key = 'schedule_hour'").fetchone()
    on = (enabled["value"] or "1") == "1" if enabled else True
    h = int(hour["value"]) if hour else 0  # 0 = midnight HKT (GMT+8)
    return on, h


def _set_defaults() -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('schedule_enabled', '1')")
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('schedule_hour', '0')")


async def _scheduler() -> None:
    _set_defaults()
    while True:
        try:
            enabled, hour = _get_config()
            if enabled:
                _maybe_run(hour)
        except Exception as exc:
            print(f"[scheduler] error: {exc}", flush=True)
        # Sleep until next whole-minute boundary to avoid drift
        now = datetime.now(timezone.utc)
        wait = 60 - now.second
        await asyncio.sleep(wait if wait > 0 else 60)


_last_rundate: str = ""


def _maybe_run(hour: int) -> None:
    """If we're in the target hour (HKT) and haven't run today, queue jobs."""
    global _last_rundate
    hkt = datetime.now(timezone.utc) + timedelta(hours=8)
    today = hkt.strftime("%Y-%m-%d")
    if hkt.hour == hour and _last_rundate != today:
        if keystore.has_any_key():
            _last_rundate = today
            print(f"[scheduler] firing daily run for {today} (HKT hour {hour})", flush=True)
            with connect() as conn:
                tenants = q(conn, "SELECT id FROM tenants")
            for t in tenants:
                tid = t["id"]
                with connect() as conn:
                    n = conn.execute(
                        "SELECT COUNT(*) FROM prompts WHERE active = 1 AND tenant_id = ?",
                        (tid,)).fetchone()[0]
                if n:
                    total = n * len(keystore.active_engines())
                    jobs.create_job("run_all", {"tenant_id": tid}, total=total)


def ensure_scheduler() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_scheduler())
