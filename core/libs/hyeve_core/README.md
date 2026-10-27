# Hyeve Core DAL Library

A robust, scalable Data Access Layer (DAL) library for the Hyeve multi-tenant SaaS ERP platform. The DAL provides a unified interface supporting multiple database technologies (Prisma ORM, SQLAlchemy, asyncpg) with Redis caching, automatic tenant isolation, soft deletes, and ULID identifiers.

# Core Libraries

The monorepo now ships the meta-driven building blocks referenced in the
Hyeve architecture notes:

- `hyeve_core.core_libs` – shared dataclasses and abstract interfaces.
- `hyeve_core.packs` – filesystem-backed pack resolver with layered overrides.
- `hyeve_core.validation` – default validation engine built on the shared
  contracts (see `validation.service.DefaultValidationService`).
- `hyeve_core.errors` – layered error catalog resolving codes to locale-aware
  messages (`errors.catalog.LayeredErrorCatalog`).

These packages are designed to be pulled into every generated service; BIVR
imports only the interfaces so concrete implementations can evolve
independently.

## Features

- **Multi-Database Support**: Prisma ORM (primary), SQLAlchemy (fallback), asyncpg (raw SQL)
- **Redis Caching**: Automatic caching with tenant isolation and pattern-based invalidation
- **Tenant Isolation**: Built-in multi-tenant data separation and security
- **Soft Deletes**: Automatic soft delete functionality with audit trails
- **ULID Identifiers**: Universally Unique Lexicographically Sortable Identifiers
- **Data Validation**: Pydantic-based validation for all operations
- **Workflow Integration**: Ayrpack integration for packet routing
- **Code Generation**: BIVR integration for automatic schema and CRUD generation
- **Comprehensive Testing**: Unit, integration, and performance tests

## Installation

```bash
pip install hyeve-core-dal
```

For development:

```bash
pip install hyeve-core-dal[dev]
```

## Quick Start

```python
from hyeve_core.dal import RobustDALService, DALConfig

# Load configuration
config = DALConfig.load_from_file("dal_config.json")

# Initialize DAL service
dal = RobustDALService(config)
await dal.initialize()

# Perform CRUD operations
result = await dal.execute_crud(operation)
```

## Configuration

Create a `dal_config.json` file:

```json
{
  "database": {
    "primary": {
      "type": "prisma",
      "connection_string": "${DATABASE_URL}"
    },
    "fallback": {
      "type": "sqlalchemy", 
      "connection_string": "${DATABASE_URL}"
    }
  },
  "cache": {
    "enabled": true,
    "redis_url": "${REDIS_URL}",
    "default_ttl": 3600
  },
  "tenant": {
    "isolation_mode": "row_level_security",
    "tenant_id_column": "tenant_id"
  }
}
```

## Development

This library is currently under active development. See the implementation tasks in `.kiro/specs/robust-dal-library/tasks.md` for current progress.

### Project Structure

```
hyeve_core/
├── src/dal/
│   ├── __init__.py           # Package exports
│   ├── interfaces.py         # Abstract base classes
│   ├── exceptions.py         # Custom exceptions
│   ├── dal_service.py        # Main DAL service
│   ├── dal_config.py         # Configuration management
│   ├── dal_cache.py          # Redis cache manager
│   ├── dal_models.py         # Pydantic models
│   └── ayrpack_integration.py # Workflow integration
├── pyproject.toml            # Project configuration
└── README.md                 # This file
```

## Requirements

- Python 3.9+
- PostgreSQL 12+
- Redis 6+
- Prisma CLI (for schema generation)

## License

MIT License - see LICENSE file for details.

## Contributing

See CONTRIBUTING.md for development guidelines and contribution process.
