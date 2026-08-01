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
