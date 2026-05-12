"""Field type definitions for model declarations.

Each field class is a Python descriptor that handles validation,
type coercion, and SQL type mapping.
"""

import binascii
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal as _Decimal
from uuid import UUID as _UUID

from sparkdb.exceptions import ValidationError


class Field:
    """Base descriptor for all model fields.

    Parameters
    ----------
    column : str, optional
        Custom column name in the database. Defaults to the field name.
    default : any, optional
        Default value for new instances. Callables are called at
        instantiation time.
    nullable : bool
        Whether the field accepts ``None`` (default ``True``).
    null : bool
        Alias for *nullable* (consistent with Django/SQLAlchemy
        convention).
    unique : bool
        Add a UNIQUE constraint on this column.
    primary_key : bool
        Mark as the primary key.
    index : bool
        Create an index for this column.
    """

    _counter = 0

    def __init__(self, column=None, default=None, nullable=True, null=None, unique=False, primary_key=False, index=False):
        """
        :param nullable: Whether the field can be None (default True)
        :param null: Alias for nullable (consistent with Django/SQLAlchemy convention)
        """
        Field._counter += 1
        self._order = Field._counter
        self._column = column
        self.default = default
        self.nullable = null if null is not None else nullable
        self.unique = unique
        self.primary_key = primary_key
        self.index = index
        self._name = None

    def contribute_to_class(self, name):
        self._name = name

    @property
    def column(self):
        return self._column or self._name

    def sql_type(self):
        raise NotImplementedError

    def to_db(self, value):
        return value

    def from_db(self, value):
        return value

    def validate(self, value):
        if value is None and not self.nullable:
            raise ValidationError(f"{self._name} cannot be null")

    def __get__(self, instance, owner):
        if instance is None:
            return self
        val = instance._data.get(self._name)
        resolved = f"_{self._name}_resolved"
        if hasattr(instance, resolved):
            return getattr(instance, resolved)
        if val is not None:
            return self.from_db(val)
        return val

    def __set__(self, instance, value):
        self.validate(value)
        instance._data[self._name] = value


class String(Field):
    """Variable-length string field mapped to ``VARCHAR(n)``.

    Parameters
    ----------
    max_length : int
        Maximum number of characters (required).
    """

    def __init__(self, max_length=255, **kwargs):
        super().__init__(**kwargs)
        self.max_length = max_length

    def sql_type(self):
        return f"VARCHAR({self.max_length})"

    def validate(self, value):
        super().validate(value)
        if value is not None:
            if not isinstance(value, str):
                raise ValidationError(f"{self._name} must be a string")
            if len(value) > self.max_length:
                raise ValidationError(f"{self._name} exceeds max length {self.max_length}")


class Text(Field):
    """Unlimited-length text field mapped to ``TEXT``."""

    def sql_type(self):
        return "TEXT"

    def validate(self, value):
        super().validate(value)
        if value is not None and not isinstance(value, str):
            raise ValidationError(f"{self._name} must be a string")


class Integer(Field):
    """Integer field mapped to ``INTEGER``.

    Parameters
    ----------
    auto_increment : bool
        Make this an auto-incrementing primary key.
    """

    def __init__(self, auto_increment=False, **kwargs):
        super().__init__(**kwargs)
        self.auto_increment = auto_increment

    def sql_type(self):
        base = "INTEGER"
        if self.auto_increment:
            base += " PRIMARY KEY AUTOINCREMENT"
        return base

    def to_db(self, value):
        if value is None:
            return None
        return int(value)

    def from_db(self, value):
        if value is None:
            return None
        return int(value)

    def validate(self, value):
        super().validate(value)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValidationError(f"{self._name} must be an integer")


class Float(Field):
    """Floating-point field mapped to ``REAL``."""

    def sql_type(self):
        return "REAL"

    def to_db(self, value):
        if value is None:
            return None
        return float(value)

    def from_db(self, value):
        if value is None:
            return None
        return float(value)

    def validate(self, value):
        super().validate(value)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f"{self._name} must be a number")


class Boolean(Field):
    """Boolean field stored as ``INTEGER`` (``0``/``1``).

    Accepts Python ``bool`` as well as ``0``/``1`` integers
    for SQLite compatibility.
    """

    def sql_type(self):
        return "INTEGER"

    def to_db(self, value):
        if value is None:
            return None
        return 1 if value else 0

    def from_db(self, value):
        if value is None:
            return None
        return bool(value)

    def validate(self, value):
        super().validate(value)
        if value is not None and not isinstance(value, bool):
            if not (isinstance(value, int) and value in (0, 1)):
                raise ValidationError(f"{self._name} must be a boolean")


class DateTime(Field):
    """Datetime field stored as ISO-8601 ``TEXT``.

    Parameters
    ----------
    auto_now : bool
        Set to the current time on every save.
    auto_now_add : bool
        Set to the current time only on creation.
    """

    def __init__(self, auto_now=False, auto_now_add=False, **kwargs):
        super().__init__(**kwargs)
        self.auto_now = auto_now
        self.auto_now_add = auto_now_add
        if auto_now or auto_now_add:
            self.nullable = True

    def sql_type(self):
        return "TEXT"

    def to_db(self, value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    def from_db(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value

    def validate(self, value):
        super().validate(value)
        if value is not None and not isinstance(value, datetime):
            raise ValidationError(f"{self._name} must be a datetime object")


class JSON(Field):
    """JSON field stored as ``TEXT`` (serialized)."""

    def sql_type(self):
        return "TEXT"

    def to_db(self, value):
        if value is None:
            return None
        try:
            return json.dumps(value)
        except (TypeError, ValueError) as e:
            raise ValidationError(f"{self._name} is not JSON serializable: {e}") from e

    def from_db(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            return json.loads(value)
        return value

    def validate(self, value):
        super().validate(value)
        if value is not None:
            try:
                json.dumps(value)
            except (TypeError, ValueError) as e:
                raise ValidationError(f"{self._name} is not JSON serializable: {e}") from e


class BLOB(Field):
    """Binary field mapped to ``BLOB``.

    Accepts ``bytes``. Values are hex-encoded for SQL transmission
    and decoded back on read.
    """

    def sql_type(self):
        return "BLOB"

    def to_db(self, value):
        if value is None:
            return None
        if isinstance(value, bytes):
            return binascii.hexlify(value).decode("ascii")
        raise ValidationError(f"{self._name} must be bytes")

    def from_db(self, value):
        if value is None:
            return None
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return binascii.unhexlify(value)
        return value

    def validate(self, value):
        super().validate(value)
        if value is not None and not isinstance(value, (bytes, bytearray)):
            raise ValidationError(f"{self._name} must be bytes")


class Date(Field):
    """Date field stored as ISO-8601 ``TEXT`` (``YYYY-MM-DD``).

    Maps to/from ``datetime.date``.
    """

    def sql_type(self):
        return "TEXT"

    def to_db(self, value):
        if value is None:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, str):
            return value
        raise ValidationError(f"{self._name} must be a date object")

    def from_db(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            return date.fromisoformat(value)
        return value

    def validate(self, value):
        super().validate(value)
        if value is not None and not isinstance(value, date):
            raise ValidationError(f"{self._name} must be a date object")


class Time(Field):
    """Time field stored as ISO-8601 ``TEXT`` (``HH:MM:SS``).

    Maps to/from ``datetime.time``.
    """

    def sql_type(self):
        return "TEXT"

    def to_db(self, value):
        if value is None:
            return None
        if isinstance(value, time):
            return value.isoformat()
        if isinstance(value, str):
            return value
        raise ValidationError(f"{self._name} must be a time object")

    def from_db(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            return time.fromisoformat(value)
        return value

    def validate(self, value):
        super().validate(value)
        if value is not None and not isinstance(value, time):
            raise ValidationError(f"{self._name} must be a time object")


class Decimal(Field):
    """Fixed-precision decimal field stored as ``TEXT``.

    Maps to/from ``decimal.Decimal``. Uses text storage to avoid
    floating-point rounding errors.

    Parameters
    ----------
    max_digits : int, optional
        Maximum number of digits (not enforced at the ORM level).
    decimal_places : int, optional
        Number of decimal places (not enforced at the ORM level).
    """

    def __init__(self, max_digits=None, decimal_places=None, **kwargs):
        super().__init__(**kwargs)
        self.max_digits = max_digits
        self.decimal_places = decimal_places

    def sql_type(self):
        return "TEXT"

    def to_db(self, value):
        if value is None:
            return None
        if isinstance(value, _Decimal):
            return str(value)
        if isinstance(value, (int, float)):
            return str(_Decimal(str(value)))
        if isinstance(value, str):
            return value
        raise ValidationError(f"{self._name} must be a decimal or number")

    def from_db(self, value):
        if value is None:
            return None
        if isinstance(value, (str, int, float)):
            return _Decimal(str(value))
        return value

    def validate(self, value):
        super().validate(value)
        if value is not None and not isinstance(value, (_Decimal, int, float, str)):
            raise ValidationError(f"{self._name} must be a decimal or number")


class UUID(Field):
    """UUID field stored as ``TEXT``.

    Maps to/from ``uuid.UUID``.
    """

    def sql_type(self):
        return "TEXT"

    def to_db(self, value):
        if value is None:
            return None
        if isinstance(value, _UUID):
            return str(value)
        if isinstance(value, str):
            return value
        raise ValidationError(f"{self._name} must be a UUID")

    def from_db(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            return _UUID(value)
        return value

    def validate(self, value):
        super().validate(value)
        if value is not None and not isinstance(value, (_UUID, str)):
            raise ValidationError(f"{self._name} must be a UUID or string")
