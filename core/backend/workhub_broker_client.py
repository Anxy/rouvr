"""Token-free file/CLI interface shared by agents, Supervisor and activities."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket

from backend.workhub_command_broker import MAX_FRAME, Refused, receive


class BrokerClient:
    def __init__(self, socket_path):
        self.socket_path = str(socket_path)

    def request(self, frame):
        raw = json.dumps(frame, allow_nan=False).encode() + b'\n'
        if len(raw) > MAX_FRAME:
            raise Refused('FRAME_TOO_LARGE')
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(45)
                sock.connect(self.socket_path)
                sock.sendall(raw)
                reply = receive(sock)
        except OSError:
            raise Refused('BROKER_UNAVAILABLE') from None
        if reply.get('ok') is not True:
            raise Refused(reply.get('code', 'BROKER_FAILURE'))
        return reply['result']

    def submit(self, intent):
        return self.request({'operation': 'submit', 'intent': intent})

    def submit_file(self, path):
        # The submitting process reads its own file. The server never opens an
        # agent-selected path and a file-supplied PID confers no authority.
        return self.submit(json.loads(Path(path).read_text()))

    def read(self, kind, task_number):
        return self.request({'operation': 'read', 'kind': kind, 'task_number': task_number})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['submit'])
    parser.add_argument('intent_file')
    parser.add_argument('--socket', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(BrokerClient(args.socket).submit_file(args.intent_file)))
    except Refused as exc:
        print(json.dumps({'ok': False, 'code': exc.code}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
