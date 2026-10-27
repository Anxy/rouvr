"""Compatibility shim for generated repository modules.

The generated repository layer still imports `backend.models.orm_models`, but
the active AIX backend no longer ships concrete ORM model definitions here.
Provide lightweight placeholders so static analysis can resolve the imports
without forcing broad rewrites of currently inactive generated files.
"""

from __future__ import annotations

from typing import Any, cast


ServiceConfigOrm = cast(Any, object)
OutboxOrm = cast(Any, object)
InboxOrm = cast(Any, object)
IdempotencyKeysOrm = cast(Any, object)
FeatureFlagsOrm = cast(Any, object)
AixModelProviderBindingsOrm = cast(Any, object)
AixModelHealthStatusOrm = cast(Any, object)
AixModelGatewayPoliciesOrm = cast(Any, object)
AixGatewayDecisionsOrm = cast(Any, object)
AixEscalationPoliciesOrm = cast(Any, object)
AdminAuditLogOrm = cast(Any, object)
