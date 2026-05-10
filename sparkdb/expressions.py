"""SQL expression helpers: Q objects, F objects, and WHERE clause builders.

These utilities generate SQL fragments from high-level Python
expressions.  They are used internally by ``QuerySet`` and can
also be used directly in user code for complex queries.
"""

import re


class Q:
    """Encapsulates a collection of WHERE clauses joined by AND/OR.

    Supports ``|`` (OR), ``&`` (AND), and ``~`` (NOT) operators for
    building complex boolean logic.

    Parameters
    ----------
    **kwargs
        Field lookups, e.g. ``name="Alice"``, ``age__gt=18``.
    """

    def __init__(self, **kwargs):
        self._children = []
        self._connector = "AND"
        self._negated = False
        for key, val in kwargs.items():
            self._children.append((key, val))

    @classmethod
    def _from_list(cls, children, connector="AND", negated=False):
        q = cls()
        q._children = list(children) if children else []
        q._connector = connector
        q._negated = negated
        return q

    def __or__(self, other):
        if not isinstance(other, Q):
            raise TypeError(f"cannot OR Q with {type(other).__name__}")
        return Q._from_list([self, other], connector="OR")

    def __and__(self, other):
        if not isinstance(other, Q):
            raise TypeError(f"cannot AND Q with {type(other).__name__}")
        return Q._from_list([self, other], connector="AND")

    def __invert__(self):
        return Q._from_list(
            list(self._children), connector=self._connector,
            negated=not self._negated
        )

    def resolve(self):
        """Resolve this Q object to SQL clauses and parameters.

        Returns
        -------
        tuple
            ``(clauses, params)`` where *clauses* is a list of SQL
            fragments and *params* is a list of parameter values.
        """
        clauses = []
        params = []

        for child in self._children:
            if isinstance(child, tuple):
                key, val = child
                field, op = _parse_where_key(key)
                clause, p = _build_where(field, op, val)
                clauses.append(clause)
                params.extend(p)
            elif isinstance(child, Q):
                sub_clauses, sub_params = child.resolve()
                if sub_clauses:
                    connector = child._connector
                    if connector == "OR" and len(sub_clauses) > 1:
                        clauses.append(f"({' OR '.join(sub_clauses)})")
                    else:
                        clauses.append(f"({' AND '.join(sub_clauses)})" if len(sub_clauses) > 1 else sub_clauses[0])
                    params.extend(sub_params)

        if self._negated:
            result_clauses = [f"NOT ({c})" if len(clauses) > 1 else f"NOT {c}" for c in clauses]
            return result_clauses, params

        return clauses, params


class F:
    """Reference to a model field for server-side evaluation.

    Used in WHERE and UPDATE clauses to refer to column values
    without fetching them into Python first.

    Parameters
    ----------
    name : str
        The field name.
    """

    def __init__(self, name):
        if not isinstance(name, str):
            raise TypeError(f"F() name must be a string, got {type(name).__name__}")
        self.name = name

    def __repr__(self):
        return f"F({self.name!r})"

    def __add__(self, other):
        return _FExpr(self, "+", other)

    def __sub__(self, other):
        return _FExpr(self, "-", other)

    def __mul__(self, other):
        return _FExpr(self, "*", other)

    def __truediv__(self, other):
        return _FExpr(self, "/", other)

    def __neg__(self):
        return _FExpr(None, "-", self)


class _FExpr:
    """Internal representation of an arithmetic expression involving F references."""

    def __init__(self, lhs, op, rhs):
        self.lhs = lhs
        self.op = op
        self.rhs = rhs

    def __repr__(self):
        return f"F({self.lhs} {self.op} {self.rhs})"

    def __add__(self, other):
        return _FExpr(self, "+", other)

    def __sub__(self, other):
        return _FExpr(self, "-", other)

    def __mul__(self, other):
        return _FExpr(self, "*", other)

    def __truediv__(self, other):
        return _FExpr(self, "/", other)


_WHERE_OPS = {
    "exact": "=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "ne": "!=",
    "contains": "LIKE",
    "icontains": "LIKE",
    "startswith": "LIKE",
    "istartswith": "LIKE",
    "endswith": "LIKE",
    "iendswith": "LIKE",
    "in": "IN",
    "not_in": "NOT IN",
    "isnull": "IS",
}


def _parse_where_key(key):
    """Parse a Django-style lookup key into field name and operator.

    Examples
    --------
    ``"name"`` → ``("name", "exact")``
    ``"age__gt"`` → ``("age", "gt")``
    ``"name__contains"`` → ``("name", "contains")``
    """
    if not key:
        raise ValueError("empty field name in where key")
    parts = key.split("__")
    if len(parts) == 1:
        return parts[0], "exact"
    op = parts[-1]
    if op in _WHERE_OPS:
        return "__".join(parts[:-1]), op
    return key, "exact"


def _build_where(field, op, value):
    """Build a SQL WHERE clause fragment for a single lookup.

    Parameters
    ----------
    field : str
        Column/field name.
    op : str
        Lookup operator key (from ``_WHERE_OPS``).
    value : any
        The value to compare against.

    Returns
    -------
    tuple
        ``(clause_string, param_list)``
    """
    if op == "in" or op == "not_in":
        if isinstance(value, (list, tuple)):
            if not value:
                return f"\"{field}\" {_WHERE_OPS[op]} (NULL)", []
            placeholders = ", ".join(["?" for _ in value])
            return f"\"{field}\" {_WHERE_OPS[op]} ({placeholders})", list(value)
        raise ValueError(f"{op} lookup requires a list or tuple, got {type(value).__name__}")
    if op in ("contains", "icontains"):
        if value is None:
            raise ValueError("contains lookup requires a non-None value")
        return f"\"{field}\" LIKE ?", [f"%{value}%"]
    if op == "startswith" or op == "istartswith":
        if value is None:
            raise ValueError("startswith lookup requires a non-None value")
        return f"\"{field}\" LIKE ?", [f"{value}%"]
    if op == "endswith" or op == "iendswith":
        if value is None:
            raise ValueError("endswith lookup requires a non-None value")
        return f"\"{field}\" LIKE ?", [f"%{value}"]
    if op == "isnull":
        not_str = "" if value else " NOT"
        return f"\"{field}\" IS{not_str} NULL", []
    return f"\"{field}\" {_WHERE_OPS[op]} ?", [value]
