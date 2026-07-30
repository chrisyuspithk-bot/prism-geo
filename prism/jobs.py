"""Background job runner for evaluations.

One in-process asyncio worker consumes jobs from the `jobs` table. Each
"run_all" job fans the active prompts out to the answer engine with bounded
concurrency, persists each run as it lands, and updates progress on the job
row so the UI can poll it. Cancellation is cooperative: the loop checks the
job status between tasks.

This mirrors how real GEO tools (Elmo: pg-boss) run evaluations async — the
difference is we keep the worker in-process instead of a separate queue.
"""

import asyncio
import json
from datetime import datetime, timezone

from . import keystore
from .db import connect, q1
from .runner import run_prompt

CONCURRENCY = 4
STEP_TIMEOUT = 75          # hard bound per (prompt, engine) call
_worker_lock: asyncio.Lock | None = None   # one job at a time per process
_task: asyncio.Task | None = None


def create_job(kind: str, payload: dict | None = None, total: int = 0) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (kind, status, total, payload) VALUES (?, 'queued', ?, ?)",
            (kind, total, json.dumps(payload or {})),
        )
        return cur.lastrowid


def get_job(job_id: int) -> dict | None:
    with connect() as conn:
        row = q1(conn, "SELECT * FROM jobs WHERE id = ?", (job_id,))
    return dict(row) if row else None


def request_cancel(job_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'cancelled' WHERE id = ? AND status IN ('queued','running')",
            (job_id,))


def _update(conn, job_id: int, **fields) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*fields.values(), job_id))


def _append_log(conn, job_id: int, line: str) -> None:
    conn.execute("UPDATE jobs SET log = log || ? WHERE id = ?", (line + "\n", job_id))


async def _do_run_all(job_id: int, tenant_id: int) -> None:
    engines = keystore.active_engines()
    with connect() as conn:
        prompt_ids = [r["id"] for r in
                      conn.execute("SELECT id FROM prompts WHERE active = 1 AND tenant_id = ? "
                                   "ORDER BY id", (tenant_id,))]
        total = len(prompt_ids) * len(engines)
        _update(conn, job_id, status="running", total=total)
        _append_log(conn, job_id,
                    f"{len(prompt_ids)} prompts x {len(engines)} engines "
                    f"({', '.join(e['name'] for e in engines)})")

    sem = asyncio.Semaphore(CONCURRENCY)
    done = err = 0

    async def one(pid: int, engine: dict) -> None:
        nonlocal done, err
        try:
            async with sem:
                with connect() as conn:
                    if q1(conn, "SELECT status FROM jobs WHERE id = ?", (job_id,))["status"] == "cancelled":
                        return
                try:
                    run_id = await asyncio.wait_for(
                        run_prompt(pid, engine=engine, job_id=job_id), timeout=STEP_TIMEOUT)
                    with connect() as conn:
                        status = q1(conn, "SELECT status FROM runs WHERE id = ?", (run_id,))["status"]
                except asyncio.TimeoutError:
                    status = "error"
                    with connect() as conn:
                        _append_log(conn, job_id,
                                    f"prompt {pid} [{engine['name']}]: timed out after {STEP_TIMEOUT}s")
                except Exception as exc:
                    status = "error"
                    with connect() as conn:
                        _append_log(conn, job_id, f"prompt {pid} [{engine['name']}]: {exc}")
                done += 1
                err += status != "ok"
                with connect() as conn:
                    _update(conn, job_id, done=done, errors=err)
                    _append_log(conn, job_id, f"prompt {pid} [{engine['name']}]: {status}")
        except asyncio.CancelledError:
            pass  # superseded job — release the semaphore and exit quietly

    await asyncio.gather(*(one(p, e) for p in prompt_ids for e in engines))

    with connect() as conn:
        final = "done" if err < total else "error"
        cancelled = q1(conn, "SELECT status FROM jobs WHERE id = ?", (job_id,))["status"] == "cancelled"
        _update(conn, job_id,
                status="cancelled" if cancelled else final,
                finished_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))


async def _do_run_prompt(job_id: int, prompt_id: int) -> None:
    engines = keystore.active_engines()
    with connect() as conn:
        _update(conn, job_id, status="running", total=len(engines))
    done = err = 0
    for engine in engines:
        with connect() as conn:
            if q1(conn, "SELECT status FROM jobs WHERE id = ?", (job_id,))["status"] == "cancelled":
                return
        try:
            run_id = await asyncio.wait_for(
                run_prompt(prompt_id, engine=engine, job_id=job_id), timeout=STEP_TIMEOUT)
            with connect() as conn:
                status = q1(conn, "SELECT status FROM runs WHERE id = ?", (run_id,))["status"]
        except Exception as exc:
            status = "error"
            with connect() as conn:
                _append_log(conn, job_id, f"prompt {prompt_id} [{engine['name']}]: {exc}")
        done += 1
        err += status != "ok"
        with connect() as conn:
            _update(conn, job_id, done=done, errors=err)
            _append_log(conn, job_id, f"prompt {prompt_id} [{engine['name']}]: {status}")
    with connect() as conn:
        _update(conn, job_id,
                status="done" if err < done else "error",
                finished_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))


async def _worker() -> None:
    while True:
        with connect() as conn:
            job = q1(conn,
                     "SELECT * FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1")
        if job is None:
            await asyncio.sleep(1.5)
            continue
        payload = json.loads(job["payload"] or "{}")
        try:
            if job["kind"] == "run_all":
                await _do_run_all(job["id"], payload["tenant_id"])
            elif job["kind"] == "run_prompt":
                await _do_run_prompt(job["id"], payload["prompt_id"])
            else:
                with connect() as conn:
                    _update(conn, job["id"], status="error")
        except Exception as exc:  # never let the worker die on one bad job
            with connect() as conn:
                _update(conn, job["id"], status="error")
                _append_log(conn, job["id"], f"worker error: {exc}")


def recover_stale_jobs() -> None:
    """Mark jobs left 'running'/'queued' by a crash as errored, on startup.

    Single-process worker: if the process died, no job can still be running.
    """
    with connect() as conn:
        n = conn.execute(
            "UPDATE jobs SET status='error', finished_at=datetime('now') "
            "WHERE status IN ('queued','running')").rowcount
        if n:
            conn.execute(
                "UPDATE jobs SET log = log || 'interrupted by server restart' "
                "WHERE status='error' AND finished_at=datetime('now')")


def ensure_worker() -> None:
    """Start the background worker once per process (called on app startup)."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_worker())
