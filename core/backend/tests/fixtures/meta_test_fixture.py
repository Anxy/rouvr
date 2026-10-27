from __future__ import annotations


class FixtureService:
    async def _connect(self):
        raise NotImplementedError

    async def list_items(
        self,
        tenant_id: str,
        limit: int,
        offset: int,
        item_type: str | None = None,
    ) -> dict:
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                "SELECT * FROM items WHERE tenant_id = $1 LIMIT $2 OFFSET $3",
                tenant_id,
                limit,
                offset,
            )
        finally:
            await conn.close()
        return {"items": [dict(r) for r in rows]}
