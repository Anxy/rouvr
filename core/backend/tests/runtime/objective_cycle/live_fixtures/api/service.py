"""In-memory item store for the live-fixture items API."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count
from typing import Any


_id_seq = count(1)


@dataclass
class Item:
    id: int
    title: str
    body: str = ""
    archived: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "archived": self.archived,
        }


@dataclass
class ItemStore:
    _items: dict[int, Item] = field(default_factory=dict)

    def create(self, *, title: str, body: str = "") -> Item:
        item = Item(id=next(_id_seq), title=title, body=body, archived=False)
        self._items[item.id] = item
        return item

    def get(self, item_id: int) -> Item | None:
        return self._items.get(int(item_id))

    def archive(self, item_id: int) -> Item | None:
        item = self.get(item_id)
        if item is None:
            return None
        item.archived = True
        return item

    def list_items(self, *, include_archived: bool = False) -> list[Item]:
        rows = list(self._items.values())
        if not include_archived:
            rows = [i for i in rows if not i.archived]
        return sorted(rows, key=lambda i: i.id)


STORE = ItemStore()


def reset_store() -> None:
    global STORE, _id_seq
    STORE = ItemStore()
    _id_seq = count(1)
