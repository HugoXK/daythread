"""Domain errors with stable codes for CLI and HTTP mapping."""

from __future__ import annotations


class DaythreadError(Exception):
    code = "daythread_error"
    status = 500


class ValidationError(DaythreadError, ValueError):
    code = "validation_error"
    status = 400


class NotFoundError(DaythreadError, KeyError):
    code = "not_found"
    status = 404

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "not found"


class PolicyDeniedError(DaythreadError):
    code = "policy_denied"
    status = 403


class BudgetExceededError(DaythreadError):
    code = "budget_exceeded"
    status = 429


class ProviderError(DaythreadError):
    code = "provider_error"
    status = 502


class BackupError(DaythreadError):
    code = "backup_error"
    status = 400


class McpError(DaythreadError):
    code = "mcp_error"
    status = 502


class BenchError(DaythreadError):
    """Bench safety cases failed: a user-facing, fail-closed bench result."""

    code = "bench_error"
    status = 500
