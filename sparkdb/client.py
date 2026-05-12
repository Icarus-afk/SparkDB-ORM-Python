"""SparkDB HTTP client.

Provides the ``SparkDB`` class that connects to a SparkDB server
over REST.  This module requires ``requests`` (installed via the
``[sparkdb]`` optional dependency).
"""

import json
from contextlib import contextmanager
from urllib.parse import urljoin

import requests

from sparkdb.exceptions import *
from sparkdb import models as m


class AdminNamespace:
    """Namespace for admin-only operations on the SparkDB server.

    Accessed via ``client.admin``. All methods require admin or
    appropriate role-level permissions.
    """

    def __init__(self, client):
        self._client = client

    def create_user(self, username, password, role):
        """Create a new database user (admin only).

        Parameters
        ----------
        username : str
        password : str
            Must meet password strength requirements (8+ chars,
            uppercase, lowercase, digit).
        role : str
            One of ``admin``, ``developer``, ``readonly``, ``auditor``.

        Returns
        -------
        dict
            ``{"id": ..., "username": ..., "role": ...}``
        """
        return self._client._request("POST", "/admin/users", json={
            "username": username, "password": password, "role": role,
        })

    def list_users(self):
        """List all database users (admin only).

        Returns
        -------
        dict
            ``{"users": [...]}``
        """
        return self._client._request("GET", "/admin/users")

    def set_user_role(self, user_id, role):
        """Change a user's role (admin only).

        Parameters
        ----------
        user_id : int
        role : str
        """
        return self._client._request(
            "PUT", f"/admin/users/{user_id}/role", json={"role": role},
        )

    def set_username(self, user_id, username):
        """Change a user's username (admin only)."""
        return self._client._request(
            "PUT", f"/admin/users/{user_id}/username", json={"username": username},
        )

    def reset_password(self, user_id, password):
        """Admin-reset a user's password (admin only).

        Triggers ``password_change_required`` on the user's next login.
        """
        return self._client._request(
            "PUT", f"/admin/users/{user_id}/password", json={"password": password},
        )

    def delete_user(self, user_id):
        """Delete a user (admin only). Cannot delete yourself.

        Returns
        -------
        dict
            ``{"message": "user deleted"}``
        """
        return self._client._request("DELETE", f"/admin/users/{user_id}")

    def audit_logs(self, limit=100):
        """View audit logs (admin/auditor).

        Parameters
        ----------
        limit : int
            Maximum number of log entries to return.

        Returns
        -------
        dict
            ``{"logs": [...]}``
        """
        return self._client._request(
            "GET", "/admin/audit-logs", params={"limit": limit},
        )


class SparkDB:
    """HTTP client for the SparkDB server.

    Parameters
    ----------
    url : str
        Server base URL (default ``http://localhost:9600``).
    api_key : str, optional
        API key for authentication (sets ``X-API-Key`` header).
    username : str, optional
        Username for JWT authentication (requires *password*).
    password : str, optional
        Password for JWT authentication (requires *username*).
    session_token : str, optional
        Session token for session-based authentication (sets
        ``Authorization: Session <token>`` header).
    timeout : int
        Request timeout in seconds (default 30).
    """

    def __init__(self, url="http://localhost:9600", api_key=None,
                 username=None, password=None, session_token=None,
                 timeout=30):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._password_change_required = False
        self._token = None
        self.admin = AdminNamespace(self)

        if api_key:
            self._session.headers.update({"X-API-Key": api_key})
        elif username and password:
            self._token = self._login(username, password)
            self._session.headers.update({"Authorization": f"Bearer {self._token}"})
        elif session_token:
            self._session.headers.update({"Authorization": f"Session {session_token}"})
        elif username or password:
            raise ValueError("both username and password are required for authentication")

    @property
    def needs_password_change(self):
        """Whether the server requires a password change on next login."""
        return self._password_change_required

    def _request(self, method, path, **kwargs):
        url = urljoin(self.url + "/", path.lstrip("/"))
        kwargs.setdefault("timeout", self.timeout)
        try:
            resp = self._session.request(method, url, **kwargs)
        except requests.ConnectionError as e:
            raise ConnectionFailedError(f"cannot connect to {url}: {e}") from e
        except requests.Timeout as e:
            raise ConnectionFailedError(f"request timed out: {e}") from e
        except requests.RequestException as e:
            raise ConnectionFailedError(f"request failed: {e}") from e

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise SparkDBError(f"invalid JSON response ({resp.status_code}): {e}") from e

        if resp.status_code >= 400:
            msg = data.get("error", data.get("message", resp.reason))
            if resp.status_code == 401:
                raise AuthenticationError(msg)
            if resp.status_code == 404:
                raise NotFoundError(msg)
            raise SparkDBError(f"[{resp.status_code}] {msg}")

        return data

    def _login(self, username, password):
        """Authenticate and return a JWT token."""
        data = self._request("POST", "/auth/login", json={"username": username, "password": password})
        token = data.get("token")
        if not token:
            raise AuthenticationError("login response missing token")
        self._password_change_required = data.get("password_change_required", False)
        return token

    def change_password(self, old_password, new_password):
        """Change the authenticated user's password.

        Parameters
        ----------
        old_password : str
        new_password : str
            Must meet password strength requirements (8+ chars,
            uppercase, lowercase, digit).

        Returns
        -------
        dict
            ``{"message": "password changed"}``
        """
        result = self._request("PUT", "/auth/password", json={
            "old_password": old_password, "new_password": new_password,
        })
        self._password_change_required = False
        return result

    def query(self, sql, params=None, database="main"):
        """Execute a query on the SparkDB server.

        Uses server-side parameter binding via the ``params`` array.

        Parameters
        ----------
        sql : str
            SQL string with ``?`` placeholders.
        params : list, optional
            Values to bind to placeholders.
        database : str
            Target database name (default ``"main"``).

        Returns
        -------
        dict
            ``{"columns": [...], "rows": [[...], ...], "time": "..."}``
        """
        body = {"query": sql, "database": database}
        if params is not None:
            body["params"] = params
        return self._request("POST", "/query", json=body)

    def _transaction(self, queries, database="main"):
        """Execute multiple queries in a transaction (direct API call)."""
        body = {"queries": queries, "database": database}
        return self._request("POST", "/transaction", json=body)

    def create_api_key(self, name):
        """Create a new API key (admin only).

        Parameters
        ----------
        name : str
            A label for the key.

        Returns
        -------
        dict
            ``{"api_key": "vl_...", "name": "..."}``
            The full key is only shown once.
        """
        return self._request("POST", "/auth/api-keys", json={"name": name})

    def list_api_keys(self):
        """List all API keys (admin only).

        Returns
        -------
        dict
            ``{"api_keys": [...]}``
        """
        return self._request("GET", "/auth/api-keys")

    def delete_api_key(self, key_id):
        """Delete an API key (admin only).

        Parameters
        ----------
        key_id : int
        """
        return self._request("DELETE", f"/auth/api-keys/{key_id}")

    def reveal_api_key(self, key_id, password):
        """Re-display a full API key (requires account password).

        Parameters
        ----------
        key_id : int
        password : str
            The authenticated user's password.

        Returns
        -------
        dict
            ``{"api_key": "vl_..."}``
        """
        return self._request("POST", f"/auth/api-keys/{key_id}/reveal", json={
            "password": password,
        })

    def create_database(self, name):
        """Create a new database on the server.

        .. deprecated::
            Use POST /databases directly. The server supports database
            creation implicitly through query execution.
        """
        return self._request("POST", "/databases", json={"name": name})

    def list_databases(self):
        """List all databases on the server.

        Returns
        -------
        dict
            ``{"databases": ["main", ...]}``
        """
        return self._request("GET", "/databases")

    def backup(self, database="main"):
        """Create a backup of a database (admin only).

        Parameters
        ----------
        database : str
            Database name to back up (default ``"main"``).

        Returns
        -------
        dict
            Backup info with name, size, database, and created_at.
        """
        return self._request("POST", "/backup", json={"database": database})

    def list_backups(self):
        """List all available backups (admin only).

        Returns
        -------
        dict
            ``{"backups": [...]}``
        """
        return self._request("GET", "/backups")

    def delete_backup(self, name):
        """Delete a specific backup by name (admin only).

        Parameters
        ----------
        name : str
            Backup name as returned by :meth:`list_backups`.
        """
        return self._request("DELETE", f"/backups/{name}")

    def restore(self, backup_file, database="main"):
        """Restore a database from a backup file (admin only).

        Parameters
        ----------
        backup_file : str
        database : str
            Target database name (default ``"main"``).

        Returns
        -------
        dict
            ``{"message": "restore completed", "database": "..."}``
        """
        return self._request("POST", "/restore", json={
            "backup_file": backup_file, "database": database,
        })

    def health(self):
        """Check server health.

        Returns
        -------
        dict
            ``{"status": "ok", "checks": {"database": "ok"}}``
        """
        return self._request("GET", "/health")

    def stats(self):
        """Get server statistics (admin/auditor).

        Returns
        -------
        dict
            Uptime, total queries, failed logins, active connections,
            avg/P99 latency, goroutines, memory, per-database sizes.
        """
        return self._request("GET", "/stats")

    @contextmanager
    def transaction(self, database="main"):
        """Context manager that collects queries and sends them as a transaction.

        Queries are buffered and sent atomically to
        ``POST /transaction`` on successful exit.  If the body raises
        the collected queries are discarded.

        Yields
        ------
        callable
            A function ``add(sql)`` that appends a SQL string to the
            pending batch.

        Examples
        --------
        >>> with db.transaction() as add:
        ...     add("INSERT INTO t (v) VALUES (1)")
        ...     add("INSERT INTO t (v) VALUES (2)")
        """
        queries = []

        def add(sql):
            queries.append(sql)

        yield add
        self._transaction(queries, database=database)

    def health_model(self):
        """Return server health as a typed :class:`m.HealthStatus`."""
        return m.HealthStatus(**self.health())

    def stats_model(self):
        """Return server statistics as a typed :class:`m.ServerStats`."""
        raw = self.stats()
        dbs = [m.DatabaseInfo(**db) for db in raw.get("databases", [])]
        return m.ServerStats(**{k: v for k, v in raw.items() if k != "databases"}, databases=dbs)

    def close(self):
        """Close the underlying HTTP session."""
        self._session.close()
