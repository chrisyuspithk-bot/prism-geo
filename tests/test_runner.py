import pytest

from prism import db
from prism.runner import store_run


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


def test_store_run_extracts_mentions_and_citations(conn):
    text = "Nike and Adidas lead. Sources: https://www.runnersworld.com/x https://reddit.com/y"
    run_id = store_run(conn, prompt_id=1, model_id=1, status="ok", text=text, tenant_id=1)
    mentions = conn.execute(
        "SELECT b.name, m.position FROM mentions m JOIN brands b ON b.id = m.brand_id"
        " WHERE m.run_id = ? ORDER BY m.position", (run_id,)).fetchall()
    assert [m["name"] for m in mentions] == ["Nike", "Adidas"]
    # Adidas was auto-created as a new brand row within the same tenant
    row = conn.execute("SELECT tenant_id FROM brands WHERE name = 'Adidas'").fetchone()
    assert row and row["tenant_id"] == 1
    cites = conn.execute("SELECT domain FROM citations WHERE run_id = ?", (run_id,)).fetchall()
    assert {c["domain"] for c in cites} == {"runnersworld.com", "reddit.com"}


def test_store_run_error_stores_nothing_else(conn):
    run_id = store_run(conn, prompt_id=1, model_id=1, status="error",
                       text="", error="boom", tenant_id=1)
    assert conn.execute("SELECT COUNT(*) n FROM mentions WHERE run_id = ?",
                        (run_id,)).fetchone()["n"] == 0
    row = conn.execute("SELECT status, error, tenant_id FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert row["status"] == "error" and row["error"] == "boom" and row["tenant_id"] == 1
