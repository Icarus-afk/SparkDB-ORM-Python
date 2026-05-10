"""SparkDB HTTP client.

Provides the ``SparkDB`` class that connects to a SparkDB server
over REST.  This module requires ``requests`` (installed via the
``[sparkdb]`` optional dependency).
"""

import json
import re
import time
from urllib.parse import urljoin

import requests

from sparkdb.exceptions import *


def _quote_sql(val):
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        return str(val)
    s = str(val)
    s = s.replace("'", "''")
    return f"'{s}'"


def _inline_params(sql, params):
    if params is None:
        return sql
    if not isinstance(params, (list, tuple)):
        params = [params]
    parts = sql.split("?")
    if len(parts) - 1 != len(params):
        if len(parts) - 1 != len(params):
            raise ValueError(f"expected {len(parts)-1} params, got {len(params)}")
    result = parts[0]
    for i, p in enumerate(params):
        result += _quote_sql(p) + parts[i + 1]
    return result


class SparkDB:
    """HTTP client for the SparkDB server.

    Parameters
    ----------
    url : str
        Server base URL (default ``http://localhost:9600``).
    api_key : str, optional
        API key for authentication.
    username : str, optional
        Username for JWT authentication (requires *password*).
    password : str, optional
        Password for JWT authentication (requires *username*).
    timeout : int
        Request timeout in seconds (default 30).
    """

    def __init__(self, url="http://localhost:9600", api_key=None, username=None, password=None, timeout=30):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

        if api_key:
            self._session.headers.update({"X-API-Key": api_key})
            self._token = None
        elif username and password:
            self._token = self._login(username, password)
            self._session.headers.update({"Authorization": f"Bearer {self._token}"})
        elif username or password:
            raise ValueError("both username and password are required for authentication")
        else:
            self._token = None

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
        return token

    def query(self, sql, params=None, database="main"):
        """Execute a query on the SparkDB server.

        Parameters are inlined into the SQL before sending (the
        SparkDB server does not currently support server-side
        parameter binding).
        """
        sql = _inline_params(sql, params)
        body = {"query": sql, "database": database}
        return self._request("POST", "/query", json=body)

    def transaction(self, queries, database="main"):
        """Execute multiple queries in a transaction."""
        body = {"queries": queries, "database": database}
        return self._request("POST", "/transaction", json=body)

    def create_database(self, name):
        """Create a new database on the server."""
        return self._request("POST", "/databases", json={"name": name})

    def list_databases(self):
        """List all databases on the server."""
        return self._request("GET", "/databases")

    def health(self):
        """Check server health."""
        return self._request("GET", "/health")

    def stats(self):
        """Get server statistics."""
        return self._request("GET", "/stats")

    def close(self):
        """Close the underlying HTTP session."""
        self._session.close()
