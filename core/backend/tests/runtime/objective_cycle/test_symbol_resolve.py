"""Tests for symbol_resolve module — contract, single match, missing symbol, and ambiguous symbol."""

import pytest
from backend.aix_core.intelligence.repository.symbol_resolve import SymbolResolver


def test_resolve_symbol_single_match_resolved() -> None:
    """Symbol found uniquely resolves to RESOLVED."""
    resolver = SymbolResolver()
    content_by_file = {
        "services/tax.py": (
            "import math\n\n"
            "def calculate_tax(amount: float) -> float:\n"
            "    return amount * 0.15\n"
        )
    }

    res = resolver.resolve_symbol(
        symbol_name="calculate_tax",
        content_by_file=content_by_file,
    )

    assert res.status == "RESOLVED"
    assert res.reason_code is None
    assert len(res.locations) == 1
    assert res.locations[0].file_path == "services/tax.py"
    assert res.locations[0].start_line == 3


def test_resolve_symbol_not_found() -> None:
    """Missing symbol returns SYMBOL_NOT_FOUND without guessing."""
    resolver = SymbolResolver()
    content_by_file = {
        "services/tax.py": "def calculate_tax(amount):\n    return amount * 0.15\n"
    }

    res = resolver.resolve_symbol(
        symbol_name="process_payment",
        content_by_file=content_by_file,
    )

    assert res.status == "SYMBOL_NOT_FOUND"
    assert res.reason_code == "SYMBOL_NOT_FOUND"
    assert len(res.locations) == 0


def test_resolve_symbol_ambiguous_and_disambiguation() -> None:
    """Duplicate symbol definitions return SYMBOL_AMBIGUOUS unless target_file disambiguates."""
    resolver = SymbolResolver()
    content_by_file = {
        "services/auth.py": "def handle_request(req):\n    pass\n",
        "services/billing.py": "def handle_request(req):\n    pass\n",
    }

    # Without target file -> ambiguous!
    res_ambiguous = resolver.resolve_symbol(
        symbol_name="handle_request",
        content_by_file=content_by_file,
    )

    assert res_ambiguous.status == "SYMBOL_AMBIGUOUS"
    assert res_ambiguous.reason_code == "SYMBOL_AMBIGUOUS"
    assert len(res_ambiguous.locations) == 2

    # With target_file specified -> resolved uniquely!
    res_resolved = resolver.resolve_symbol(
        symbol_name="handle_request",
        target_file="services/auth.py",
        content_by_file=content_by_file,
    )

    assert res_resolved.status == "RESOLVED"
    assert res_resolved.reason_code is None
    assert len(res_resolved.locations) == 1
    assert res_resolved.locations[0].file_path == "services/auth.py"
