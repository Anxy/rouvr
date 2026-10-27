"""
Hyeve Core DAL Library

A robust, scalable Data Access Layer (DAL) library for the Hyeve multi-tenant SaaS ERP platform.
Provides unified interface supporting multiple database technologies with Redis caching,
automatic tenant isolation, soft deletes, and ULID identifiers.
"""

from .ayrpack_integration import AyrpackIntegration
from .dal_cache import DALCacheManager
from .dal_config import DALConfig
from .dal_models import BaseDALModel, CRUDOperation, CRUDResult
from .dal_service import RobustDALService


class DALServiceClient:
    def __init__(self, connection_string: str | None = None, tenant_id: str | None = None):
        self.connection_string = connection_string
        self.tenant_id = tenant_id


__version__ = "0.1.0"
__all__ = [
    "RobustDALService",
    "DALServiceClient",
    "DALConfig",
    "DALCacheManager",
    "BaseDALModel",
    "CRUDOperation",
    "CRUDResult",
    "AyrpackIntegration",
]
