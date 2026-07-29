"""Draft CRUD for generated marketing copy."""

from .db import connect, q, q1


def list_drafts(tenant_id: int, status: str | None = None) -> list[dict]:
    with connect() as conn:
        if status and status in ("draft", "published"):
            rows = q(conn,
                     """SELECT d.*, s.domain FROM drafts d
                        LEFT JOIN sites s ON s.id = d.site_id
                        WHERE d.tenant_id = ? AND d.status = ?
                        ORDER BY d.updated_at DESC""",
                     (tenant_id, status))
        else:
            rows = q(conn,
                     """SELECT d.*, s.domain FROM drafts d
                        LEFT JOIN sites s ON s.id = d.site_id
                        WHERE d.tenant_id = ?
                        ORDER BY d.updated_at DESC""",
                     (tenant_id,))
        return [dict(r) for r in rows]


def get_draft(tenant_id: int, draft_id: int) -> dict | None:
    with connect() as conn:
        r = q1(conn,
               """SELECT d.*, s.domain FROM drafts d
                  LEFT JOIN sites s ON s.id = d.site_id
                  WHERE d.id = ? AND d.tenant_id = ?""",
               (draft_id, tenant_id))
        return dict(r) if r else None


def create_draft(tenant_id: int, site_id: int | None, prompt: str,
                 fmt: str, content: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO drafts (tenant_id, site_id, prompt, format, content)
               VALUES (?, ?, ?, ?, ?)""",
            (tenant_id, site_id, prompt, fmt, content),
        )
        return cur.lastrowid


def update_draft(tenant_id: int, draft_id: int, **kwargs) -> bool:
    allowed = {"content", "status", "format"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return False
    updates["updated_at"] = None  # will be set by SQL
    set_clause = ", ".join(
        f"{k} = " + (f"'{v}'" if k != "updated_at" else "datetime('now')")
        for k, v in updates.items()
    )
    with connect() as conn:
        conn.execute(
            f"UPDATE drafts SET {set_clause} WHERE id = ? AND tenant_id = ?",
            (draft_id, tenant_id),
        )
        return True


def delete_draft(tenant_id: int, draft_id: int) -> bool:
    with connect() as conn:
        conn.execute("DELETE FROM drafts WHERE id = ? AND tenant_id = ?",
                     (draft_id, tenant_id))
        return True
