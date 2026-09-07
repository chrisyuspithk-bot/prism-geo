"""Draft CRUD for generated marketing copy."""

from .db import connect, q, q1


# Exclude fb_image/ig_image (large base64 data URLs) from list queries.
_DRAFT_LIST_COLS = ("d.id", "d.tenant_id", "d.site_id", "d.prompt", "d.format",
                    "d.content", "d.status", "d.created_at", "d.updated_at")


def list_drafts(tenant_id: int, status: str | None = None) -> list[dict]:
    cols = ", ".join(_DRAFT_LIST_COLS)
    with connect() as conn:
        if status and status in ("draft", "published"):
            rows = q(conn,
                     f"""SELECT {cols}, s.domain FROM drafts d
                         LEFT JOIN sites s ON s.id = d.site_id
                         WHERE d.tenant_id = ? AND d.status = ?
                         ORDER BY d.updated_at DESC""",
                     (tenant_id, status))
        else:
            rows = q(conn,
                     f"""SELECT {cols}, s.domain FROM drafts d
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
                 fmt: str, content: str, fb_image: str = "", ig_image: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO drafts (tenant_id, site_id, prompt, format, content, fb_image, ig_image)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tenant_id, site_id, prompt, fmt, content, fb_image, ig_image),
        )
        return cur.lastrowid


def update_draft(tenant_id: int, draft_id: int, **kwargs) -> bool:
    allowed = {"content", "status", "format", "fb_image", "ig_image", "prompt"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return False
    sets = [f"{k} = ?" for k in updates]
    sets.append("updated_at = datetime('now')")
    values = list(updates.values()) + [draft_id, tenant_id]
    with connect() as conn:
        conn.execute(
            f"UPDATE drafts SET {', '.join(sets)} WHERE id = ? AND tenant_id = ?",
            tuple(values),
        )
        return True


def delete_draft(tenant_id: int, draft_id: int) -> bool:
    with connect() as conn:
        conn.execute("DELETE FROM drafts WHERE id = ? AND tenant_id = ?",
                     (draft_id, tenant_id))
        return True
