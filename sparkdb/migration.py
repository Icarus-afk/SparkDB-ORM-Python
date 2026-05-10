"""Schema migration helpers.

Provides auto-detection of missing columns via ``PRAGMA table_info``
and versioned migration tracking via a ``_schema_versions`` table.
"""

from sparkdb.exceptions import MigrationError


class Migrator:
    """Manages schema migrations for one or more models.

    Parameters
    ----------
    database : DatabaseBackend
        An active backend instance.
    """

    def __init__(self, database):
        self.database = database
        self._ensure_schema_table()

    def _ensure_schema_table(self):
        sql = """CREATE TABLE IF NOT EXISTS "_schema_versions" (
            "version" INTEGER PRIMARY KEY,
            "applied_at" TEXT DEFAULT (datetime('now'))
        )"""
        self.database.query(sql)

    def _get_current_version(self):
        result = self.database.query('SELECT COALESCE(MAX("version"), 0) FROM "_schema_versions"')
        rows = result.get("rows", [])
        if rows and rows[0]:
            return rows[0][0] if isinstance(rows[0], (list, tuple)) else 0
        return 0

    def _record_migration(self, version):
        self.database.query(
            'INSERT INTO "_schema_versions" ("version") VALUES (?)',
            params=[version]
        )

    def create_table(self, model_cls, if_not_exists=True):
        """Create the table for *model_cls* using ``Model.create_table()``."""
        model_cls.create_table(if_not_exists=if_not_exists)

    def auto_migrate(self, *model_classes):
        """Detect and add missing columns for each model class.

        Uses ``PRAGMA table_info`` to find columns that exist in the
        model definition but not in the actual table, then runs
        ``ALTER TABLE ADD COLUMN`` for each.
        """
        for model_cls in model_classes:
            existing = self.database.query(
                f"PRAGMA table_info(\"{model_cls._meta.table}\")",
                database=model_cls._meta.database_name
            )
            existing_cols = {row[1] for row in existing.get("rows", [])}

            for field in model_cls._field_list:
                if field.column not in existing_cols:
                    col_type = field.sql_type()
                    has_pk = "PRIMARY KEY" in col_type.upper()
                    if field.primary_key and not has_pk:
                        col_type += " PRIMARY KEY"
                    if not field.nullable and not has_pk:
                        col_type += " NOT NULL"
                    if field.unique and not has_pk:
                        col_type += " UNIQUE"
                    if field.default is not None:
                        if isinstance(field.default, str):
                            col_type += f" DEFAULT '{field.default.replace(chr(39), chr(39)+chr(39))}'"
                        else:
                            col_type += f" DEFAULT {field.default}"
                    sql = f"ALTER TABLE \"{model_cls._meta.table}\" ADD COLUMN \"{field.column}\" {col_type}"
                    self.database.query(sql, database=model_cls._meta.database_name)

    def migrate(self, version, operations):
        """Run a versioned migration if it has not already been applied.

        Parameters
        ----------
        version : int
            The migration version number.
        operations : list of str
            SQL statements to execute.
        """
        current = self._get_current_version()
        if version <= current:
            return

        for op in operations:
            self.database.query(op)

        self._record_migration(version)
