"""
Shared error catalog for generated backend services.
"""

try:
    from hyeve_core.errors import ErrorCatalogConfig, LayeredErrorCatalog
except ImportError:
    from dataclasses import dataclass, field
    from typing import Any

    @dataclass
    class ErrorCatalogConfig:
        default_messages: dict[str, Any] = field(default_factory=dict)
        default_http_hints: dict[str, Any] = field(default_factory=dict)
        default_locale: str = "en"

    class LayeredErrorCatalog:
        def __init__(self, config: ErrorCatalogConfig) -> None:
            self.config = config

DEFAULT_MESSAGES = {
    "en": {
        "form": {
            "save_failed": "Failed to save record",
            "save_success": "Record saved",
            "validation_failed": "Please correct the highlighted fields.",
        },
        "validation": {
            "api": "{fieldLabel} failed server validation",
            "boolean": "{fieldLabel} must be true or false",
            "email": "{fieldLabel} must be a valid email",
            "invalid_option": "{fieldLabel} must be a valid option",
            "max_length": "{fieldLabel} must be at most {maxLength} characters",
            "number": {
                "max": "{fieldLabel} must be less than or equal to {max}",
                "min": "{fieldLabel} must be greater than or equal to {min}",
                "type": "{fieldLabel} must be a number",
            },
            "pattern": "{fieldLabel} is invalid",
            "required": "{fieldLabel} is required",
        },
    },
}
DEFAULT_HTTP_HINTS = {
    "form.save_failed": 500,
    "form.save_success": 200,
    "form.validation_failed": 422,
    "validation.api": 422,
    "validation.boolean": 422,
    "validation.email": 422,
    "validation.invalid_option": 422,
    "validation.max_length": 422,
    "validation.number.max": 422,
    "validation.number.min": 422,
    "validation.number.type": 422,
    "validation.pattern": 422,
    "validation.required": 422,
}


def build_error_catalog() -> LayeredErrorCatalog:
    return LayeredErrorCatalog(
        ErrorCatalogConfig(
            default_messages=DEFAULT_MESSAGES,
            default_http_hints=DEFAULT_HTTP_HINTS,
            default_locale="en",
        ),
    )
