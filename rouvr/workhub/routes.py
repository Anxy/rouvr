"""rouvr/workhub/routes.py — WorkHub routes Rouvr uses (R1..R10 from the doc).

Prefixes code-confirmed against hyeve-workhub-service @ 9798926 in A1
(contract router prefix /api/v1, v2 router prefix /api/v2/workhub).
Live /openapi.json confirmation still pending staging access.
"""
R1_TASK_BY_NUMBER = "/api/v1/projects/{project}/tasks/by-number/{number}"
R2_TASK = "/api/v2/workhub/tasks/{task_id}"
R3_TASK_LIST = "/api/v2/workhub/tasks"  # UNVERIFIED query params
R4_BINDING_BY_NUMBER = "/api/v1/projects/{project}/tasks/by-number/{number}/assignment-binding"
R5_EVIDENCE_POST = "/api/v1/tasks/{task_id}/evidence"
R6_EVIDENCE_GET = "/api/v1/tasks/{task_id}/evidence"
R7_EVALUATE = "/api/v1/tasks/{task_id}/verification/evaluate"
R8_TRANSITIONS = "/api/v1/projects/{project}/tasks/transitions/batch"
R9_CAPABILITIES = "/api/v1/capabilities"
# R10 assignment lifecycle (A1-confirmed paths; router :5297-:7351).
R10_CLAIM = "/api/v1/projects/{project}/assignments/claim"
R10_ATTEMPT = "/api/v1/projects/{project}/attempts"
R10_RESULT = "/api/v1/projects/{project}/results"
R10_RECLAIM = "/api/v1/projects/{project}/assignments/reclaim"
R10_LEASE_CLAIM = "/api/v1/projects/{project}/leases/claim"
R10_LEASE_RENEW = "/api/v1/projects/{project}/leases/renew"
R10_RECLAIM_DEAD = "/api/v1/projects/{project}/reclaim-dead-assignments"
R10_LIVENESS_RENEW = "/api/v1/projects/{project}/assignment-liveness/renew"
AUTH_SERVICE_TOKEN = "/v1/auth/service-tokens"
