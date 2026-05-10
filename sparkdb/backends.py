"""Database backend abstractions for the SparkDB ORM.

Provides a protocol (``DatabaseBackend``) that all backends implement,
plus built-in adapters for DB-API 2.0 connections and the SparkDB HTTP
client.
"""

from sparkdb.exceptions import SparkDBError


class DatabaseBackend:
    """Protocol for database backends.

    Every backend must implement ``query()`` and may optionally
    implement ``close()``.  The return value of ``query()`` is a
    dict with ``"columns"`` (list of column name strings) and
    ``"rows"`` (list of tuples/lists of cell values).

    This is the same format produced by the SparkDB HTTP API, so
    switching between backends is transparent to the ORM.
    """

    def query(self, sql, params=None, database="main"):
        """Execute SQL and return results.

        Parameters
        ----------
        sql : str
            SQL string with ``?`` placeholders.
        params : list or None
            Values to bind to placeholders.
        database : str
            Database name (ignored by single-file backends like
            sqlite3; used by SparkDB and multi-DB drivers).

        Returns
        -------
        dict
            ``{"columns": [str, ...], "rows": [[...], ...]}``
        """
        raise NotImplementedError

    def close(self):
        """Release any backend resources."""


class SparkDBBackend(DatabaseBackend):
    """Adapter that delegates ``query()`` to a SparkDB HTTP client.

    Parameters
    ----------
    client : SparkDB
        An authenticated ``sparkdb.client.SparkDB`` instance.
    """

    def __init__(self, client):
        self._client = client

    def query(self, sql, params=None, database="main"):
        return self._client.query(sql, params=params, database=database)

    def close(self):
        self._client.close()


class DBAPI2Backend(DatabaseBackend):
    """Adapter wrapping a PEP 249 (DB-API 2.0) connection.

    Works with any compliant driver: ``sqlite3``, ``psycopg2``,
    ``mysql-connector-python``, etc.

    Parameters
    ----------
    connection
        A PEP 249 connection object.
    """

    def __init__(self, connection):
        self._conn = connection

    def query(self, sql, params=None, database=None):
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, params or [])
            if cursor.description:
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
            else:
                columns = []
                rows = []
                if cursor.lastrowid is not None or cursor.rowcount not in (-1, 0):
                    rows = [[cursor.lastrowid, cursor.rowcount]]
                    columns = ["last_insert_id", "rows_affected"]
            cursor.close()
            return {"columns": columns, "rows": rows}
        except Exception as e:
            cursor.close()
            raise SparkDBError(f"query failed: {e}") from e

    def close(self):
        self._conn.close()
