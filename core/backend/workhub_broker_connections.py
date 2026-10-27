"""Connect maintained MSG-085 consumer to the single broker and canonical Temporal.

Supervisor dispatch/result submission and activity-host binding are outside
this candidate's scope (#126 F2). #120 completion does not bind this broker.
This module only exposes the consumer adapter; it does not launch workers or
register a parallel workflow.
"""
from __future__ import annotations

import time

from backend.aix_event_consumer.admission import DefaultAdmissionPolicy
from backend.aix_event_consumer.consumer import AixEventConsumer
from backend.workhub_broker_client import BrokerClient


class ConsumerProjection:
    def __init__(self, client):
        self.client = client

    def read_task_snapshot(self, n):
        try:
            return self.client.read('task', n)
        except Exception:
            return {'status': 'UNAVAILABLE'}  # never None: consumer must defer

    def is_fresh(self, snapshot):
        return snapshot.get('status') == 'OK'

    def snapshot_detail(self, snapshot):
        return snapshot['data']

    def read_task(self, n):
        return self.client.read('task', n)['data']


def connect_consumer(*, socket_path, event_source, cursor, temporal, tenant_id):
    """temporal is #119 TemporalClientAdapter over canonical runtime manager."""
    projection = ConsumerProjection(BrokerClient(socket_path))
    return AixEventConsumer(event_source=event_source, cursor=cursor, temporal=temporal,
                            admission=DefaultAdmissionPolicy(allowed_tenants=(tenant_id,)),
                            projection_reader=projection, workhub_reader=projection)


def agent_wake_metric(*, event_id, task_id, event_at_epoch, woke_at_epoch=None):
    woke = time.time() if woke_at_epoch is None else woke_at_epoch
    return {'metric': 'aix.workhub_broker.event_to_agent_wake', 'event_id': event_id,
            'task_id': task_id, 'event_at_epoch': event_at_epoch,
            'agent_woke_at_epoch': woke, 'duration_ms': max(0, int((woke - event_at_epoch) * 1000))}
