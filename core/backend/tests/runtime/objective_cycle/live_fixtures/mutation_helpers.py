from typing import TypedDict


class FeatureFlag(TypedDict):
    name: str
    version: int


def bump_version(feature: FeatureFlag) -> FeatureFlag:
    """Return a shallow copy of feature with its version increased by one."""
    result = feature.copy()
    result["version"] += 1
    return result