"""QuerySet — the query builder and result iterator.

``QuerySet`` provides a lazy, immutable API for constructing and
executing SQL queries.  Every mutating method returns a *clone* so
that chaining is safe and the original is never modified.
"""

import json

from sparkdb.expressions import Q, F, _FExpr, _parse_where_key, _build_where, _WHERE_OPS
from sparkdb.exceptions import ValidationError


def _render_fexpr(expr):
    if isinstance(expr, F):
        return f"\"{expr.name}\""
    if isinstance(expr, _FExpr):
        if expr.lhs is None:
            return f"-{_render_fexpr(expr.rhs)}"
        return f"({_render_fexpr(expr.lhs)} {expr.op} {_render_fexpr(expr.rhs)})"
    if isinstance(expr, (int, float)):
        return str(expr)
    if isinstance(expr, str):
        return f"'{expr.replace(chr(39), chr(39)+chr(39))}'"
    return f"'{str(expr).replace(chr(39), chr(39)+chr(39))}'"


class QuerySet:
    """Lazy, immutable query builder for a single model.

    Parameters
    ----------
    model_cls : type
        The ``Model`` subclass to query against.
    """

    def __init__(self, model_cls):
        self._model_cls = model_cls
        self._where_clauses = []
        self._where_params = []
        self._q_objects = []
        self._order_by_fields = []
        self._order_dirs = {}
        self._limit_value = None
        self._offset_value = None
        self._select_related = []
        self._prefetch_related = []
        self._distinct_flag = False
        self._group_by_fields = []
        self._having_clauses = []
        self._having_params = []
        self._values_fields = None
        self._values_list_flag = False
        self._debug = False

        meta = model_cls._meta
        if meta.ordering:
            for field in meta.ordering:
                desc = field.startswith("-")
                fname = field.lstrip("-")
                self._order_by_fields.append(fname)
                if desc:
                    self._order_dirs[fname] = True

    def _clone(self):
        qs = QuerySet(self._model_cls)
        qs._where_clauses = list(self._where_clauses)
        qs._where_params = list(self._where_params)
        qs._q_objects = list(self._q_objects)
        qs._order_by_fields = list(self._order_by_fields)
        qs._order_dirs = dict(self._order_dirs)
        qs._limit_value = self._limit_value
        qs._offset_value = self._offset_value
        qs._select_related = list(self._select_related)
        qs._prefetch_related = list(self._prefetch_related)
        qs._distinct_flag = self._distinct_flag
        qs._group_by_fields = list(self._group_by_fields)
        qs._having_clauses = list(self._having_clauses)
        qs._having_params = list(self._having_params)
        if isinstance(self._values_fields, tuple):
            qs._values_fields = self._values_fields
        else:
            qs._values_fields = list(self._values_fields) if self._values_fields else None
        qs._values_list_flag = self._values_list_flag
        qs._debug = self._debug
        return qs

    def debug(self, enabled=True):
        """Print the generated SQL when executed.

        Parameters
        ----------
        enabled : bool
        """
        qs = self._clone()
        qs._debug = enabled
        return qs

    def where(self, *args, **kwargs):
        """Add WHERE conditions.

        Accepts ``Q`` objects and/or keyword lookups::

            qs.where(active=True, age__gt=18)
            qs.where(Q(name="Alice") | Q(name="Bob"))
        """
        qs = self._clone()
        if args and isinstance(args[0], Q):
            qs._q_objects.append(args[0])
        for key, val in kwargs.items():
            field, op = _parse_where_key(key)
            if isinstance(val, F):
                clause = f"\"{field}\" {_WHERE_OPS[op] if op != 'exact' else '='} \"{val.name}\""
                qs._where_clauses.append(clause)
            elif isinstance(val, _FExpr):
                clause = f"\"{field}\" {_WHERE_OPS[op] if op != 'exact' else '='} {_render_fexpr(val)}"
                qs._where_clauses.append(clause)
            else:
                clause, params = _build_where(field, op, val)
                qs._where_clauses.append(clause)
                qs._where_params.extend(params)
        return qs

    def where_raw(self, clause, *params):
        """Add a raw SQL WHERE clause.

        Parameters
        ----------
        clause : str
            SQL fragment (e.g. ``"age > ?"``).
        params
            Values for ``?`` placeholders.
        """
        qs = self._clone()
        qs._where_clauses.append(clause)
        qs._where_params.extend(params)
        return qs

    def order_by(self, *fields):
        """Set ORDER BY columns.

        Replaces any default ordering from ``Meta.ordering``.
        Prefix a field name with ``-`` for descending.
        """
        qs = self._clone()
        qs._order_by_fields = []
        qs._order_dirs = {}
        for f in fields:
            if not f or not isinstance(f, str):
                raise ValueError(f"invalid order_by field: {f!r}")
            desc = f.startswith("-")
            fname = f.lstrip("-")
            if not fname:
                raise ValueError(f"invalid order_by field: {f!r}")
            qs._order_by_fields.append(fname)
            if desc:
                qs._order_dirs[fname] = True
            else:
                qs._order_dirs[fname] = False
        return qs

    def limit(self, n):
        """Limit the result set to *n* records."""
        if not isinstance(n, int) or n < 0:
            raise ValueError(f"limit must be a non-negative integer, got {n!r}")
        qs = self._clone()
        qs._limit_value = n
        return qs

    def offset(self, n):
        """Skip the first *n* records."""
        if not isinstance(n, int) or n < 0:
            raise ValueError(f"offset must be a non-negative integer, got {n!r}")
        qs = self._clone()
        qs._offset_value = n
        return qs

    def distinct(self):
        """Add DISTINCT to the SELECT."""
        qs = self._clone()
        qs._distinct_flag = True
        return qs

    def group_by(self, *fields):
        """Add GROUP BY columns."""
        qs = self._clone()
        qs._group_by_fields.extend(fields)
        return qs

    def having(self, *args, **kwargs):
        """Add HAVING conditions (after GROUP BY).

        Accepts raw SQL with params or Q objects.
        """
        qs = self._clone()
        if args:
            if isinstance(args[0], Q):
                qs._q_objects.append(args[0])
            elif isinstance(args[0], str):
                qs._having_clauses.append(args[0])
                if len(args) > 1:
                    if isinstance(args[1], (list, tuple)):
                        qs._having_params.extend(args[1])
                    else:
                        qs._having_params.append(args[1])
        for key, val in kwargs.items():
            field, op = _parse_where_key(key)
            clause, params = _build_where(field, op, val)
            qs._having_clauses.append(clause)
            qs._having_params.extend(params)
        return qs

    def select_related(self, *fields):
        """Eagerly load ForeignKey relations."""
        qs = self._clone()
        qs._select_related.extend(fields)
        return qs

    def prefetch_related(self, *fields):
        """Eagerly load reverse relations."""
        qs = self._clone()
        qs._prefetch_related.extend(fields)
        return qs

    def values(self, *fields):
        """Return dicts for the given fields instead of model instances."""
        if not fields:
            raise ValueError("values() requires at least one field name")
        qs = self._clone()
        qs._values_fields = fields
        qs._values_list_flag = False
        return qs

    def values_list(self, *fields):
        """Return tuples for the given fields instead of model instances."""
        if not fields:
            raise ValueError("values_list() requires at least one field name")
        qs = self._clone()
        qs._values_fields = fields
        qs._values_list_flag = True
        return qs

    def pluck(self, field):
        """Return a list of single values for the given field."""
        qs = self._clone()
        qs._values_fields = (field,)
        qs._values_list_flag = True
        return qs

    def _resolve_q_objects(self):
        if not self._q_objects:
            return
        all_clauses = list(self._where_clauses)
        all_params = list(self._where_params)

        for q in self._q_objects:
            clauses, params = q.resolve()
            connector = q._connector
            if connector == "OR" and (all_clauses or self._q_objects):
                if clauses:
                    combined = f" OR ".join(clauses) if len(clauses) > 1 else clauses[0]
                    all_clauses.append(f"({combined})")
                    all_params.extend(params)
            else:
                all_clauses.extend(clauses)
                all_params.extend(params)

        self._where_clauses = all_clauses
        self._where_params = all_params
        self._q_objects = []

    def _resolve_having_q_objects(self):
        if not self._q_objects:
            return
        for q in self._q_objects:
            clauses, params = q.resolve()
            self._having_clauses.extend(clauses)
            self._having_params.extend(params)
        self._q_objects = []

    def _build_select(self, for_count=False):
        table = self._model_cls._meta.table
        if for_count:
            sql = "SELECT COUNT(*) as cnt"
        elif self._values_fields:
            cols_list = []
            for f in self._values_fields:
                field_obj = self._model_cls._fields.get(f)
                col = field_obj.column if field_obj else f
                cols_list.append(f"\"{col}\"")
            cols = ", ".join(cols_list)
            prefix = "DISTINCT " if self._distinct_flag else ""
            sql = f"SELECT {prefix}{cols}"
        else:
            prefix = "DISTINCT " if self._distinct_flag else ""
            sql = f"SELECT {prefix}*"

        sql += f" FROM \"{table}\""
        params = []

        self._resolve_q_objects()

        if self._where_clauses:
            sql += " WHERE " + " AND ".join(self._where_clauses)
            params.extend(self._where_params)

        if self._group_by_fields:
            sql += " GROUP BY \"" + "\", \"".join(self._group_by_fields) + "\""

        if self._having_clauses:
            sql += " HAVING " + " AND ".join(self._having_clauses)
            params.extend(self._having_params)

        if not for_count:
            if self._order_by_fields:
                parts = []
                for f in self._order_by_fields:
                    dir = " DESC" if self._order_dirs.get(f) else ""
                    parts.append(f"\"{f}\"{dir}")
                sql += " ORDER BY " + ", ".join(parts)

            if self._limit_value is not None:
                sql += f" LIMIT {self._limit_value}"

            if self._offset_value is not None:
                if self._limit_value is None:
                    sql += " LIMIT -1"
                sql += f" OFFSET {self._offset_value}"

        return sql, params

    def _execute(self, for_count=False):
        sql, params = self._build_select(for_count=for_count)
        if self._debug:
            print(f"[SQL] {sql}  params={params}")
        result = self._model_cls._meta.database.query(
            sql, params=params if params else None,
            database=self._model_cls._meta.database_name
        )
        columns = result.get("columns", [])
        rows = result.get("rows", [])
        return columns, rows

    def _eager_load(self, instances):
        if not instances:
            return
        if self._select_related:
            self._load_select_related(instances)
        if self._prefetch_related:
            self._load_prefetch_related(instances)

    def _load_select_related(self, instances):
        for fk_name in self._select_related:
            if fk_name not in self._model_cls._fields:
                continue
            field = self._model_cls._fields[fk_name]
            if not hasattr(field, "ref_model"):
                continue
            ref_model = field.ref_model
            if ref_model is None:
                continue
            fk_col = field.column
            pk_name = ref_model._meta.pk_name
            field_obj = self._model_cls._fields[fk_name]
            fk_values = set()
            for inst in instances:
                val = inst._data.get(fk_name)
                if val is not None:
                    fk_values.add(field_obj.to_db(val))
            if not fk_values:
                continue
            refs = ref_model.where(**{f"{pk_name}__in": list(fk_values)}).all()
            ref_map = {r.pk: r for r in refs}
            for inst in instances:
                val = inst._data.get(fk_name)
                if val is not None:
                    db_val = field_obj.to_db(val)
                    if db_val in ref_map:
                        object.__setattr__(inst, f"_{fk_name}_resolved", ref_map[db_val])

    def _load_prefetch_related(self, instances):
        for rel_name in self._prefetch_related:
            loader_fn = getattr(self._model_cls, f"_prefetch_{rel_name}", None)
            if not loader_fn:
                continue
            pks = [inst.pk for inst in instances if inst.pk is not None]
            related = loader_fn(pks)
            key_attr = f"_prefetch_key_{rel_name}"
            for inst in instances:
                key = getattr(inst, key_attr, inst.pk)
                rel_col = loader_fn.fk_column
                matched = []
                for r in related:
                    r_val = getattr(r, rel_col, None)
                    if rel_col in r._fields:
                        r_val = r._fields[rel_col].to_db(r_val)
                    if r_val == key:
                        matched.append(r)
                object.__setattr__(inst, f"_{rel_name}_cache", matched)

    def all(self):
        """Evaluate the query and return all matching records.

        Returns model instances, dicts (``values``), or tuples
        (``values_list`` / ``pluck``) depending on the query
        configuration.
        """
        if self._values_fields:
            columns, rows = self._execute()
            if self._values_list_flag:
                return [row for row in rows]
            field_indices = {col: idx for idx, col in enumerate(columns)}
            result = []
            for row in rows:
                d = {}
                for f in self._values_fields:
                    field_obj = self._model_cls._fields.get(f)
                    col = field_obj.column if field_obj else f
                    idx = field_indices.get(col)
                    d[f] = row[idx] if idx is not None else None
                result.append(d)
            return result
        columns, rows = self._execute()
        instances = [self._model_cls._from_row(dict(zip(columns, row))) for row in rows]
        self._eager_load(instances)
        return instances

    def first(self):
        """Return the first matching record, or ``None``."""
        qs = self._clone()
        qs._limit_value = 1
        if self._values_fields:
            res = qs.all()
            return res[0] if res else None
        columns, rows = qs._execute()
        if rows:
            inst = self._model_cls._from_row(dict(zip(columns, rows[0])))
            qs._eager_load([inst])
            return inst
        return None

    def count(self):
        """Return the number of matching records."""
        _, rows = self._execute(for_count=True)
        if rows:
            return rows[0][0]
        return 0

    def _aggregate(self, func, field):
        table = self._model_cls._meta.table
        prefix = "DISTINCT " if self._distinct_flag and func.upper() == "COUNT" else ""
        sql = f"SELECT {func}(\"{field}\") as val FROM \"{table}\""
        params = []
        self._resolve_q_objects()
        if self._where_clauses:
            sql += " WHERE " + " AND ".join(self._where_clauses)
            params.extend(self._where_params)
        if self._group_by_fields:
            sql += " GROUP BY \"" + "\", \"".join(self._group_by_fields) + "\""
        if self._having_clauses:
            sql += " HAVING " + " AND ".join(self._having_clauses)
            params.extend(self._having_params)
        if self._debug:
            print(f"[SQL] {sql}  params={params}")
        result = self._model_cls._meta.database.query(
            sql, params=params if params else None,
            database=self._model_cls._meta.database_name
        )
        rows = result.get("rows", [])
        if rows:
            return rows[0][0]
        return None

    def sum(self, field):
        """Return the SUM of *field* over the query."""
        return self._aggregate("SUM", field)

    def avg(self, field):
        """Return the AVG of *field* over the query."""
        return self._aggregate("AVG", field)

    def min(self, field):
        """Return the MIN of *field* over the query."""
        return self._aggregate("MIN", field)

    def max(self, field):
        """Return the MAX of *field* over the query."""
        return self._aggregate("MAX", field)

    def exists(self):
        """Return ``True`` if at least one matching record exists."""
        qs = self._clone()
        qs._limit_value = 1
        _, rows = qs._execute()
        return len(rows) > 0

    def paginate(self, page=1, per_page=20):
        """Return a page of results with metadata.

        Parameters
        ----------
        page : int
            1-indexed page number.
        per_page : int
            Records per page.

        Returns
        -------
        dict
            ``{items, page, per_page, total, pages, has_next, has_prev}``
        """
        if not isinstance(page, int) or page < 1:
            raise ValueError(f"page must be a positive integer, got {page!r}")
        if not isinstance(per_page, int) or per_page < 1:
            raise ValueError(f"per_page must be a positive integer, got {per_page!r}")
        total = self.count()
        pages = max(1, -(-total // per_page))
        qs = self._clone()
        qs._offset_value = (page - 1) * per_page
        qs._limit_value = per_page
        items = qs.all()
        return {
            "items": items,
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": pages,
            "has_next": page < pages,
            "has_prev": page > 1,
        }

    def delete(self):
        """Delete all records matching the query."""
        table = self._model_cls._meta.table
        sql = f"DELETE FROM \"{table}\""
        params = []
        self._resolve_q_objects()
        if self._where_clauses:
            sql += " WHERE " + " AND ".join(self._where_clauses)
            params.extend(self._where_params)
        if self._debug:
            print(f"[SQL] {sql}  params={params}")
        self._model_cls._meta.database.query(
            sql, params=params if params else None,
            database=self._model_cls._meta.database_name
        )

    def update(self, **kwargs):
        """Update all records matching the query.

        Parameters
        ----------
        **kwargs
            Field name to new value mappings.  Accepts ``F``
            expressions for server-side evaluation.
        """
        table = self._model_cls._meta.table
        sets = []
        params = []
        for col, val in kwargs.items():
            if isinstance(val, (F, _FExpr)):
                sets.append(f"\"{col}\" = {_render_fexpr(val)}")
            else:
                sets.append(f"\"{col}\" = ?")
                params.append(val)
        if not sets:
            return
        sql = f"UPDATE \"{table}\" SET " + ", ".join(sets)
        self._resolve_q_objects()
        if self._where_clauses:
            sql += " WHERE " + " AND ".join(self._where_clauses)
            params.extend(self._where_params)
        if self._debug:
            print(f"[SQL] {sql}  params={params}")
        self._model_cls._meta.database.query(
            sql, params=params if params else None,
            database=self._model_cls._meta.database_name
        )

    def __iter__(self):
        return iter(self.all())

    def __getitem__(self, index):
        if isinstance(index, slice):
            qs = self._clone()
            if index.start is not None and index.start < 0:
                raise IndexError("negative slice start not supported")
            if index.stop is not None and index.stop < 0:
                raise IndexError("negative slice stop not supported")
            if index.step is not None and index.step != 1:
                raise IndexError("slice step must be 1")
            if index.start is not None:
                qs._offset_value = index.start
            if index.stop is not None:
                if index.start is not None and index.stop < index.start:
                    return []
                qs._limit_value = index.stop - (index.start or 0)
            return qs.all()
        if not isinstance(index, int) or index < 0:
            raise IndexError("index must be a non-negative integer")
        qs = self._clone()
        qs._limit_value = 1
        qs._offset_value = index
        items = qs.all()
        return items[0] if items else None

    def __repr__(self):
        return f"<QuerySet model={self._model_cls.__name__}>"
