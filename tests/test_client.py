#!/usr/bin/env python3
"""Comprehensive tests for the SparkDB HTTP client."""

import json
import os
import sys
import unittest
from unittest.mock import ANY, MagicMock, Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sparkdb.client import (
    SparkDB, AdminNamespace, validate_password_strength,
)
from sparkdb.exceptions import (
    AuthenticationError, ConnectionFailedError, NotFoundError, SparkDBError,
)
from sparkdb import models as m


def _mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    resp.reason = "OK" if status_code < 400 else "Error"
    return resp


class TestValidatePasswordStrength(unittest.TestCase):

    def test_empty(self):
        self.assertEqual(validate_password_strength(""), "password is required")
        self.assertEqual(validate_password_strength(None), "password is required")

    def test_too_short(self):
        self.assertEqual(validate_password_strength("Ab1"), "password must be at least 8 characters")

    def test_no_upper(self):
        self.assertEqual(validate_password_strength("abcdefgh1"), "password must contain at least one uppercase letter")

    def test_no_lower(self):
        self.assertEqual(validate_password_strength("ABCDEFGH1"), "password must contain at least one lowercase letter")

    def test_no_digit(self):
        self.assertEqual(validate_password_strength("Abcdefgh"), "password must contain at least one digit")

    def test_valid(self):
        self.assertIsNone(validate_password_strength("ValidPass1"))
        self.assertIsNone(validate_password_strength("Str0ng!@#Pass"))


class TestSparkDBInit(unittest.TestCase):

    @patch("requests.Session")
    def test_no_auth(self, mock_session):
        db = SparkDB(url="http://localhost:9600")
        self.assertEqual(db.url, "http://localhost:9600")
        self.assertIsNone(db._token)
        self.assertFalse(db.needs_password_change)
        mock_session.return_value.headers.update.assert_called_once()

    @patch("requests.Session")
    def test_api_key_auth(self, mock_session):
        session = mock_session.return_value
        db = SparkDB(api_key="vl_test123")
        session.headers.update.assert_any_call({"Content-Type": "application/json"})
        session.headers.update.assert_any_call({"X-API-Key": "vl_test123"})
        self.assertIsNone(db._token)

    @patch("requests.Session")
    def test_username_password_auth(self, mock_session):
        session = mock_session.return_value
        resp = _mock_response({"token": "jwt_token", "token_type": "bearer", "password_change_required": True})
        session.request.return_value = resp
        db = SparkDB(username="admin", password="admin")
        self.assertEqual(db._token, "jwt_token")
        self.assertTrue(db.needs_password_change)

    @patch("requests.Session")
    def test_session_token_auth(self, mock_session):
        session = mock_session.return_value
        db = SparkDB(session_token="sess_abc123")
        session.headers.update.assert_any_call({"Authorization": "Session sess_abc123"})
        self.assertIsNone(db._token)

    @patch("requests.Session")
    def test_username_without_password_raises(self, mock_session):
        with self.assertRaises(ValueError):
            SparkDB(username="admin")

    @patch("requests.Session")
    def test_password_without_username_raises(self, mock_session):
        with self.assertRaises(ValueError):
            SparkDB(password="admin")

    @patch("requests.Session")
    def test_login_failure_no_token(self, mock_session):
        session = mock_session.return_value
        resp = _mock_response({"message": "invalid credentials"}, status_code=401)
        session.request.return_value = resp
        with self.assertRaises(AuthenticationError):
            SparkDB(username="admin", password="wrong")


class TestSparkDBRequest(unittest.TestCase):

    def setUp(self):
        self.session_patcher = patch("requests.Session")
        self.mock_session_class = self.session_patcher.start()
        self.mock_session = self.mock_session_class.return_value
        # API-key based init — no login call needed
        self.db = SparkDB(api_key="test_key")

    def tearDown(self):
        self.session_patcher.stop()

    def test_request_success(self):
        self.mock_session.request.return_value = _mock_response({"status": "ok"})
        result = self.db._request("GET", "/health")
        self.assertEqual(result, {"status": "ok"})

    def test_request_timeout(self):
        import requests as req
        self.mock_session.request.side_effect = req.exceptions.Timeout("timed out")
        with self.assertRaises(ConnectionFailedError):
            self.db._request("GET", "/health")

    def test_request_connection_error(self):
        import requests as req
        self.mock_session.request.side_effect = req.ConnectionError("refused")
        with self.assertRaises(ConnectionFailedError):
            self.db._request("GET", "/health")

    def test_request_bad_json(self):
        resp = MagicMock()
        resp.json.side_effect = json.JSONDecodeError("bad json", "", 0)
        resp.status_code = 200
        self.mock_session.request.return_value = resp
        with self.assertRaises(SparkDBError):
            self.db._request("GET", "/health")

    def test_request_401(self):
        self.mock_session.request.return_value = _mock_response(
            {"error": "unauthorized"}, status_code=401,
        )
        with self.assertRaises(AuthenticationError):
            self.db._request("GET", "/admin/users")

    def test_request_404(self):
        self.mock_session.request.return_value = _mock_response(
            {"error": "not found"}, status_code=404,
        )
        with self.assertRaises(NotFoundError):
            self.db._request("GET", "/nonexistent")

    def test_request_500(self):
        self.mock_session.request.return_value = _mock_response(
            {"error": "internal error"}, status_code=500,
        )
        with self.assertRaises(SparkDBError):
            self.db._request("GET", "/error")


class TestSparkDBAuthMethods(unittest.TestCase):

    def setUp(self):
        self.session_patcher = patch("requests.Session")
        self.mock_session_class = self.session_patcher.start()
        self.mock_session = self.mock_session_class.return_value
        self.login_resp = _mock_response({
            "token": "jwt_token", "token_type": "bearer",
            "expires_in": 86400, "password_change_required": True,
        })
        self.mock_session.request.return_value = self.login_resp
        self.db = SparkDB(username="admin", password="admin")

    def tearDown(self):
        self.session_patcher.stop()

    def test_login_sets_password_change_required(self):
        self.assertTrue(self.db.needs_password_change)

    def test_change_password(self):
        self.mock_session.request.return_value = _mock_response({"message": "password changed"})
        result = self.db.change_password("oldPass1", "NewStr0ng1")
        self.assertEqual(result, {"message": "password changed"})
        self.assertFalse(self.db.needs_password_change)
        self.mock_session.request.assert_called_with(
            "PUT", ANY, json={"old_password": "oldPass1", "new_password": "NewStr0ng1"},
            timeout=30,
        )

    def test_query_without_params(self):
        self.mock_session.request.return_value = _mock_response({
            "columns": ["id"], "rows": [[1]], "time": "1ms",
        })
        result = self.db.query("SELECT 1")
        self.assertEqual(result["columns"], ["id"])

    def test_query_with_params(self):
        self.mock_session.request.return_value = _mock_response({
            "columns": ["id", "name"], "rows": [[1, "Alice"]], "time": "1ms",
        })
        result = self.db.query("SELECT * FROM t WHERE id = ?", params=[1])
        self.assertEqual(result["rows"], [[1, "Alice"]])
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["json"]["params"], [1])

    def test_query_with_database(self):
        self.mock_session.request.return_value = _mock_response({
            "columns": [], "rows": [], "time": "0ms",
        })
        self.db.query("SELECT 1", database="mydb")
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["json"]["database"], "mydb")

    def test_transaction_context_manager(self):
        self.mock_session.request.return_value = _mock_response({"results": []})
        with self.db.transaction() as add:
            add("INSERT INTO t (v) VALUES (1)")
            add("INSERT INTO t (v) VALUES (2)")
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["json"]["queries"], [
            "INSERT INTO t (v) VALUES (1)",
            "INSERT INTO t (v) VALUES (2)",
        ])

    def test_transaction_context_manager_rollback_on_error(self):
        self.mock_session.request.return_value = _mock_response({"results": []})
        try:
            with self.db.transaction() as add:
                add("INSERT INTO t (v) VALUES (1)")
                raise ValueError("boom")
        except ValueError:
            pass
        # The _transaction endpoint should NOT have been called
        calls = [c for c in self.mock_session.request.call_args_list if c[0][1] == "/transaction"]
        self.assertEqual(len(calls), 0)

    def test_transaction_direct(self):
        self.mock_session.request.return_value = _mock_response({"results": []})
        result = self.db._transaction(["SELECT 1"])
        self.assertEqual(result, {"results": []})

    def test_create_api_key(self):
        self.mock_session.request.return_value = _mock_response({"api_key": "vl_newkey", "name": "my-key"})
        result = self.db.create_api_key("my-key")
        self.assertEqual(result["api_key"], "vl_newkey")
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["json"], {"name": "my-key"})

    def test_list_api_keys(self):
        self.mock_session.request.return_value = _mock_response({"api_keys": []})
        result = self.db.list_api_keys()
        self.assertEqual(result, {"api_keys": []})

    def test_delete_api_key(self):
        self.mock_session.request.return_value = _mock_response({"message": "API key deleted"})
        result = self.db.delete_api_key(1)
        self.assertEqual(result, {"message": "API key deleted"})
        self.mock_session.request.assert_called_with(
            "DELETE", ANY, timeout=30,
        )

    def test_reveal_api_key(self):
        self.mock_session.request.return_value = _mock_response({"api_key": "vl_revealed"})
        result = self.db.reveal_api_key(1, "mypass")
        self.assertEqual(result["api_key"], "vl_revealed")
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["json"], {"password": "mypass"})


class TestSparkDBAdmin(unittest.TestCase):

    def setUp(self):
        self.session_patcher = patch("requests.Session")
        self.mock_session_class = self.session_patcher.start()
        self.mock_session = self.mock_session_class.return_value
        self.mock_session.request.return_value = _mock_response({"message": "ok"})
        self.db = SparkDB(api_key="test_key")
        self.admin = self.db.admin

    def tearDown(self):
        self.session_patcher.stop()

    def test_admin_is_admin_namespace(self):
        self.assertIsInstance(self.admin, AdminNamespace)

    def test_create_user(self):
        self.admin.create_user("dev1", "Str0ng1", "developer")
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["json"], {"username": "dev1", "password": "Str0ng1", "role": "developer"})

    def test_list_users(self):
        self.mock_session.request.return_value = _mock_response({"users": [{"id": 1, "username": "admin", "role": "admin"}]})
        result = self.admin.list_users()
        self.assertEqual(len(result["users"]), 1)

    def test_set_user_role(self):
        self.admin.set_user_role(2, "readonly")
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["json"], {"role": "readonly"})

    def test_set_username(self):
        self.admin.set_username(2, "new-name")
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["json"], {"username": "new-name"})

    def test_reset_password(self):
        self.admin.reset_password(2, "NewStr0ng2")
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["json"], {"password": "NewStr0ng2"})

    def test_delete_user(self):
        self.admin.delete_user(3)
        self.mock_session.request.assert_called_with("DELETE", ANY, timeout=30)

    def test_audit_logs(self):
        self.admin.audit_logs(limit=50)
        _, kwargs = self.mock_session.request.call_args
        self.assertIn("limit", kwargs.get("params", {}))


class TestSparkDBBackupRestore(unittest.TestCase):

    def setUp(self):
        self.session_patcher = patch("requests.Session")
        self.mock_session_class = self.session_patcher.start()
        self.mock_session = self.mock_session_class.return_value
        self.db = SparkDB(api_key="test_key")

    def tearDown(self):
        self.session_patcher.stop()

    def test_backup(self):
        self.mock_session.request.return_value = _mock_response({"name": "main_backup", "size": 100})
        result = self.db.backup("main")
        self.assertEqual(result["name"], "main_backup")
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["json"], {"database": "main"})

    def test_backup_default_db(self):
        self.mock_session.request.return_value = _mock_response({"name": "backup", "size": 0})
        self.db.backup()
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["json"], {"database": "main"})

    def test_list_backups(self):
        self.mock_session.request.return_value = _mock_response({"backups": []})
        result = self.db.list_backups()
        self.assertEqual(result, {"backups": []})

    def test_delete_backup(self):
        self.mock_session.request.return_value = _mock_response({"message": "backup deleted"})
        result = self.db.delete_backup("main_backup_123")
        self.assertEqual(result["message"], "backup deleted")

    def test_restore(self):
        self.mock_session.request.return_value = _mock_response({"message": "restore completed", "database": "main"})
        result = self.db.restore("backup_file.db", "main")
        self.assertEqual(result["message"], "restore completed")
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["json"], {"backup_file": "backup_file.db", "database": "main"})

    def test_restore_default_db(self):
        self.mock_session.request.return_value = _mock_response({"message": "ok", "database": "main"})
        self.db.restore("backup.db")
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["json"]["database"], "main")


class TestSparkDBInfoMethods(unittest.TestCase):

    def setUp(self):
        self.session_patcher = patch("requests.Session")
        self.mock_session_class = self.session_patcher.start()
        self.mock_session = self.mock_session_class.return_value
        self.db = SparkDB(api_key="test_key")

    def tearDown(self):
        self.session_patcher.stop()

    def test_health(self):
        self.mock_session.request.return_value = _mock_response({"status": "ok", "checks": {"database": "ok"}})
        result = self.db.health()
        self.assertEqual(result["status"], "ok")

    def test_health_model(self):
        self.mock_session.request.return_value = _mock_response({"status": "ok", "checks": {"database": "ok"}})
        result = self.db.health_model()
        self.assertIsInstance(result, m.HealthStatus)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.checks, {"database": "ok"})

    def test_stats(self):
        self.mock_session.request.return_value = _mock_response({
            "uptime_seconds": 3600, "total_queries": 100, "databases": [{"name": "main", "size": 4096}],
        })
        result = self.db.stats()
        self.assertEqual(result["total_queries"], 100)

    def test_stats_model(self):
        self.mock_session.request.return_value = _mock_response({
            "uptime_seconds": 3600, "total_queries": 100, "failed_logins": 0,
            "active_connections": 1, "avg_latency_ms": 1.5, "p99_latency_ms": 5.0,
            "goroutines": 5, "alloc_mb": 10.0,
            "databases": [{"name": "main", "size": 4096}],
        })
        result = self.db.stats_model()
        self.assertIsInstance(result, m.ServerStats)
        self.assertEqual(result.total_queries, 100)
        self.assertEqual(len(result.databases), 1)
        self.assertEqual(result.databases[0].name, "main")

    def test_replication_log(self):
        self.mock_session.request.return_value = _mock_response({"entries": []})
        result = self.db.replication_log(since=5, limit=100)
        self.assertEqual(result, {"entries": []})
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["params"], {"since": "5", "limit": "100"})

    def test_replication_log_defaults(self):
        self.mock_session.request.return_value = _mock_response({"entries": []})
        self.db.replication_log()
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["params"], {"since": "0", "limit": "500"})

    def test_list_databases(self):
        self.mock_session.request.return_value = _mock_response({"databases": ["main", "test"]})
        result = self.db.list_databases()
        self.assertEqual(result["databases"], ["main", "test"])

    def test_create_database(self):
        self.mock_session.request.return_value = _mock_response({"message": "created"})
        result = self.db.create_database("mydb")
        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["json"], {"name": "mydb"})

    def test_close(self):
        self.db.close()
        self.mock_session.close.assert_called_once()


class TestSparkDBErrors(unittest.TestCase):

    @patch("requests.Session")
    def test_request_exception_wrapped(self, mock_session_class):
        mock_session = mock_session_class.return_value
        import requests as req
        mock_session.request.side_effect = req.RequestException("network error")
        db = SparkDB(api_key="key")
        with self.assertRaises(ConnectionFailedError):
            db._request("GET", "/health")


if __name__ == "__main__":
    unittest.main(verbosity=2)
