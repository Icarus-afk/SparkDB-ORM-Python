"""Relationship fields and helpers: ForeignKey, LazyRelation, ReverseRelation, and has_many.

These provide the "related object" traversal that can lazy-load or
eager-load associated records.
"""

from sparkdb.fields import Field
from sparkdb.exceptions import ValidationError


class ForeignKey(Field):
    """A many-to-one relationship to another model.

    Stores the related model's primary key as an ``INTEGER`` column.

    Parameters
    ----------
    model_cls : type
        The related ``Model`` subclass.
    column : str, optional
        Custom column name (defaults to the field name).
    nullable : bool
        Whether the FK can be ``None`` (default ``True``).
    """

    def __init__(self, model_cls, column=None, **kwargs):
        self.ref_model = model_cls
        kwargs.setdefault("nullable", True)
        if column:
            kwargs["column"] = column
        kwargs.setdefault("default", None)
        super().__init__(**kwargs)

    def contribute_to_class(self, name):
        super().contribute_to_class(name)
        self._name = name

    def sql_type(self):
        base = "INTEGER"
        ref_table = self.ref_model._meta.table
        ref_pk = self.ref_model._meta.pk_name
        return f"{base} REFERENCES \"{ref_table}\"(\"{ref_pk}\")"

    def to_db(self, value):
        if value is None:
            return None
        if hasattr(value, "pk"):
            return value.pk
        return int(value)

    def from_db(self, value):
        if value is None:
            return None
        if isinstance(value, LazyRelation):
            return value
        return LazyRelation(self.ref_model, value, self._name)

    def validate(self, value):
        if value is not None:
            if isinstance(value, (int, type(self.ref_model) if isinstance(self.ref_model, type) else ())):
                return
            if hasattr(value, "pk"):
                return
            try:
                int(value)
            except (TypeError, ValueError):
                raise ValidationError(
                    f"{self._name} must be an integer or {self.ref_model.__name__} instance"
                )


class LazyRelation:
    """Deferred proxy for a related model instance.

    The related object is not fetched from the database until an
    attribute is accessed.
    """

    def __init__(self, model_cls, pk, fk_name, instance=None):
        self._model_cls = model_cls
        self._pk = pk
        self._fk_name = fk_name
        self._instance = instance
        self._resolved = None

    @property
    def pk(self):
        return self._pk

    def _resolve(self):
        if self._resolved is not None:
            return self._resolved
        cache_attr = f"_{self._fk_name}_resolved"
        if self._instance and hasattr(self._instance, cache_attr):
            self._resolved = getattr(self._instance, cache_attr)
        else:
            self._resolved = self._model_cls.find(self._pk)
        return self._resolved

    def __getattr__(self, name):
        resolved = self._resolve()
        if resolved is None:
            raise AttributeError(f"related object ({self._model_cls.__name__} pk={self._pk}) not found")
        return getattr(resolved, name)

    def __repr__(self):
        resolved = self._resolve()
        if resolved is None:
            return f"<LazyRelation: {self._model_cls.__name__} pk={self._pk} (not found)>"
        return repr(resolved)

    def __bool__(self):
        return self._resolve() is not None


class ReverseRelation:
    """Descriptor for the reverse side of a ForeignKey (one-to-many).

    Typically created via the ``has_many()`` helper rather than
    directly.
    """

    def __init__(self, fk_column, related_cls, rel_name):
        self.fk_column = fk_column
        self.related_cls = related_cls
        self.rel_name = rel_name

    def __get__(self, instance, owner):
        if instance is None:
            return self
        cache_attr = f"_{self.rel_name}_cache"
        if hasattr(instance, cache_attr):
            return getattr(instance, cache_attr)
        if instance.pk is None:
            return []
        return self.related_cls.where(**{self.fk_column: instance.pk}).all()

    def __set__(self, instance, value):
        object.__setattr__(instance, f"_{self.rel_name}_cache", value)


def has_many(related_cls, name=None, fk_column=None):
    """Decorator that installs a reverse relation on a model.

    Parameters
    ----------
    related_cls : type
        The child model class (the one with the ForeignKey).
    name : str, optional
        Attribute name on the parent. Defaults to ``<table>_set``.
    fk_column : str, optional
        The ForeignKey column name in the child table. Defaults to
        ``<singular_table>_id``.

    Example
    -------
    >>> has_many(Book, name="books", fk_column="author")(Author)
    >>> author = Author.find(1)
    >>> author.books  # list of Book instances
    """
    ref_table = related_cls._meta.table
    singular = ref_table[:-1] if ref_table.endswith("s") and not ref_table.endswith("ss") else ref_table
    if fk_column is None:
        fk_column = singular + "_id"
    if name is None:
        name = ref_table + "_set"

    rel = ReverseRelation(fk_column, related_cls, name)

    def decorator(target_cls):
        rel_name = name
        setattr(target_cls, rel_name, rel)
        if not hasattr(target_cls, "_rel_descriptors"):
            target_cls._rel_descriptors = {}
        target_cls._rel_descriptors[rel_name] = rel
        prefetch_name = f"_prefetch_{rel_name}"
        def loader(pks):
            if not pks:
                return []
            return related_cls.where(**{f"{fk_column}__in": list(pks)}).all()
        loader.fk_column = fk_column
        loader.rel_name = rel_name
        setattr(target_cls, prefetch_name, staticmethod(loader))

        def prefetch_key(self):
            return self.pk
        setattr(target_cls, f"_prefetch_key_{rel_name}", property(prefetch_key))

        return target_cls

    return decorator
