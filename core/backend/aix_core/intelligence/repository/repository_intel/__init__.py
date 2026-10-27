"""P4 repository intelligence packages (C030+)."""

from backend.aix_core.intelligence.repository.repository_intel.symbol_index import (
    INDEX_SCHEMA,
    FileIndexEntry,
    LanguageAwareSymbolIndex,
    SymbolIndex,
    SymbolIndexError,
    SymbolRef,
    detect_language,
    extract_symbols,
)

__all__ = [
    "INDEX_SCHEMA",
    "FileIndexEntry",
    "LanguageAwareSymbolIndex",
    "SymbolIndex",
    "SymbolIndexError",
    "SymbolRef",
    "detect_language",
    "extract_symbols",
]
