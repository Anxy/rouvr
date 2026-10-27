from __future__ import annotations

import json


def main() -> int:
    payload = {
        "success": False,
        "code": "LEGACY_ENTRYPOINT_RETIRED",
        "message": "hyeve-aix-service/backend/scripts/wh_fast_read.py is retired.",
        "migration": {
            "primary_read": "python scripts/wh.py read ...",
            "direct_read_engine": "python scripts/wh-read.py ...",
            "gateway_registry": "python scripts/wh.py help registry",
        },
    }
    print(json.dumps(payload, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
