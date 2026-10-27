"""Application model for the live-fixture user store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class FixtureUser:
    id: int
    email: str
    display_name: str
    email_verified: bool = False

    @classmethod
    def from_row(cls, row: Any) -> "FixtureUser":
        return cls(
            id=int(row["id"]),
            email=str(row["email"]),
            display_name=str(row["display_name"] or ""),
            email_verified=bool(int(row["email_verified"] if "email_verified" in row.keys() else 0)),
        )


@dataclass
class FixtureItem:
    id: int
    title: str
    body: str
    archived: bool = False

    @classmethod
    def from_row(cls, row: Any) -> "FixtureItem":
        return cls(
            id=int(row["id"]),
            title=str(row["title"]),
            body=str(row["body"] or ""),
            archived=bool(int(row["archived"])),
        )


def insert_user(conn: Any, *, email: str, display_name: str = "") -> FixtureUser:
    cur = conn.execute(
        "INSERT INTO fixture_users(email, display_name) VALUES (?, ?)",
        (email, display_name),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id, email, display_name, email_verified FROM fixture_users WHERE id = ?",
        (int(cur.lastrowid),),
    ).fetchone()
    return FixtureUser.from_row(row)


def get_user(conn: Any, user_id: int) -> FixtureUser | None:
    row = conn.execute(
        "SELECT id, email, display_name, email_verified FROM fixture_users WHERE id = ?",
        (int(user_id),),
    ).fetchone()
    return FixtureUser.from_row(row) if row is not None else None


def insert_item(conn: Any, *, title: str, body: str = "") -> FixtureItem:
    cur = conn.execute(
        "INSERT INTO fixture_items(title, body) VALUES (?, ?)",
        (title, body),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id, title, body, archived FROM fixture_items WHERE id = ?",
        (int(cur.lastrowid),),
    ).fetchone()
    return FixtureItem.from_row(row)


def get_item(conn: Any, item_id: int) -> FixtureItem | None:
    row = conn.execute(
        "SELECT id, title, body, archived FROM fixture_items WHERE id = ?",
        (int(item_id),),
    ).fetchone()
    return FixtureItem.from_row(row) if row is not None else None


def list_items(conn: Any, *, include_archived: bool = False) -> list[FixtureItem]:
    query = "SELECT id, title, body, archived FROM fixture_items"
    params: tuple[Any, ...] = ()
    if not include_archived:
        query += " WHERE archived = 0"
    query += " ORDER BY id"
    return [FixtureItem.from_row(row) for row in conn.execute(query, params)]


def archive_item(conn: Any, item_id: int) -> FixtureItem | None:
    conn.execute(
        "UPDATE fixture_items SET archived = 1 WHERE id = ?",
        (int(item_id),),
    )
    conn.commit()
    return get_item(conn, item_id)
