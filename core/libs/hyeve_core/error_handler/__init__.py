"""
Hyeve Core Error Handler
=======================

Centralized error handling system for Hyeve microservices.
Provides structured error handling, logging, and monitoring capabilities.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ErrorInfo:
    """Structured error information."""

    code: str
    message: str
    severity: str = "ERROR"
    category: str = "SYSTEM"
    module: str = ""
    function: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    context: dict[str, Any] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)
    recoverable: bool = False
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "category": self.category,
            "module": self.module,
            "function": self.function,
            "timestamp": self.timestamp,
            "context": self.context,
            "suggestions": self.suggestions,
            "recoverable": self.recoverable,
            "correlation_id": self.correlation_id,
        }


class ErrorCategory:
    """Error category constants."""

    SYSTEM = "SYSTEM"
    VALIDATION = "VALIDATION"
    SECURITY = "SECURITY"
    DATABASE = "DATABASE"
    NETWORK = "NETWORK"
    CONFIGURATION = "CONFIGURATION"
    BUSINESS_LOGIC = "BUSINESS_LOGIC"


class ErrorSeverity:
    """Error severity constants."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ErrorHandler:
    """
    Centralized error handling system for Hyeve services.

    Provides structured error handling, logging, and monitoring capabilities
    that can be used across all microservices.
    """

    def __init__(
        self, service_name: str = "hyeve-service", enable_structured_logging: bool = True, log_level: str = "INFO"
    ):
        self.service_name = service_name
        self.enable_structured_logging = enable_structured_logging

        # Configure logging
        self._setup_logging(log_level)

        # Error tracking
        self.errors: list[ErrorInfo] = []
        self.warnings: list[ErrorInfo] = []
        self.correlation_map: dict[str, ErrorInfo] = {}

        # Recovery tracking
        self.recovery_attempts: dict[str, int] = {}

    def _setup_logging(self, log_level: str) -> None:
        """Setup structured logging."""
        level = getattr(logging, log_level.upper(), logging.INFO)
        logger.setLevel(level)

        if self.enable_structured_logging and not logger.handlers:
            # Add structured JSON handler if not already present
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                json.dumps(
                    {
                        "timestamp": "%(asctime)s",
                        "level": "%(levelname)s",
                        "service": self.service_name,
                        "message": "%(message)s",
                        "module": "%(name)s",
                    }
                )
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)

    def handle_error(
        self,
        error: Exception,
        context: dict[str, Any] | None = None,
        auto_recover: bool = False,
        correlation_id: str | None = None,
        user_context: dict[str, Any] | None = None,
        tags: dict[str, str] | None = None,
    ) -> ErrorInfo:
        """
        Handle an error with structured logging and tracking.

        Args:
            error: The exception to handle
            context: Additional context information
            auto_recover: Whether to attempt automatic recovery
            correlation_id: Optional correlation ID for request tracking
            user_context: User context for error tracking
            tags: Additional tags for error categorization

        Returns:
            ErrorInfo object with structured error details
        """
        # Generate correlation ID if not provided
        if not correlation_id:
            correlation_id = f"{self.service_name}_{datetime.now(UTC).timestamp()}"

        # Extract error details
        error_info = ErrorInfo(
            code=getattr(error, "code", "UNKNOWN_ERROR"),
            message=str(error),
            severity=ErrorSeverity.ERROR,
            category=getattr(error, "category", ErrorCategory.SYSTEM),
            module=getattr(error, "__module__", ""),
            function=getattr(error, "__name__", ""),
            context=context or {},
            recoverable=getattr(error, "recoverable", False),
            correlation_id=correlation_id,
        )

        # Add additional context
        if user_context:
            error_info.context.update({"user_context": user_context})

        if tags:
            error_info.context.update({"tags": tags})

        # Log the error
        self._log_error(error_info)

        # Store error for tracking
        self.errors.append(error_info)
        self.correlation_map[correlation_id] = error_info

        # Attempt recovery if requested
        if auto_recover and error_info.recoverable:
            self._attempt_recovery(error_info)

        return error_info

    def handle_warning(
        self,
        message: str,
        code: str = "WARNING",
        context: dict[str, Any] | None = None,
        category: str = ErrorCategory.SYSTEM,
    ) -> ErrorInfo:
        """
        Handle a warning with structured logging.

        Args:
            message: Warning message
            code: Warning code
            context: Additional context information
            category: Warning category

        Returns:
            ErrorInfo object with structured warning details
        """
        warning_info = ErrorInfo(
            code=code, message=message, severity=ErrorSeverity.WARNING, category=category, context=context or {}
        )

        # Log the warning
        self._log_warning(warning_info)

        # Store warning
        self.warnings.append(warning_info)

        return warning_info

    def _log_error(self, error_info: ErrorInfo) -> None:
        """Log error with appropriate level."""
        log_data = error_info.to_dict()

        if error_info.severity == ErrorSeverity.CRITICAL:
            logger.critical(json.dumps(log_data))
        elif error_info.severity == ErrorSeverity.ERROR:
            logger.error(json.dumps(log_data))
        elif error_info.severity == ErrorSeverity.WARNING:
            logger.warning(json.dumps(log_data))
        else:
            logger.info(json.dumps(log_data))

    def _log_warning(self, warning_info: ErrorInfo) -> None:
        """Log warning."""
        logger.warning(json.dumps(warning_info.to_dict()))

    def _attempt_recovery(self, error_info: ErrorInfo) -> None:
        """Attempt automatic recovery for recoverable errors."""
        correlation_id = error_info.correlation_id or "unknown"
        attempts = self.recovery_attempts.get(correlation_id, 0)

        if attempts < 3:  # Max 3 recovery attempts
            self.recovery_attempts[correlation_id] = attempts + 1
            logger.info(f"Attempting recovery for error {correlation_id}, attempt {attempts + 1}")

            # Add recovery suggestion
            error_info.suggestions.append("Automatic recovery attempted")
        else:
            logger.warning(f"Max recovery attempts reached for error {correlation_id}")

    def get_error_summary(self) -> dict[str, Any]:
        """Get summary of all errors and warnings."""
        error_categories: dict[str, int] = {}
        warning_categories: dict[str, int] = {}

        for error in self.errors:
            category = error.category
            error_categories[category] = error_categories.get(category, 0) + 1

        for warning in self.warnings:
            category = warning.category
            warning_categories[category] = warning_categories.get(category, 0) + 1

        return {
            "total_errors": len(self.errors),
            "total_warnings": len(self.warnings),
            "critical_errors": len([e for e in self.errors if e.severity == ErrorSeverity.CRITICAL]),
            "error_categories": error_categories,
            "warning_categories": warning_categories,
            "recoverable_errors": len([e for e in self.errors if e.recoverable]),
            "errors": [
                {
                    "code": error.code,
                    "message": error.message,
                    "severity": error.severity,
                    "category": error.category,
                    "timestamp": error.timestamp,
                    "recoverable": error.recoverable,
                }
                for error in self.errors[-10:]  # Last 10 errors
            ],
            "warnings": [
                {
                    "code": warning.code,
                    "message": warning.message,
                    "category": warning.category,
                    "timestamp": warning.timestamp,
                }
                for warning in self.warnings[-10:]  # Last 10 warnings
            ],
        }

    def clear_errors(self) -> None:
        """Clear all recorded errors and warnings."""
        self.errors.clear()
        self.warnings.clear()
        self.recovery_attempts.clear()

    def has_errors(self) -> bool:
        """Check if any errors have been recorded."""
        return len(self.errors) > 0

    def has_critical_errors(self) -> bool:
        """Check if any critical errors have been recorded."""
        return any(error.severity == ErrorSeverity.CRITICAL for error in self.errors)

    def export_error_report(self, file_path: str | None = None) -> str:
        """Export comprehensive error report to file."""
        if file_path is None:
            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            file_path = f"error_report_{timestamp}.json"

        report = {
            "timestamp": datetime.now(UTC).isoformat(),
            "service": self.service_name,
            "summary": self.get_error_summary(),
            "detailed_errors": [error.to_dict() for error in self.errors],
            "detailed_warnings": [warning.to_dict() for warning in self.warnings],
            "recovery_attempts": self.recovery_attempts,
        }

        try:
            with open(file_path, "w") as f:
                json.dump(report, f, indent=2)
            logger.info(f"Error report exported to {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Failed to export error report to {file_path}: {str(e)}")
            return ""

    def get_troubleshooting_guide(self, error_code: str | None = None) -> dict[str, Any]:
        """Get troubleshooting guide for specific error or general guidance."""
        if error_code:
            # Find specific error
            matching_errors = [e for e in self.errors if e.code == error_code]
            if matching_errors:
                error = matching_errors[0]
                return {
                    "error_code": error_code,
                    "description": error.message,
                    "category": error.category,
                    "suggestions": error.suggestions,
                    "context": error.context,
                    "recoverable": error.recoverable,
                }

        # General troubleshooting
        return {
            "common_issues": {
                "VALIDATION_ERROR": {
                    "description": "Data validation failed",
                    "solutions": ["Check input data format", "Verify required fields", "Review validation rules"],
                },
                "DATABASE_ERROR": {
                    "description": "Database operation failed",
                    "solutions": ["Check database connection", "Verify table permissions", "Review query syntax"],
                },
                "NETWORK_ERROR": {
                    "description": "Network communication failed",
                    "solutions": ["Check network connectivity", "Verify service endpoints", "Review timeout settings"],
                },
            },
            "system_checks": [
                "Review application logs",
                "Check system resources",
                "Verify configuration",
                "Test service dependencies",
            ],
        }


# Global error handler instance
error_handler = ErrorHandler()


def configure_error_handler(
    service_name: str = "hyeve-service", log_level: str = "INFO", enable_structured_logging: bool = True
) -> ErrorHandler:
    """Configure the global error handler."""
    global error_handler
    error_handler = ErrorHandler(
        service_name=service_name, enable_structured_logging=enable_structured_logging, log_level=log_level
    )
    return error_handler
