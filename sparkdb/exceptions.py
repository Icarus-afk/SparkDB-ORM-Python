"""ORM-specific exceptions."""


class SparkDBError(Exception):
    """Base exception for all ORM errors."""


class ValidationError(SparkDBError):
    """Raised when a field value fails validation."""


class AuthenticationError(SparkDBError):
    """Raised when authentication with the SparkDB server fails."""


class NotFoundError(SparkDBError):
    """Raised when a requested resource is not found."""


class ConnectionFailedError(SparkDBError):
    """Raised when the backend cannot connect."""


class MigrationError(SparkDBError):
    """Raised when a schema migration fails."""
