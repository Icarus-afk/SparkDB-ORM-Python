"""SparkDB ORM — a lightweight Python ORM for SQL and SparkDB.

Supports DB-API 2.0 connections (sqlite3, psycopg2, etc.) and
the SparkDB HTTP backend with zero required dependencies.
"""

from sparkdb.model import Model
from sparkdb.backends import DatabaseBackend, SparkDBBackend, DBAPI2Backend
from sparkdb import fields
from sparkdb.exceptions import *
from sparkdb.relationship import ForeignKey, has_many
from sparkdb.migration import Migrator
from sparkdb.query import QuerySet
from sparkdb import models as models

try:
    from sparkdb.client import SparkDB, AdminNamespace, validate_password_strength
except ImportError:
    SparkDB = None
    AdminNamespace = None
    validate_password_strength = None

__version__ = "0.3.0"
