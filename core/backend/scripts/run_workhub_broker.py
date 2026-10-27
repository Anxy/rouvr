"""Operator entrypoint; requires #121 backend.workhub_projection on Python path.

Run as python -m backend.scripts.run_workhub_broker --config /private/config.json.
Config and credentials are broker-owned 0600 files; registry keys are Linux
uid:pid:start_ticks populated by the trusted Supervisor after worker launch.
"""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import signal
import threading

from backend.workhub_command_broker import Broker, BrokerServer, private_json
from backend.workhub_broker_transport import WorkHubTransport


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    config = private_json(parser.parse_args().config)
    if config.get('projection_module'):
        path = Path(config['projection_module'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != config.get('projection_sha256'):
            raise ValueError('PROJECTION_DEPENDENCY_DIGEST_MISMATCH')
        spec = importlib.util.spec_from_file_location('aix_projection_dependency', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        projection_type = module.WorkHubProjection
    else:
        from backend.workhub_projection import WorkHubProjection
        projection_type = WorkHubProjection
    transport = WorkHubTransport(base_url=config['workhub_base_url'], credential_file=config['credential_file'], tenant_id=config['tenant_id'], project_id=config['project_id'], mutation_contract=config.get('mutation_contract'))
    projection = projection_type(root=config['projection_dir'], reader=transport, project_id=config['project_id'], max_age_seconds=config.get('max_age_seconds', 60))
    broker = Broker(state_dir=config['state_dir'], tenant_id=config['tenant_id'], project_id=config['project_id'], principals=config['principals'], projection=projection, transport=transport)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    try:
        BrokerServer(broker, config['socket_path']).serve(stop)
    finally:
        broker.close()


if __name__ == '__main__':
    main()
