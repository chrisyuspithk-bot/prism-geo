"""Daily auto-evaluation scheduler.

One asyncio background task that wakes up daily at a configured HKT hour and
inserts a `run_all` job for every tenant. Checks every 60 s so a changed
schedule_hour takes effect within a minute — no restart needed.

Idempotency is enforced by checking the jobs table: if any run_all job was
created on today's HKT date, the scheduler skips. This survives server restarts
and covers the case where the server was down during the scheduled hour window
(the scheduler fires on the next tick once the hour has been reached or passed).
"""

import asyncio
from datetime import datetime, timedelta, timezone

from . import keystore
from .db import connect, q, q1
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


def _already_ran_today(hkt_now: datetime) -> bool:
    """True if a run_all job was already created on today's HKT date."""
    today = hkt_now.strftime("%Y-%m-%d")
    with connect() as conn:
        row = q1(conn,
                 "SELECT 1 FROM jobs WHERE kind = 'run_all' "
                 "AND date(created_at, '+8 hours') = ? LIMIT 1", (today,))
    return row is not None


def _fire(hour: int, today: str) -> None:
    """Create run_all jobs for every tenant with active prompts."""
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


async def _scheduler() -> None:
    _set_defaults()
    enabled, hour = _get_config()
    print(f"[scheduler] started: enabled={enabled}, hour={hour:02d}:00 HKT (GMT+8)", flush=True)
    while True:
        try:
            enabled, hour = _get_config()
            if enabled and keystore.has_any_key():
                hkt = datetime.now(timezone.utc) + timedelta(hours=8)
                if not _already_ran_today(hkt):
                    today = hkt.strftime("%Y-%m-%d")
                    # Fire during the scheduled hour, or later if the window
                    # was missed (server was down / restarting).
                    if hkt.hour >= hour:
                        _fire(hour, today)
        except Exception as exc:
            print(f"[scheduler] error: {exc}", flush=True)
        # Sleep until next whole-minute boundary to avoid drift
        now = datetime.now(timezone.utc)
        wait = 60 - now.second
        await asyncio.sleep(wait if wait > 0 else 60)


def ensure_scheduler() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_scheduler())
