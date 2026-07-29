"""Tenants (client workspaces) and brand setup.

A tenant is one client: the brand being tracked. Its competitors live in the
brands table linked by brand_id, all scoped by tenant_id so every dashboard and
extraction stays isolated to that client. Aliases are how we recognize a brand
in answer text — the display name plus sensible variants (domain, lowercase).
"""

import re

from .db import connect, q, q1
from .extract import domain_of


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def default_aliases(name: str, website: str) -> str:
    aliases = {name, name.lower()}
    domain = domain_of(website) if website else ""
    if domain:
        root = domain.split(".")[0]
        aliases.add(root)
        aliases.add(domain)
    return "\n".join(sorted(a for a in aliases if a))


# --- Tenant (client) CRUD ----------------------------------------------------

def list_tenants(conn) -> list:
    return q(conn, "SELECT * FROM tenants ORDER BY name")


def get_tenant(conn, tenant_id: int) -> dict | None:
    row = q1(conn, "SELECT * FROM tenants WHERE id = ?", (tenant_id,))
    return dict(row) if row else None


def default_tenant_id(conn) -> int:
    """The first tenant, creating the shell if the workspace is empty."""
    row = q1(conn, "SELECT id FROM tenants ORDER BY id LIMIT 1")
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO tenants (name, slug) VALUES ('Default', 'default')")
    return cur.lastrowid


def create_tenant(name: str, website: str = "") -> int:
    """Create a client workspace and the brand row it tracks."""
    with connect() as conn:
        slug = _unique_slug(conn, slugify(name), "tenants")
        cur = conn.execute(
            "INSERT INTO tenants (name, slug, website) VALUES (?, ?, ?)",
            (name, slug, website))
        tenant_id = cur.lastrowid
        _upsert_own_brand(conn, tenant_id, name, website)
        return tenant_id


def update_tenant(tenant_id: int, name: str, website: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE tenants SET name=?, website=? WHERE id=?",
                     (name, website, tenant_id))
        _upsert_own_brand(conn, tenant_id, name, website)


def delete_tenant(tenant_id: int) -> None:
    """Remove a client and everything it owns (runs cascade to mentions/citations)."""
    with connect() as conn:
        conn.execute("DELETE FROM mentions WHERE run_id IN "
                     "(SELECT id FROM runs WHERE tenant_id = ?)", (tenant_id,))
        conn.execute("DELETE FROM citations WHERE run_id IN "
                     "(SELECT id FROM runs WHERE tenant_id = ?)", (tenant_id,))
        conn.execute("DELETE FROM runs WHERE tenant_id = ?", (tenant_id,))
        conn.execute("DELETE FROM prompts WHERE tenant_id = ?", (tenant_id,))
        # mentions.brand_id references brands without cascade — clear orphans first
        conn.execute("DELETE FROM mentions WHERE brand_id IN "
                     "(SELECT id FROM brands WHERE tenant_id = ?)", (tenant_id,))
        conn.execute("DELETE FROM brands WHERE tenant_id = ?", (tenant_id,))
        # Content Studio data: sites cascade to pages cascade to chunks
        conn.execute("DELETE FROM drafts WHERE tenant_id = ?", (tenant_id,))
        conn.execute("DELETE FROM sites WHERE tenant_id = ?", (tenant_id,))
        conn.execute("DELETE FROM tenants WHERE id = ?", (tenant_id,))


def _unique_slug(conn, base: str, table: str) -> str:
    slug, i = base, 2
    while q1(conn, f"SELECT id FROM {table} WHERE slug = ?", (slug,)):
        slug, i = f"{base}-{i}", i + 1
    return slug


def _upsert_own_brand(conn, tenant_id: int, name: str, website: str) -> int:
    """Ensure the tenant's tracked-brand row exists and is current."""
    aliases = default_aliases(name, website)
    own = q1(conn, "SELECT id FROM brands WHERE tenant_id = ? AND is_own = 1",
             (tenant_id,))
    if own:
        conn.execute(
            "UPDATE brands SET name=?, website=?, aliases=? WHERE id=?",
            (name, website, aliases, own["id"]))
        return own["id"]
    cur = conn.execute(
        "INSERT INTO brands (name, slug, website, aliases, is_own, tenant_id)"
        " VALUES (?, ?, ?, ?, 1, ?)",
        (name, _unique_slug(conn, slugify(name), "brands"), website, aliases, tenant_id))
    return cur.lastrowid


# --- Tenant-scoped brand/competitor helpers ----------------------------------

def own_brand(conn, tenant_id: int) -> dict | None:
    row = q1(conn, "SELECT * FROM brands WHERE tenant_id = ? AND is_own = 1",
             (tenant_id,))
    return dict(row) if row else None


def competitors(conn, tenant_id: int) -> list:
    return q(conn, "SELECT * FROM brands WHERE tenant_id = ? AND is_own = 0 ORDER BY name",
             (tenant_id,))


def add_competitor(tenant_id: int, name: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO brands (name, slug, aliases, is_own, tenant_id)"
            " VALUES (?, ?, ?, 0, ?)",
            (name, _unique_slug(conn, slugify(name), "brands"),
             default_aliases(name, ""), tenant_id))


def remove_competitor(tenant_id: int, brand_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM brands WHERE id = ? AND tenant_id = ? AND is_own = 0",
            (brand_id, tenant_id))


def alias_map(conn, tenant_id: int) -> dict[str, str]:
    """Extraction alias map {lowercase alias: brand display name} for one tenant."""
    out: dict[str, str] = {}
    for row in conn.execute("SELECT name, aliases FROM brands WHERE tenant_id = ?",
                            (tenant_id,)):
        names = [row["name"], *[a for a in row["aliases"].split("\n") if a]]
        for a in names:
            out[a.lower()] = row["name"]
    return out


def brand_domains(conn, tenant_id: int) -> set[str]:
    """Root domains of one tenant's tracked brand + its competitors."""
    domains = set()
    for row in conn.execute(
            "SELECT website FROM brands WHERE tenant_id = ? AND website != ''", (tenant_id,)):
        d = domain_of(row["website"])
        if d:
            domains.add(d)
    return domains
