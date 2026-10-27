"""
Hyeve Core Security Module
=========================

Security utilities for handling sensitive data, PII redaction, input validation, and access control.
Provides functions for redacting personally identifiable information and sanitizing user input.
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def redact_pii_fields(data: dict[str, Any], user_role: str, pii_fields: list[str] | None = None) -> dict[str, Any]:
    """
    Redact PII fields based on user role.

    Args:
        data: Dictionary containing data that may have PII
        user_role: User's role (e.g., 'admin', 'user', 'anonymous')
        pii_fields: List of field names considered PII. If None, uses default list.

    Returns:
        Dictionary with PII fields redacted based on role
    """
    if user_role == 'admin':
        return data

    # Default PII fields if not provided
    if pii_fields is None:
        pii_fields = [
            'email', 'phone', 'address', 'ssn', 'social_security',
            'passport', 'license', 'emergency_contact', 'personal', 'private',
            'first_name', 'last_name', 'full_name', 'date_of_birth', 'birth_date'
        ]

    redacted_data = data.copy()

    for field in pii_fields:
        if field in redacted_data:
            redacted_data[field] = "***REDACTED***"

    return redacted_data


def check_access_control(user_role: str, required_role: str, resource: str) -> bool:
    """
    Check if user has access to a resource based on role hierarchy.

    Args:
        user_role: User's current role
        required_role: Required role for access
        resource: Resource being accessed

    Returns:
        True if access is granted, False otherwise
    """
    # Define role hierarchy (higher index = more permissions)
    role_hierarchy = {
        'anonymous': 0,
        'user': 1,
        'staff': 2,
        'admin': 3,
        'super_admin': 4
    }

    user_level = role_hierarchy.get(user_role, 0)
    required_level = role_hierarchy.get(required_role, 999)

    has_access = user_level >= required_level

    logger.debug(f"Access check: user_role={user_role}({user_level}), required={required_role}({required_level}), resource={resource}, access={has_access}")

    return has_access


def sanitize_input(input_data: str, max_length: int = 1000) -> str:
    """
    Sanitize user input to prevent injection attacks and XSS.

    Args:
        input_data: Raw input string
        max_length: Maximum allowed length

    Returns:
        Sanitized input string
    """
    if not input_data:
        return ""

    # Truncate to max length
    sanitized = input_data[:max_length]

    # Remove HTML tags completely (improved from character-by-character removal)
    sanitized = re.sub(r'<[^>]+>', '', sanitized)

    # Remove potentially dangerous characters that remain
    dangerous_chars = ['"', "'", ';', '--', '/*', '*/', '\\']
    for char in dangerous_chars:
        sanitized = sanitized.replace(char, "")

    # Remove JavaScript event handlers
    sanitized = re.sub(r'on\w+\s*=', '', sanitized, flags=re.IGNORECASE)

    # Remove script tags and content (extra protection)
    sanitized = re.sub(r'<script[^>]*>.*?</script>', '', sanitized, flags=re.IGNORECASE | re.DOTALL)

    return sanitized.strip()


def validate_data_integrity(data: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """
    Validate data integrity against a schema.

    Args:
        data: Data to validate
        schema: Schema definition

    Returns:
        List of validation errors
    """
    errors = []

    # Basic validation - can be extended
    required_fields = schema.get('required', [])
    for field in required_fields:
        if field not in data or data[field] is None or data[field] == "":
            errors.append(f"Required field '{field}' is missing or empty")

    return errors


class InputValidator:
    """Enhanced input validation with security focus."""

    def __init__(self):
        # Security patterns
        self.security_patterns = {
            'sql_injection': re.compile(
                r'\b(union|select|insert|update|delete|drop|exec|script)\b',
                re.IGNORECASE
            ),
            'xss_patterns': re.compile(
                r'<script|<iframe|<object|<embed|javascript:|on\w+\s*=|<\w+[^>]*>.*?</\w+>',
                re.IGNORECASE | re.DOTALL
            ),
            'path_traversal': re.compile(r'\.\.[\\/]|~[\\/]|\$\{|\%2e\%2e'),
            'command_injection': re.compile(
                r'[;&|`$\(\)<>]|\|\||&&|`[^`]*`',
                re.IGNORECASE
            )
        }

        # Validation rules
        self.validation_rules = {
            'max_string_length': 10000,
            'max_array_length': 1000,
            'max_object_depth': 10,
        }

    def validate_input(
        self,
        data: Any,
        context: dict[str, Any] | None = None,
        strict_mode: bool = True
    ) -> dict[str, Any]:
        """Comprehensive input validation with security checks."""
        validation_result = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'security_issues': [],
            'sanitized_data': data
        }

        try:
            # Basic type validation
            self._validate_basic_types(data, validation_result, strict_mode)

            # Security validation
            self._validate_security(data, validation_result)

            # Structure validation
            self._validate_structure(data, validation_result)

            # Sanitize if issues found
            if validation_result['security_issues'] or validation_result['warnings']:
                validation_result['sanitized_data'] = self._sanitize_data(data)

            # Set overall validity
            validation_result['valid'] = len(validation_result['errors']) == 0

            return validation_result

        except Exception as e:
            logger.error(f"Validation error: {str(e)}")
            return {
                'valid': False,
                'errors': [f"Validation error: {str(e)}"],
                'warnings': [],
                'security_issues': [],
                'sanitized_data': None
            }

    def _validate_basic_types(
        self,
        data: Any,
        result: dict[str, list[str]],
        strict_mode: bool
    ) -> None:
        """Validate basic data types and constraints."""
        if isinstance(data, str):
            if len(data) > self.validation_rules['max_string_length']:
                result['errors'].append(
                    f"String length {len(data)} exceeds maximum {self.validation_rules['max_string_length']}"
                )

        elif isinstance(data, list):
            if len(data) > self.validation_rules['max_array_length']:
                result['errors'].append(
                    f"Array length {len(data)} exceeds maximum {self.validation_rules['max_array_length']}"
                )

        elif isinstance(data, dict):
            self._validate_object_depth(data, result)

    def _validate_security(self, data: Any, result: dict[str, list[str]]) -> None:
        """Validate input for security threats."""
        if isinstance(data, str):
            # Check for SQL injection
            if self.security_patterns['sql_injection'].search(data):
                result['security_issues'].append("Potential SQL injection detected")

            # Check for XSS (improved pattern)
            if self.security_patterns['xss_patterns'].search(data):
                result['security_issues'].append("Potential XSS attack detected")

            # Check for path traversal
            if self.security_patterns['path_traversal'].search(data):
                result['security_issues'].append("Potential path traversal detected")

            # Check for command injection
            if self.security_patterns['command_injection'].search(data):
                result['security_issues'].append("Potential command injection detected")

        elif isinstance(data, dict):
            for key, value in data.items():
                self._validate_security(value, result)

        elif isinstance(data, list):
            for item in data:
                self._validate_security(item, result)

    def _validate_structure(self, data: Any, result: dict[str, list[str]]) -> None:
        """Validate data structure constraints."""
        if isinstance(data, dict):
            # Check for suspicious keys
            suspicious_keys = ['__proto__', 'constructor', 'prototype']
            for key in data.keys():
                if key in suspicious_keys:
                    result['security_issues'].append(f"Suspicious object key detected: {key}")

    def _validate_object_depth(self, obj: dict[str, Any], result: dict[str, list[str]], depth: int = 0) -> None:
        """Validate object nesting depth."""
        if depth > self.validation_rules['max_object_depth']:
            result['errors'].append(f"Object nesting depth {depth} exceeds maximum {self.validation_rules['max_object_depth']}")
            return

        for value in obj.values():
            if isinstance(value, dict):
                self._validate_object_depth(value, result, depth + 1)

    def _sanitize_data(self, data: Any) -> Any:
        """Sanitize data by removing or escaping dangerous content."""
        if isinstance(data, str):
            # Remove HTML tags completely
            sanitized = re.sub(r'<[^>]+>', '', data)

            # Remove potential XSS content
            sanitized = self.security_patterns['xss_patterns'].sub('', sanitized)

            # Remove potential SQL injection content
            sanitized = self.security_patterns['sql_injection'].sub('', sanitized)

            return sanitized.strip()

        elif isinstance(data, dict):
            return {
                key: self._sanitize_data(value)
                for key, value in data.items()
                if key not in ['__proto__', 'constructor', 'prototype']
            }

        elif isinstance(data, list):
            return [self._sanitize_data(item) for item in data]

        return data
