# CodeIntel v2 migration pointer (Wave 4 Agent1)

No migration runner exists in this repo (`backend/migrations/` holds only
`__init__.py`; `backend/prisma/` has no `migrations/` dir), so per precedent
(raw SQL in `backend/database/scripts/`, e.g. `create_schema.sql`):

- SQL to apply: `hyeve-aix-service/backend/database/scripts/add_codeintel_v2_tables.sql`
- Prisma models: `CodeIntelRepo`, `CodeIntelIndexJob`, `CodeIntelSnapshot`
  in `hyeve-aix-service/backend/prisma/service.prisma`
- SQLAlchemy model: `CodeIntelRepo`
  in `hyeve-aix-service/backend/models/codeintel_v2_models.py`

Apply manually: `psql "$DATABASE_URL" -f add_codeintel_v2_tables.sql`
