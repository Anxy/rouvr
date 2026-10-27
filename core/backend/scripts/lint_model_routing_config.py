from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.services.config.model_routing_loader import (
    DEFAULT_MODEL_ROUTING_CONFIG_PATH,
    load_model_routing_config,
    model_routing_config_snapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint and summarize the AIX model routing config.")
    parser.add_argument(
        "--config-path",
        default=str(DEFAULT_MODEL_ROUTING_CONFIG_PATH),
        help="Path to model_routing_config.yaml",
    )
    args = parser.parse_args()
    config_path = Path(str(args.config_path))
    config = load_model_routing_config(config_path=config_path)
    snapshot = model_routing_config_snapshot(config=config, config_path=config_path)
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
