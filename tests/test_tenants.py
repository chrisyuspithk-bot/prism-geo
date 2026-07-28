"""Multi-tenant isolation: one client's data must never leak into another's."""

import pytest

from prism import db, queries, workspace
from prism.runner import store_run


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    db.init_db(path)
    monkeypatch.setattr(db, "DB_PATH", path)
    with db.connect(path) as c:
        yield c


def _make_client(conn, name, website, competitors):
    slug = workspace.slugify(name)
    cur = conn.execute("INSERT INTO tenants (name, slug, website) VALUES (?, ?, ?)",
                       (name, slug, website))
    tid = cur.lastrowid
    conn.execute("INSERT INTO brands (name, slug, website, is_own, tenant_id)"
                 " VALUES (?, ?, ?, 1, ?)", (name, slug, website, tid))
    for comp in competitors:
        conn.execute("INSERT INTO brands (name, slug, is_own, tenant_id)"
                     " VALUES (?, ?, 0, ?)", (comp, workspace.slugify(comp), tid))
    cur = conn.execute("INSERT INTO prompts (text, tenant_id) VALUES (?, ?)",
                       (f"best for {name}?", tid))
    return tid, cur.lastrowid


def test_tenant_crud_and_own_brand(conn):
    tid, _ = _make_client(conn, "Allbirds", "https://allbirds.com", ["Nike", "Adidas"])
    own = workspace.own_brand(conn, tid)
    assert own["name"] == "Allbirds"
    assert {c["name"] for c in workspace.competitors(conn, tid)} == {"Nike", "Adidas"}
    assert workspace.get_tenant(conn, tid)["website"] == "https://allbirds.com"


def test_runs_are_isolated_per_tenant(conn):
    a, pa = _make_client(conn, "Allbirds", "https://allbirds.com", ["Nike"])
    b, pb = _make_client(conn, "Acme", "https://acme.com", ["Globex"])
    conn.execute("INSERT INTO models (name) VALUES ('stub')")

    # Allbirds run mentions its own competitor set; Acme run mentions Globex only.
    store_run(conn, prompt_id=pa, model_id=1, status="ok",
              text="Allbirds and Nike are great.", tenant_id=a,
              aliases=workspace.alias_map(conn, a))
    store_run(conn, prompt_id=pb, model_id=1, status="ok",
              text="Acme beats Globex.", tenant_id=b,
              aliases=workspace.alias_map(conn, b))

    va = queries.visibility_page(conn, a, 30)
    vb = queries.visibility_page(conn, b, 30)
    # Allbirds visibility reflects its own mention; Acme its own.
    assert va["cards"][0]["visibility"] == 100.0
    assert vb["cards"][0]["visibility"] == 100.0

    # SoV for Allbirds must not contain Acme's brands and vice versa.
    sov_a = {r["brand"] for r in queries.share_of_voice_page(conn, a, 30)["table"]}
    sov_b = {r["brand"] for r in queries.share_of_voice_page(conn, b, 30)["table"]}
    assert "Allbirds" in sov_a and "Nike" in sov_a
    assert "Acme" in sov_b and "Globex" in sov_b
    assert "Acme" not in sov_a and "Globex" not in sov_a
    assert "Allbirds" not in sov_b and "Nike" not in sov_b


def test_overview_scoped_to_tenant(conn):
    a, pa = _make_client(conn, "Allbirds", "https://allbirds.com", ["Nike"])
    b, _ = _make_client(conn, "Acme", "https://acme.com", [])
    conn.execute("INSERT INTO models (name) VALUES ('stub')")
    store_run(conn, prompt_id=pa, model_id=1, status="ok", text="Allbirds ftw", tenant_id=a)

    oa = queries.overview(conn, a, 30)
    ob = queries.overview(conn, b, 30)
    assert oa["runs"] == 1 and oa["brand"]["name"] == "Allbirds"
    assert ob["runs"] == 0 and ob["brand"]["name"] == "Acme"
