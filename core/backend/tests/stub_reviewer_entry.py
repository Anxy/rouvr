"""Supervised verifier stub for leak-inspection tests. Reads the capability
token from stdin only; prints lengths, never bytes."""

import sys


def main() -> int:
    attempt = ""
    args = sys.argv[1:]
    for i, part in enumerate(args):
        if part == "--attempt" and i + 1 < len(args):
            attempt = args[i + 1]
    token = sys.stdin.read()
    print(f"attempt={attempt} received_chars={len(token)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
