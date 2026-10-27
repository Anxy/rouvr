import pytest
from cli.rouvr_cli.__main__ import main


def test_help_exits_cleanly():
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0
