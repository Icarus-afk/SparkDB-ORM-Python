# SparkDB ORM

A lightweight Python ORM supporting DB-API 2.0 (sqlite3, PostgreSQL, MySQL) and SparkDB HTTP backends. Features model definitions, a Django-inspired query builder, relationship management, and schema migrations — all with zero required dependencies.

## Installation

```bash
pip install sparkdb-orm
```

For the SparkDB HTTP backend:

```bash
pip install sparkdb-orm[sparkdb]
```

## Quick Start (sqlite3)

```python
import sqlite3
from sparkdb import Model, DBAPI2Backend
from sparkdb.fields import String, Integer, Boolean

conn = sqlite3.connect(":memory:")
db = DBAPI2Backend(conn)

class User(Model):
    name = String(max_length=100)
    age = Integer()
    active = Boolean(default=True)

    class Meta:
        database = db

User.create_table()
user = User.create(name="Alice", age=30)
print(user.name)  # Alice
```

## Fields

All field types support common keyword arguments:

| Argument    | Description |
|-------------|-------------|
| `column`    | Custom column name in the database (defaults to field name) |
| `default`   | Default value for the field |
| `nullable` / `null` | Whether the field can be `None` (default `True`) |
| `unique`    | Add a UNIQUE constraint |
| `primary_key` | Mark as primary key |
| `index`     | Create an index for this field |

### Field Types

| Field     | SQL Type     | Python Type         |
|-----------|-------------|----------------------|
| `String(max_length)` | `VARCHAR(n)` | `str` |
| `Text`    | `TEXT`       | `str` |
| `Integer` | `INTEGER`    | `int` |
| `Float`   | `REAL`       | `float` |
| `Boolean` | `INTEGER`    | `bool` (accepts `0`/`1`) |
| `DateTime` | `TEXT`      | `datetime` (ISO format) |
| `JSON`    | `TEXT`       | `dict`/`list` (serialized) |

### Auto-increment

```python
class Article(Model):
    id = Integer(primary_key=True, auto_increment=True)
    ...
    class Meta:
        database = db
```

If no field has `primary_key=True`, an auto-increment `id` field is added automatically.

## Models

### CRUD

```python
# Create
user = User.create(name="Bob", age=25)

# Read
user = User.find(1)                         # by PK
users = User.where(active=True).all()       # filtered
first = User.where(name="Alice").first()    # single result

# Update
user.name = "Robert"
user.save()

# Delete
user.delete()
```

### Bulk Operations

```python
# Bulk create
User.bulk_create([
    {"name": "a", "age": 20},
    {"name": "b", "age": 30},
])

# Bulk update
User.update(active=False)
User.where(age__lt=18).update(active=False)
```

### Hooks

```python
class User(Model):
    name = String(max_length=100)
    class Meta:
        database = db

    def before_save(self): ...
    def after_save(self): ...
    def before_delete(self): ...
    def after_delete(self): ...
```

### Meta Options

| Option | Description |
|--------|-------------|
| `database` | Backend instance (required) |
| `table` | Custom table name (defaults to `classname + "s"`) |
| `database_name` | Database name for SparkDB backend (default `"main"`) |
| `timestamps` | Auto-manage `created_at`/`updated_at` |
| `ordering` | Default ordering, e.g. `["name", "-created_at"]` |
| `unique_together` | List of column tuples, e.g. `[("email", "tenant_id")]` |
| `indexes` | List of index dicts, e.g. `[{"fields": ["email"]}]` |

### `to_dict()` and `to_json()`

```python
d = user.to_dict()                         # all fields
d = user.to_dict(include=["name", "age"])  # subset only
d = user.to_dict(exclude=["age"])          # exclude specific
j = user.to_json(indent=2)                 # formatted JSON
```

### `get_or_create` / `update_or_create`

```python
user, created = User.get_or_create(defaults={"age": 20}, name="Alice")
user, created = User.update_or_create(defaults={"age": 21}, name="Alice")
```

## QuerySet

### WHERE Lookups

```python
qs = User.where(age__gt=18)                # >
qs = User.where(age__gte=18)               # >=
qs = User.where(age__lt=30)                # <
qs = User.where(age__lte=30)               # <=
qs = User.where(name__ne="Alice")          # !=
qs = User.where(name__contains="li")       # LIKE '%li%'
qs = User.where(name__startswith="A")      # LIKE 'A%'
qs = User.where(name__endswith="ce")       # LIKE '%ce'
qs = User.where(age__in=[20, 30, 40])      # IN (...)
qs = User.where(age__not_in=[20, 30])      # NOT IN (...)
qs = User.where(name__isnull=True)         # IS NULL
qs = User.where(name__isnull=False)        # IS NOT NULL
qs = User.where(active=True)               # exact match
```

### Chaining & Immutability

All QuerySet methods return a new clone — the original is never mutated:

```python
qs1 = User.where(active=True)
qs2 = qs1.where(age__gt=18)      # qs1 is unchanged
qs3 = qs2.order_by("-age")        # both previous are unchanged
```

### Ordering

```python
User.order_by("name")             # ascending
User.order_by("-name")            # descending
User.order_by("age", "-name")     # multiple fields
```

Explicit `order_by()` replaces any `Meta.ordering`.

### Limit / Offset / Slicing

```python
User.limit(10).all()
User.offset(5).all()
User.limit(10).offset(5).all()
User[3:8]                         # slice → offset=3, limit=5
User[0]                           # single item → first()
```

### Aggregation

```python
User.sum("age")
User.avg("age")
User.min("age")
User.max("age")
User.count()                      # with current filters
```

### Distinct / Group By / Having

```python
User.distinct().values("category").all()
User.group_by("category").values("category").all()
User.group_by("category").having("COUNT(*) > ?", 1).all()
```

### Pagination

```python
page = User.paginate(page=1, per_page=20)
# → {"items": [...], "page": 1, "per_page": 20,
#    "total": 100, "pages": 5,
#    "has_next": True, "has_prev": False}
```

### Values / Values List / Pluck

```python
User.values("name", "age").all()         # list of dicts
User.values_list("name", "age").all()    # list of tuples
User.pluck("name").all()                 # list of single values
```

### F Expressions (server-side evaluation)

```python
from sparkdb.expressions import F

# In WHERE
User.where(age=F("graduation_year") - 2000).all()

# In UPDATE
User.update(counter=F("counter") + 1)
```

### Q Objects (complex logic)

```python
from sparkdb.expressions import Q

# OR
User.where(Q(name="Alice") | Q(name="Bob")).all()

# NOT
User.where(~Q(name="Alice")).all()

# Combined
User.where((Q(name="Alice") | Q(name="Bob")) & Q(age=30)).all()
```

### Raw WHERE

```python
User.where_raw("\"age\" > ?", 18).all()
```

### Exists

```python
User.where(name="Alice").exists()
```

### Delete via QuerySet

```python
User.where(active=False).delete()
```

## Relationships

### ForeignKey

```python
from sparkdb import Model
from sparkdb.fields import String
from sparkdb.relationship import ForeignKey, has_many

class Author(Model):
    name = String(max_length=100)
    class Meta:
        database = db

class Book(Model):
    title = String(max_length=100)
    author = ForeignKey(Author, nullable=True)
    class Meta:
        database = db

# Lazy load
book = Book.where(title="Book 1").first()
print(book.author.name)  # triggers a query

# Eager load
book = Book.select_related("author").where(title="Book 1").first()
```

### has_many (reverse relation)

```python
has_many(Book, name="books", fk_column="author")(Author)

author = Author.where(name="Alice").first()
for book in author.books:
    print(book.title)
```

## Backends

### DBAPI2Backend

Wraps any PEP 249 (DB-API 2.0) connection. No extra dependencies required.

```python
import sqlite3
from sparkdb import DBAPI2Backend

conn = sqlite3.connect(":memory:")
db = DBAPI2Backend(conn)
```

Works with any DB-API 2.0 driver: `psycopg2` (PostgreSQL), `mysql-connector-python` (MySQL), etc.

### SparkDBBackend

Wraps the SparkDB HTTP client for use with a SparkDB server.

```python
from sparkdb import SparkDB, SparkDBBackend

client = SparkDB(url="http://localhost:9600")
client._login("admin", "admin")
db = SparkDBBackend(client)
```

### Custom Backend

Implement the `DatabaseBackend` protocol:

```python
from sparkdb import DatabaseBackend

class MyBackend(DatabaseBackend):
    def query(self, sql, params=None, database="main"):
        # return {"columns": [...], "rows": [[...], ...]}
        ...
```

## Migrations

```python
from sparkdb import Migrator

migrator = Migrator(db)

# Add missing columns
migrator.auto_migrate(User, Article)

# Versioned migrations
migrator.migrate(1, [
    'ALTER TABLE users ADD COLUMN "email" TEXT',
])
```

## Development

```bash
# Install in editable mode
pip install -e .

# Run tests
python tests/test_orm.py -v
```
