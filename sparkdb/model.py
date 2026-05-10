"""Model base class and metaclass.

Provides the declarative ``Model`` base that users subclass to
define their data models.  The ``ModelMeta`` metaclass collects
field declarations, sets up primary keys, and stores metadata.
"""

import json
from datetime import datetime, timezone

from sparkdb.exceptions import *
from sparkdb.fields import Field, Integer, DateTime
from sparkdb.query import QuerySet


class ModelMeta(type):
    """Metaclass that collects ``Field`` descriptors and sets up ``_meta``."""

    def __new__(mcs, name, bases, namespace):
        if name == "Model":
            return super().__new__(mcs, name, bases, namespace)

        fields = {}
        pk_field = None

        for base in bases:
            if hasattr(base, "_fields"):
                fields.update(base._fields)

        for attr_name, attr_val in namespace.items():
            if isinstance(attr_val, Field):
                attr_val.contribute_to_class(attr_name)
                fields[attr_name] = attr_val

        auto_pk = not any(
            f.primary_key or getattr(f, "auto_increment", False)
            for f in fields.values()
        )
        if auto_pk:
            pk = Integer(primary_key=True, auto_increment=True, column="id")
            pk.contribute_to_class("id")
            fields["id"] = pk

        for f in fields.values():
            if f.primary_key or getattr(f, "auto_increment", False):
                pk_field = f
                break

        if pk_field is None:
            for f in fields.values():
                if f.primary_key:
                    pk_field = f
                    break

        meta = namespace.get("Meta", None)
        table_name = getattr(meta, "table", name.lower() + "s")
        database_name = getattr(meta, "database_name", "main")
        database = getattr(meta, "database", None)
        timestamps = getattr(meta, "timestamps", False)
        ordering = getattr(meta, "ordering", [])
        indexes = getattr(meta, "indexes", [])
        unique_together = getattr(meta, "unique_together", [])

        if indexes:
            for idx in indexes:
                if not isinstance(idx, dict) or "fields" not in idx:
                    raise ValueError(
                        f"each entry in Meta.indexes must be a dict with a 'fields' key"
                    )

        namespace["_fields"] = fields
        namespace["_pk_field"] = pk_field
        namespace["_meta"] = _Meta(
            table_name, database, database_name, timestamps,
            pk_field, ordering, indexes, unique_together
        )
        namespace["_field_list"] = sorted(fields.values(), key=lambda f: f._order)
        namespace["_rel_descriptors"] = {}

        cls = super().__new__(mcs, name, bases, namespace)
        return cls


class _Meta:
    """Stores model metadata collected by ``ModelMeta``."""

    def __init__(self, table, database, database_name, timestamps, pk_field,
                 ordering=None, indexes=None, unique_together=None):
        self.table = table
        self.database = database
        self.database_name = database_name
        self.timestamps = timestamps
        self.pk_name = pk_field._name if pk_field else "id"
        self.ordering = ordering or []
        self.indexes = indexes or []
        self.unique_together = unique_together or []


class Model(metaclass=ModelMeta):
    """Base class for all data models.

    Subclass and declare ``Field`` descriptors as class attributes.
    Configuration goes in an inner ``Meta`` class.

    Example
    -------
    >>> class User(Model):
    ...     name = String(max_length=100)
    ...     age = Integer()
    ...     class Meta:
    ...         database = db
    ...         timestamps = True
    """

    def __init__(self, **kwargs):
        self._data = {}
        self._saved = False
        self._exists = False
        for name in self._fields:
            field = self._fields[name]
            default = field.default
            self._data[name] = default() if callable(default) else default
        for name, val in kwargs.items():
            if name not in self._fields:
                raise ValidationError(f"unknown field: {name}")
            setattr(self, name, val)

    def __getattr__(self, name):
        cls = type(self)
        if name in cls._rel_descriptors:
            return cls._rel_descriptors[name].__get__(self, cls)
        raise AttributeError(name)

    @property
    def pk(self):
        """The value of this instance's primary key."""
        return self._data.get(self._meta.pk_name)

    def to_dict(self, include=None, exclude=None):
        """Serialize fields to a dict.

        Parameters
        ----------
        include : list of str, optional
            Only include these field names.
        exclude : list of str, optional
            Exclude these field names.
        """
        result = {}
        def should_include(name):
            if include and name not in include:
                return False
            if exclude and name in exclude:
                return False
            return True
        for name in self._fields:
            if not should_include(name):
                continue
            val = self._data.get(name)
            field = self._fields[name]
            if val is not None:
                result[name] = field.to_db(val)
            else:
                result[name] = val
        if self._meta.timestamps:
            for attr in ("created_at", "updated_at"):
                if attr in self._data and should_include(attr):
                    result[attr] = self._data[attr]
        return result

    def to_json(self, **kwargs):
        """Serialize to a JSON string.

        Parameters passed to ``json.dumps()``.
        """
        return json.dumps(self.to_dict(**kwargs), default=str, indent=2)

    @classmethod
    def _from_row(cls, values):
        if not values:
            return None
        inst = cls.__new__(cls)
        inst._data = {}
        inst._saved = True
        inst._exists = True
        name_to_col = {f.column: name for name, f in cls._fields.items()}
        for key, val in values.items():
            field_name = name_to_col.get(key, key)
            if field_name in cls._fields:
                field = cls._fields[field_name]
                inst._data[field_name] = field.from_db(val)
            else:
                inst._data[key] = val
        for name in cls._fields:
            if name not in inst._data:
                field = cls._fields[name]
                default = field.default
                inst._data[name] = default() if callable(default) else default
        return inst

    @classmethod
    def create_table(cls, if_not_exists=True):
        """Create the database table for this model.

        Parameters
        ----------
        if_not_exists : bool
            Add ``IF NOT EXISTS`` to the SQL.
        """
        if not cls._meta.database:
            raise SparkDBError("no database configured")
        parts = []
        for f in cls._field_list:
            col = f.column
            sql_type = f.sql_type()
            if f.primary_key and "AUTOINCREMENT" not in sql_type.upper():
                if "PRIMARY KEY" not in sql_type.upper():
                    sql_type += " PRIMARY KEY"
            if not f.nullable and "PRIMARY KEY" not in sql_type.upper():
                sql_type += " NOT NULL"
            if f.unique and "PRIMARY KEY" not in sql_type.upper():
                sql_type += " UNIQUE"
            if f.default is not None:
                if isinstance(f.default, str):
                    sql_type += f" DEFAULT '{f.default.replace(chr(39), chr(39)+chr(39))}'"
                elif isinstance(f.default, bool):
                    sql_type += f" DEFAULT {1 if f.default else 0}"
                else:
                    sql_type += f" DEFAULT {f.default}"
            parts.append(f"\"{col}\" {sql_type}")
        if cls._meta.timestamps:
            parts.append("\"created_at\" TEXT DEFAULT (datetime('now'))")
            parts.append("\"updated_at\" TEXT DEFAULT (datetime('now'))")
        for ut in cls._meta.unique_together:
            cols = ", ".join(f"\"{c}\"" for c in ut)
            parts.append(f"UNIQUE({cols})")
        ie = "IF NOT EXISTS " if if_not_exists else ""
        sql = f"CREATE TABLE {ie}\"{cls._meta.table}\" ({', '.join(parts)})"
        cls._meta.database.query(sql, database=cls._meta.database_name)
        for f in cls._field_list:
            if f.index and not f.primary_key:
                cls._meta.database.query(
                    f"CREATE INDEX IF NOT EXISTS \"idx_{cls._meta.table}_{f.column}\" "
                    f"ON \"{cls._meta.table}\"(\"{f.column}\")",
                    database=cls._meta.database_name
                )
        for idx in cls._meta.indexes:
            cols = ", ".join(f"\"{c}\"" for c in idx["fields"])
            name = idx.get("name", f"idx_{cls._meta.table}_{'_'.join(idx['fields'])}")
            unique = "UNIQUE " if idx.get("unique") else ""
            cls._meta.database.query(
                f"CREATE {unique}INDEX IF NOT EXISTS \"{name}\" "
                f"ON \"{cls._meta.table}\"({cols})",
                database=cls._meta.database_name
            )

    @classmethod
    def drop_table(cls, if_exists=True):
        """Drop the database table for this model.

        Parameters
        ----------
        if_exists : bool
            Add ``IF EXISTS`` to the SQL.
        """
        if not cls._meta.database:
            raise SparkDBError("no database configured")
        ie = "IF EXISTS " if if_exists else ""
        cls._meta.database.query(
            f"DROP TABLE {ie}\"{cls._meta.table}\"",
            database=cls._meta.database_name
        )

    @classmethod
    def find(cls, pk):
        """Find a single record by primary key."""
        return cls.where(**{cls._meta.pk_name: pk}).first()

    @classmethod
    def where(cls, *args, **kwargs):
        """Start a filtered query.

        Accepts ``Q`` objects and/or keyword lookups.
        """
        return QuerySet(cls).where(*args, **kwargs)

    @classmethod
    def all(cls):
        """Return all records."""
        return QuerySet(cls).all()

    @classmethod
    def first(cls):
        """Return the first record according to default ordering."""
        return QuerySet(cls).first()

    @classmethod
    def count(cls):
        """Return the number of matching records."""
        return QuerySet(cls).count()

    @classmethod
    def order_by(cls, *fields):
        """Return a QuerySet ordered by the given fields.

        Prefix a field name with ``-`` for descending order.
        """
        return QuerySet(cls).order_by(*fields)

    @classmethod
    def limit(cls, n):
        """Return a QuerySet limited to *n* records."""
        return QuerySet(cls).limit(n)

    @classmethod
    def offset(cls, n):
        """Return a QuerySet offset by *n* records."""
        return QuerySet(cls).offset(n)

    @classmethod
    def values(cls, *fields):
        """Return a QuerySet that returns dicts for the given fields."""
        return QuerySet(cls).values(*fields)

    @classmethod
    def values_list(cls, *fields):
        """Return a QuerySet that returns tuples for the given fields."""
        return QuerySet(cls).values_list(*fields)

    @classmethod
    def create(cls, **kwargs):
        """Create and save a new instance."""
        inst = cls(**kwargs)
        inst.save()
        return inst

    @classmethod
    def bulk_create(cls, items, batch_size=100):
        """Insert many records in a single query.

        Parameters
        ----------
        items : list of Model or list of dict
            Instances or dicts to insert.
        batch_size : int
            Max number of records per INSERT statement.

        Returns
        -------
        list of Model
            The created instances (with PKs assigned).
        """
        if not items:
            return []
        db = cls._meta.database
        table = cls._meta.table
        pk_name = cls._meta.pk_name
        pk_field = cls._fields.get(pk_name)
        auto_pk = pk_field and getattr(pk_field, "auto_increment", False)

        created = []
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            cols = []
            all_placeholders = []
            all_params = []
            field_list = [(n, f) for n, f in cls._fields.items()
                          if not (n == pk_name and auto_pk)]
            cols = [f"\"{f.column}\"" for n, f in field_list]
            batch_instances = []
            for item in batch:
                if isinstance(item, cls):
                    inst = item
                else:
                    inst = cls(**item)
                batch_instances.append(inst)
                placeholders = []
                for n, f in field_list:
                    val = inst._data.get(n)
                    val = f.to_db(val)
                    placeholders.append("?")
                    all_params.append(val)
                all_placeholders.append(f"({', '.join(placeholders)})")
            sql = f"INSERT INTO \"{table}\" ({', '.join(cols)}) VALUES {', '.join(all_placeholders)}"
            r = db.query(sql, params=all_params, database=cls._meta.database_name)
            if auto_pk and r:
                rows = r.get("rows", [])
                if rows and len(rows[0]) > 0:
                    last_id = rows[0][0]
                    for j, inst in enumerate(batch_instances):
                        if inst._data.get(pk_name) is None:
                            inst._data[pk_name] = last_id + j if r.get("rows") and len(r["rows"]) > 1 else last_id
                            inst._exists = True
                            inst._saved = True
            created.extend(batch_instances)
        return created

    @classmethod
    def get_or_create(cls, defaults=None, **kwargs):
        """Look up a record or create it if it does not exist.

        Returns
        -------
        tuple
            ``(instance, created_flag)``
        """
        defaults = defaults or {}
        instance = cls.where(**kwargs).first()
        if instance:
            return instance, False
        merged = {**kwargs, **defaults}
        instance = cls.create(**merged)
        return instance, True

    @classmethod
    def update_or_create(cls, defaults=None, **kwargs):
        """Update a record or create it if it does not exist.

        Returns
        -------
        tuple
            ``(instance, created_flag)``
        """
        defaults = defaults or {}
        instance = cls.where(**kwargs).first()
        if instance:
            for key, val in defaults.items():
                setattr(instance, key, val)
            instance.save()
            return instance, False
        merged = {**kwargs, **defaults}
        instance = cls.create(**merged)
        return instance, True

    @classmethod
    def update(cls, **kwargs):
        """Update all rows matching the current query.

        Shorthand for ``QuerySet(cls).update(**kwargs)``.
        """
        return QuerySet(cls).update(**kwargs)

    def before_save(self):
        """Hook called before saving. Override in subclasses."""

    def after_save(self):
        """Hook called after saving. Override in subclasses."""

    def before_delete(self):
        """Hook called before deleting. Override in subclasses."""

    def after_delete(self):
        """Hook called after deleting. Override in subclasses."""

    def save(self):
        """Insert or update this record."""
        if not self._meta.database:
            raise SparkDBError("no database configured")
        self.before_save()
        db = self._meta.database
        table = self._meta.table
        pk_name = self._meta.pk_name
        if self._meta.timestamps:
            now = datetime.now(timezone.utc).isoformat()
            if not self._exists:
                if "created_at" not in self._data or self._data["created_at"] is None:
                    self._data["created_at"] = now
            self._data["updated_at"] = now
        if self._exists and self.pk is not None:
            sets = []
            params = []
            for name, field in self._fields.items():
                if name == pk_name:
                    continue
                val = self._data.get(name)
                val = field.to_db(val)
                sets.append(f"\"{field.column}\" = ?")
                params.append(val)
            if not sets:
                return
            if self._meta.timestamps:
                sets.append("\"updated_at\" = ?")
                params.append(
                    self._data.get("updated_at", datetime.now(timezone.utc).isoformat())
                )
            params.append(self.pk)
            sql = f"UPDATE \"{table}\" SET {', '.join(sets)} WHERE \"{pk_name}\" = ?"
            db.query(sql, params=params, database=self._meta.database_name)
        else:
            cols = []
            placeholders = []
            params = []
            for name, field in self._fields.items():
                if name == pk_name and getattr(field, "auto_increment", False):
                    continue
                val = self._data.get(name)
                val = field.to_db(val)
                cols.append(f"\"{field.column}\"")
                placeholders.append("?")
                params.append(val)
            if self._meta.timestamps:
                now = datetime.now(timezone.utc).isoformat()
                cols.append("\"created_at\"")
                placeholders.append("?")
                params.append(now)
                cols.append("\"updated_at\"")
                placeholders.append("?")
                params.append(now)
            sql = f"INSERT INTO \"{table}\" ({', '.join(cols)}) VALUES ({', '.join(placeholders)})"
            r = db.query(sql, params=params, database=self._meta.database_name)
            if pk_name in self._fields and (self._data.get(pk_name) is None):
                if r:
                    rows = r.get("rows", [])
                    if rows and len(rows[0]) > 0:
                        self._data[pk_name] = rows[0][0]
            self._exists = True
            self._saved = True
        self.after_save()

    def delete(self):
        """Delete this record from the database."""
        if not self._meta.database or self.pk is None:
            return
        self.before_delete()
        db = self._meta.database
        table = self._meta.table
        pk_name = self._meta.pk_name
        db.query(
            f"DELETE FROM \"{table}\" WHERE \"{pk_name}\" = ?",
            params=[self.pk],
            database=self._meta.database_name
        )
        self._exists = False
        self.after_delete()

    def reload(self):
        """Re-fetch this record from the database."""
        if self.pk is None:
            return
        fresh = self.find(self.pk)
        if fresh:
            self._data = fresh._data
            self._exists = True
        else:
            self._exists = False

    def __repr__(self):
        pk = self.pk
        shown = ", ".join(
            f"{k}={v}" for k, v in self._data.items() if v is not None
        )
        return f"<{self.__class__.__name__} pk={pk} ({shown})>"
