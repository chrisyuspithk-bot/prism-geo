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


def test_fire_creates_run_all_job(conn, monkeypatch):
    """_fire must create one run_all job per tenant with active prompts."""
    from prism import scheduler

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    scheduler._fire(0, "2026-07-31")

    with db.connect() as c:
        rows = c.execute("SELECT kind, payload FROM jobs").fetchall()
    assert len(rows) == 1
    assert rows[0]["kind"] == "run_all"
    assert json.loads(rows[0]["payload"])["tenant_id"] == 1


def test_already_ran_today_detects_existing_job(conn):
    """_already_ran_today returns True only when a run_all job exists on the HKT date."""
    from prism import scheduler

    # No job yet — should return False
    hkt = dt.datetime(2026, 7, 31, 0, 30, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    assert not scheduler._already_ran_today(hkt)

    # Insert a run_all job with a specific UTC created_at that maps to 2026-07-31 HKT
    # 2026-07-31 00:30 HKT = 2026-07-30 16:30 UTC
    conn.execute(
        "INSERT INTO jobs (kind, status, total, created_at) VALUES ('run_all', 'done', 1, ?)",
        ("2026-07-30 16:30:00",))
    conn.execute("COMMIT")

    # Should now return True for 2026-07-31 HKT
    assert scheduler._already_ran_today(hkt)

    # Different day — should return False
    hkt_next = dt.datetime(2026, 8, 1, 0, 30, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    assert not scheduler._already_ran_today(hkt_next)
