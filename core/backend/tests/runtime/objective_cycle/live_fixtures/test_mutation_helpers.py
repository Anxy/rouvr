from .mutation_helpers import bump_version


def test_bump_version_increments_copy_without_mutating_original():
    feature = {"name": "x", "version": 3}

    updated = bump_version(feature)

    assert updated["version"] == 4
    assert feature["version"] == 3