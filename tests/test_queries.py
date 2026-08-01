import datetime as dt
import json

import pytest

from prism import db
from prism.queries import prompt_detail


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    db.init_db(path)
    monkeypatch.setattr(db, "DB_PATH", path)
    with db.connect(path) as c:
        c.execute("INSERT INTO brands (name, slug, is_own, tenant_id) VALUES ('Nike', 'nike', 1, 1)")
        c.execute("INSERT INTO models (name) VALUES ('stub')")
        c.execute("INSERT INTO prompts (text, tenant_id) VALUES ('best running shoes?', 1)")
        c.commit()  # visible to other connections (scheduler opens its own)
        yield c


def _run(conn, prompt_id, model_id, ran_at, job_id=None):
    conn.execute(
        "INSERT INTO runs (prompt_id, model_id, ran_at, response_text, status, job_id, tenant_id)"
        " VALUES (?, ?, ?, 'text', 'ok', ?, 1)",
        (prompt_id, model_id, ran_at, job_id),
    )


def test_prompt_detail_runs_grouped_and_newest_first(conn):
    # job 1: two runs
    _run(conn, 1, 1, "2026-07-29 01:00:00", job_id=1)
    _run(conn, 1, 1, "2026-07-29 01:01:00", job_id=1)
    # legacy run without a job — should sort by its own timestamp, not be pinned last
    _run(conn, 1, 1, "2026-07-30 02:00:00", job_id=None)
    # job 2: newest
    _run(conn, 1, 1, "2026-07-31 03:00:00", job_id=2)
    _run(conn, 1, 1, "2026-07-31 03:05:00", job_id=2)

    data = prompt_detail(conn, 1)

    assert data["runs_total"] == 5
    batches = data["runs"]
    # newest batch first
    assert batches[0]["job_id"] == 2 and len(batches[0]["runs"]) == 2
    # legacy NULL-job run sits between the two job batches, in time order
    assert batches[1]["job_id"] is None and len(batches[1]["runs"]) == 1
    assert batches[2]["job_id"] == 1 and len(batches[2]["runs"]) == 2
    # within a batch, newest run first
    assert batches[0]["runs"][0]["ran_at"] == "2026-07-31 03:05:00"


def test_prompt_detail_batch_not_split_across_pages(conn):
    # one job with 6 runs (more than RUNS_PER=5) — must stay a single batch on page 1
    for i in range(6):
        _run(conn, 1, 1, f"2026-07-30 01:0{i}:00", job_id=1)

    data = prompt_detail(conn, 1, runs_page=1)
    assert len(data["runs"]) == 1
    assert len(data["runs"][0]["runs"]) == 6
    assert data["runs_pages"] == 1


@pytest.mark.parametrize("legacy", ["2", "16"])
def test_migration_normalizes_legacy_schedule_hour(tmp_path, legacy):
    path = tmp_path / "test.db"
    db.init_db(path)
    with db.connect(path) as c:
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('schedule_hour', ?)", (legacy,))
    # re-running init_db applies _migrate
    db.init_db(path)
    with db.connect(path) as c:
        val = c.execute("SELECT value FROM settings WHERE key = 'schedule_hour'").fetchone()["value"]
    assert val == "0"


def _fake_datetime(fixed: dt.datetime):
    return type("FakeDT", (), {"now": staticmethod(lambda tz=None: fixed)})


def test_maybe_run_fires_at_midnight_hkt(conn, monkeypatch):
    """16:00 UTC == 00:00 HKT — the daily run must be queued."""
    from prism import scheduler

    fixed = dt.datetime(2026, 7, 31, 16, 0, 30, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(scheduler, "datetime", _fake_datetime(fixed))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    scheduler._last_rundate = ""

    scheduler._maybe_run(0)

    with db.connect() as c:
        jobs = c.execute("SELECT kind, payload FROM jobs").fetchall()
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "run_all"
    assert json.loads(jobs[0]["payload"])["tenant_id"] == 1

    # guard: must not fire a second time on the same day
    scheduler._maybe_run(0)
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"] == 1


def test_maybe_run_skips_outside_target_hour(conn, monkeypatch):
    """23:59 HKT is still hour 23, not 0 — nothing should be queued."""
    from prism import scheduler

    fixed = dt.datetime(2026, 7, 31, 15, 59, 30, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(scheduler, "datetime", _fake_datetime(fixed))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    scheduler._last_rundate = ""

    scheduler._maybe_run(0)

    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"] == 0
