"""Typed response models for the SparkDB HTTP API.

These dataclasses wrap the raw JSON responses from the SparkDB server
into structured objects with typed fields.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class QueryResult:
    """Result of a single SQL query."""
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    error: str = ""
    time: str = ""


@dataclass
class LoginResponse:
    """Response from POST /auth/login."""
    token: str
    token_type: str = "bearer"
    expires_in: int = 86400
    password_change_required: bool = False
    user: Optional[dict] = None


@dataclass
class HealthStatus:
    """Response from GET /health."""
    status: str = "ok"
    checks: dict[str, str] = field(default_factory=dict)


@dataclass
class DatabaseInfo:
    """Per-database statistics."""
    name: str = ""
    size: int = 0


@dataclass
class ServerStats:
    """Response from GET /stats."""
    uptime_seconds: float = 0.0
    total_queries: int = 0
    failed_logins: int = 0
    active_connections: int = 0
    avg_query_latency_ms: float = 0.0
    p99_query_latency_ms: float = 0.0
    goroutines: int = 0
    alloc_mb: float = 0.0
    sys_mb: float = 0.0
    databases: list[DatabaseInfo] = field(default_factory=list)


@dataclass
class UserView:
    """A user as returned by GET /admin/users."""
    id: int = 0
    username: str = ""
    role: str = ""
    created_at: Optional[str] = None
    locked_until: Optional[str] = None


@dataclass
class APIKeyView:
    """An API key as returned by GET /auth/api-keys."""
    id: int = 0
    user_id: int = 0
    name: str = ""
    prefix: str = ""
    created_at: Optional[str] = None
    expires_at: Optional[str] = None


@dataclass
class BackupInfo:
    """A backup entry as returned by GET /backups."""
    name: str = ""
    database: str = ""
    size: int = 0
    path: str = ""
    created_at: Optional[str] = None


@dataclass
class AuditLogEntry:
    """An audit log entry as returned by GET /admin/audit-logs."""
    id: int = 0
    user_id: Optional[int] = None
    username: str = ""
    ip_address: str = ""
    query: str = ""
    endpoint: str = ""
    status: str = ""
    timestamp: Optional[str] = None
