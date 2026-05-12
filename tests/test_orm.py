#!/usr/bin/env python3
import json
import os
import sqlite3
import sys
import unittest
from datetime import date, datetime, time, timezone
from decimal import Decimal as _Decimal
from uuid import UUID as _UUID

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sparkdb import Model, DBAPI2Backend, SparkDBBackend
from sparkdb.backends import DatabaseBackend
from sparkdb.exceptions import *
from sparkdb.fields import (
    String, Integer, Float, Boolean, DateTime, JSON, Text, Field,
    BLOB, Date, Time, Decimal, UUID
)
from sparkdb.expressions import Q, F, _parse_where_key, _build_where, _WHERE_OPS
from sparkdb.relationship import ForeignKey, has_many, LazyRelation
from sparkdb.migration import Migrator
from sparkdb.query import QuerySet


TABLE_COUNTER = 0


def unique_table(base):
    global TABLE_COUNTER
    TABLE_COUNTER += 1
    return f"{base}_{TABLE_COUNTER}"


def memory_db():
    return DBAPI2Backend(sqlite3.connect(":memory:"))


class TestFieldValidation(unittest.TestCase):

    def test_string_validation(self):
        f = String(max_length=5)
        f._name = "name"
        f.validate("hello")
        with self.assertRaises(ValidationError):
            f.validate("toolong")
        with self.assertRaises(ValidationError):
            f.validate(123)
        f.validate(None)
        f.nullable = False
        with self.assertRaises(ValidationError):
            f.validate(None)

    def test_text_validation(self):
        f = Text()
        f._name = "content"
        f.validate("any string")
        f.validate("x" * 10000)
        with self.assertRaises(ValidationError):
            f.validate(123)
        f.validate(None)

    def test_integer_validation(self):
        f = Integer()
        f._name = "age"
        f.validate(42)
        f.validate(0)
        f.validate(-1)
        with self.assertRaises(ValidationError):
            f.validate(3.14)
        with self.assertRaises(ValidationError):
            f.validate("5")
        with self.assertRaises(ValidationError):
            f.validate(True)
        f.validate(None)
        f.nullable = False
        with self.assertRaises(ValidationError):
            f.validate(None)

    def test_float_validation(self):
        f = Float()
        f._name = "price"
        f.validate(3.14)
        f.validate(42)
        with self.assertRaises(ValidationError):
            f.validate("5.5")
        with self.assertRaises(ValidationError):
            f.validate(True)
        f.validate(None)

    def test_boolean_validation(self):
        f = Boolean()
        f._name = "active"
        f.validate(True)
        f.validate(False)
        f.validate(0)
        f.validate(1)
        with self.assertRaises(ValidationError):
            f.validate("true")
        with self.assertRaises(ValidationError):
            f.validate(5)
        f.validate(None)

    def test_datetime_validation(self):
        f = DateTime()
        f._name = "created"
        f.validate(datetime.now())
        with self.assertRaises(ValidationError):
            f.validate("2024-01-01")
        with self.assertRaises(ValidationError):
            f.validate(12345)
        f.validate(None)

    def test_json_validation(self):
        f = JSON()
        f._name = "data"
        f.validate({"key": "value"})
        f.validate([1, 2, 3])
        f.validate("string")
        f.validate(42)
        f.validate(None)
        with self.assertRaises(ValidationError):
            f.validate(object())
        f.nullable = False
        with self.assertRaises(ValidationError):
            f.validate(None)

    def test_field_defaults_applied(self):
        db = memory_db()
        tbl = unique_table("test_defaults")

        class TestDefaults(Model):
            name = String(default="anonymous")
            age = Integer(default=18)
            active = Boolean(default=True)
            score = Float(default=0.0)

            class Meta:
                database = db
                database_name = "main"
                table = tbl

        TestDefaults.create_table()
        inst = TestDefaults()
        self.assertEqual(inst._data["name"], "anonymous")
        self.assertEqual(inst._data["age"], 18)
        self.assertEqual(inst._data["active"], True)
        self.assertEqual(inst._data["score"], 0.0)
        inst.save()
        reloaded = TestDefaults.find(inst.pk)
        self.assertEqual(reloaded._data["name"], "anonymous")
        self.assertEqual(reloaded._data["age"], 18)

    def test_null_keyword(self):
        f = String(null=False)
        self.assertFalse(f.nullable)
        f2 = String(null=True)
        self.assertTrue(f2.nullable)


class TestModelEdgeCases(unittest.TestCase):

    def setUp(self):
        self.db = memory_db()
        self.tbl = unique_table("test_item")

        class TestItem(Model):
            name = String(max_length=100)
            value = Integer(nullable=True)
            active = Boolean(default=True)

            class Meta:
                database = self.db
                database_name = "main"
                table = self.tbl
                timestamps = True

        self.TestItem = TestItem
        TestItem.create_table()

    def test_create_and_find(self):
        item = self.TestItem.create(name="test", value=42)
        self.assertIsNotNone(item.pk)
        self.assertTrue(item._exists)
        self.assertTrue(item._saved)
        found = self.TestItem.find(item.pk)
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "test")
        self.assertEqual(found.value, 42)

    def test_save_update(self):
        item = self.TestItem(name="first", value=10)
        item.save()
        pk = item.pk
        item.name = "updated"
        item.save()
        found = self.TestItem.find(pk)
        self.assertEqual(found.name, "updated")

    def test_delete(self):
        item = self.TestItem.create(name="delete_me")
        item.delete()
        self.assertFalse(item._exists)
        found = self.TestItem.find(item.pk)
        self.assertIsNone(found)

    def test_reload_deleted_object(self):
        item = self.TestItem.create(name="reload_test")
        pk = item.pk
        self.TestItem.find(pk).delete()
        item.reload()
        self.assertFalse(item._exists)

    def test_reload_none_pk(self):
        item = self.TestItem()
        item.reload()

    def test_bulk_create_returns_instances(self):
        items = self.TestItem.bulk_create([
            {"name": "a", "value": 1},
            {"name": "b", "value": 2},
            {"name": "c", "value": 3},
        ])
        self.assertEqual(len(items), 3)
        for inst in items:
            self.assertIsInstance(inst, Model)
            self.assertTrue(inst._exists)

    def test_bulk_create_empty(self):
        result = self.TestItem.bulk_create([])
        self.assertEqual(result, [])

    def test_bulk_create_with_instances(self):
        inst = self.TestItem(name="premade", value=99)
        items = self.TestItem.bulk_create([inst, {"name": "dict", "value": 1}])
        self.assertEqual(len(items), 2)
        self.assertIs(items[0], inst)

    def test_get_or_create_existing(self):
        self.TestItem.create(name="existing", value=5)
        item, created = self.TestItem.get_or_create(defaults={"value": 5}, name="existing")
        self.assertFalse(created)

    def test_get_or_create_new(self):
        item, created = self.TestItem.get_or_create(defaults={"value": 99}, name="brand_new")
        self.assertTrue(created)
        self.assertEqual(item.value, 99)

    def test_update_or_create_existing(self):
        self.TestItem.create(name="to_update", value=1)
        item, created = self.TestItem.update_or_create(defaults={"value": 2}, name="to_update")
        self.assertFalse(created)
        self.assertEqual(item.value, 2)

    def test_update_or_create_new(self):
        item, created = self.TestItem.update_or_create(defaults={"value": 10}, name="new_one")
        self.assertTrue(created)
        self.assertEqual(item.value, 10)

    def test_save_no_changes(self):
        item = self.TestItem.create(name="nochange")
        item.save()

    def test_delete_no_pk(self):
        item = self.TestItem()
        item.delete()

    def test_to_dict_includes_timestamps(self):
        item = self.TestItem.create(name="ts_test")
        d = item.to_dict()
        self.assertIn("created_at", d)
        self.assertIn("updated_at", d)

    def test_to_dict_exclude(self):
        item = self.TestItem.create(name="filter")
        d = item.to_dict(exclude=["value"])
        self.assertIn("name", d)
        self.assertNotIn("value", d)

    def test_to_dict_include(self):
        item = self.TestItem.create(name="include_test")
        d = item.to_dict(include=["name"])
        self.assertEqual(set(d.keys()), {"name"})

    def test_to_json(self):
        item = self.TestItem.create(name="json_test")
        j = item.to_json()
        parsed = json.loads(j)
        self.assertEqual(parsed["name"], "json_test")

    def test_repr(self):
        item = self.TestItem.create(name="repr_test")
        r = repr(item)
        self.assertIn("repr_test", r)
        self.assertIn(str(item.pk), r)

    def test_hooks(self):
        calls = []
        tbl = unique_table("hooked")

        class Hooked(Model):
            name = String(max_length=100)

            class Meta:
                database = self.db
                database_name = "main"
                table = tbl

            def before_save(self):
                calls.append("before_save")

            def after_save(self):
                calls.append("after_save")

            def before_delete(self):
                calls.append("before_delete")

            def after_delete(self):
                calls.append("after_delete")

        Hooked.create_table()
        item = Hooked.create(name="hook_test")
        self.assertEqual(calls, ["before_save", "after_save"])
        item.delete()
        self.assertEqual(calls, ["before_save", "after_save", "before_delete", "after_delete"])

    def test_nullable_enforcement(self):
        tbl = unique_table("strict")

        class Strict(Model):
            name = String(max_length=100, nullable=False)

            class Meta:
                database = self.db
                database_name = "main"
                table = tbl

        Strict.create_table()
        with self.assertRaises(ValidationError):
            Strict(name=None).save()

    def test_unknown_field(self):
        with self.assertRaises(ValidationError):
            self.TestItem(nonexistent=42)

    def test_all_field_types(self):
        tbl = unique_table("alltypes")

        class AllTypes(Model):
            str_field = String(max_length=100)
            int_field = Integer()
            float_field = Float()
            bool_field = Boolean()
            dt_field = DateTime(nullable=True)
            json_field = JSON(nullable=True)
            txt_field = Text(nullable=True)

            class Meta:
                database = self.db
                database_name = "main"
                table = tbl

        AllTypes.create_table()
        now = datetime.now(timezone.utc)
        inst = AllTypes.create(
            str_field="hello",
            int_field=42,
            float_field=3.14,
            bool_field=True,
            dt_field=now,
            json_field={"nested": {"key": [1, 2, 3]}},
            txt_field="long text",
        )
        found = AllTypes.find(inst.pk)
        self.assertEqual(found.str_field, "hello")
        self.assertEqual(found.int_field, 42)
        self.assertAlmostEqual(found.float_field, 3.14)
        self.assertEqual(found.bool_field, True)
        if found.dt_field is not None:
            self.assertIsInstance(found.dt_field, datetime)
        self.assertEqual(found.json_field, {"nested": {"key": [1, 2, 3]}})
        self.assertEqual(found.txt_field, "long text")

    def test_bulk_update(self):
        self.TestItem.create(name="a", value=1)
        self.TestItem.create(name="b", value=2)
        self.TestItem.update(value=99)
        r = self.TestItem.where(value=99).all()
        self.assertEqual(len(r), 2)


class TestQuerySetEdgeCases(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db = memory_db()
        cls.tbl = unique_table("qitem")

        class QItem(Model):
            name = String(max_length=100)
            category = String(max_length=50, nullable=True)
            value = Integer()
            price = Float(default=0.0)
            active = Boolean(default=True)

            class Meta:
                database = cls.db
                database_name = "main"
                table = cls.tbl
                ordering = ["name"]

        cls.QItem = QItem
        QItem.create_table()
        QItem.bulk_create([
            {"name": "alpha", "category": "A", "value": 10, "price": 1.5, "active": True},
            {"name": "beta", "category": "B", "value": 20, "price": 2.5, "active": True},
            {"name": "gamma", "category": "A", "value": 30, "price": 3.5, "active": False},
            {"name": "delta", "category": "C", "value": 40, "price": 4.5, "active": True},
            {"name": "epsilon", "category": None, "value": 50, "price": 5.5, "active": False},
        ])

    def qs(self):
        return self.QItem.where()

    def test_all(self):
        results = self.QItem.all()
        self.assertEqual(len(results), 5)

    def test_where_exact(self):
        r = self.qs().where(name="alpha").all()
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0].value, 10)

    def test_where_gt(self):
        r = self.qs().where(value__gt=30).all()
        self.assertEqual(len(r), 2)

    def test_where_gte(self):
        r = self.qs().where(value__gte=30).all()
        self.assertEqual(len(r), 3)

    def test_where_lt(self):
        r = self.qs().where(value__lt=20).all()
        self.assertEqual(len(r), 1)

    def test_where_lte(self):
        r = self.qs().where(value__lte=20).all()
        self.assertEqual(len(r), 2)

    def test_where_ne(self):
        r = self.qs().where(name__ne="alpha").all()
        self.assertEqual(len(r), 4)

    def test_where_contains(self):
        r = self.qs().where(name__contains="ph").all()
        self.assertEqual(len(r), 1)

    def test_where_startswith(self):
        r = self.qs().where(name__startswith="al").all()
        self.assertEqual(len(r), 1)

    def test_where_endswith(self):
        r = self.qs().where(name__endswith="ma").all()
        self.assertEqual(len(r), 1)

    def test_where_in(self):
        r = self.qs().where(name__in=["alpha", "beta"]).all()
        self.assertEqual(len(r), 2)

    def test_where_in_empty(self):
        r = self.qs().where(name__in=[]).all()
        self.assertEqual(len(r), 0)

    def test_where_not_in(self):
        r = self.qs().where(name__not_in=["alpha", "beta"]).all()
        self.assertEqual(len(r), 3)

    def test_where_isnull(self):
        r = self.qs().where(category__isnull=True).all()
        self.assertEqual(len(r), 1)

    def test_where_isnull_false(self):
        r = self.qs().where(category__isnull=False).all()
        self.assertEqual(len(r), 4)

    def test_where_multiple(self):
        r = self.qs().where(category="A", value__gt=10).all()
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0].name, "gamma")

    def test_order_by_override(self):
        r = self.qs().order_by("-value").all()
        values = [x.value for x in r]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_limit(self):
        r = self.qs().limit(2).all()
        self.assertEqual(len(r), 2)

    def test_limit_zero(self):
        r = self.qs().limit(0).all()
        self.assertEqual(len(r), 0)

    def test_offset(self):
        r = self.qs().offset(2).all()
        self.assertEqual(len(r), 3)

    def test_limit_negative(self):
        with self.assertRaises(ValueError):
            self.qs().limit(-1)

    def test_offset_negative(self):
        with self.assertRaises(ValueError):
            self.qs().offset(-5)

    def test_slice(self):
        r = self.qs()[1:3]
        self.assertEqual(len(r), 2)

    def test_slice_start_only(self):
        r = self.qs()[3:]
        self.assertEqual(len(r), 2)

    def test_slice_stop_less_than_start(self):
        r = self.qs()[3:1]
        self.assertEqual(len(r), 0)

    def test_slice_negative_start(self):
        with self.assertRaises(IndexError):
            self.qs()[-1:2]

    def test_slice_step_not_one(self):
        with self.assertRaises(IndexError):
            self.qs()[::2]

    def test_getitem_index(self):
        item = self.qs()[0]
        self.assertIsNotNone(item)

    def test_getitem_index_negative(self):
        with self.assertRaises(IndexError):
            self.qs()[-1]

    def test_values(self):
        r = self.qs().values("name", "value").all()
        self.assertEqual(len(r), 5)
        self.assertIn("name", r[0])
        self.assertIn("value", r[0])

    def test_values_empty_raises(self):
        with self.assertRaises(ValueError):
            self.qs().values()

    def test_values_list(self):
        r = self.qs().values_list("name").all()
        self.assertEqual(len(r), 5)

    def test_values_list_empty_raises(self):
        with self.assertRaises(ValueError):
            self.qs().values_list()

    def test_pluck(self):
        r = self.qs().pluck("name").all()
        self.assertEqual(len(r), 5)

    def test_first(self):
        item = self.qs().first()
        self.assertIsNotNone(item)

    def test_first_empty(self):
        item = self.qs().where(name="nonexistent").first()
        self.assertIsNone(item)

    def test_count(self):
        c = self.qs().count()
        self.assertEqual(c, 5)

    def test_count_filtered(self):
        c = self.qs().where(active=True).count()
        self.assertEqual(c, 3)

    def test_exists(self):
        self.assertTrue(self.qs().where(name="alpha").exists())
        self.assertFalse(self.qs().where(name="nonexistent").exists())

    def test_distinct(self):
        r = self.qs().distinct().values("category").all()
        self.assertGreater(len(r), 0)

    def test_sum(self):
        total = self.qs().sum("value")
        self.assertEqual(total, 150)

    def test_sum_filtered(self):
        total = self.qs().where(active=True).sum("value")
        self.assertEqual(total, 70)

    def test_avg(self):
        avg = self.qs().avg("value")
        self.assertEqual(avg, 30.0)

    def test_min(self):
        m = self.qs().min("value")
        self.assertEqual(m, 10)

    def test_max(self):
        m = self.qs().max("value")
        self.assertEqual(m, 50)

    def test_paginate(self):
        result = self.qs().paginate(page=1, per_page=2)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["pages"], 3)
        self.assertTrue(result["has_next"])
        self.assertFalse(result["has_prev"])

    def test_paginate_page_zero(self):
        with self.assertRaises(ValueError):
            self.qs().paginate(page=0)

    def test_paginate_per_page_zero(self):
        with self.assertRaises(ValueError):
            self.qs().paginate(per_page=0)

    def test_paginate_last_page(self):
        result = self.qs().paginate(page=3, per_page=2)
        self.assertFalse(result["has_next"])
        self.assertTrue(result["has_prev"])

    def test_debug_does_not_mutate_original(self):
        qs = self.qs()
        qs2 = qs.debug()
        self.assertFalse(qs._debug)
        self.assertTrue(qs2._debug)

    def test_where_raw(self):
        r = self.qs().where_raw("\"value\" > ?", 30).all()
        self.assertEqual(len(r), 2)

    def test_group_by(self):
        r = self.qs().group_by("category").values("category").all()
        self.assertGreater(len(r), 0)

    def test_having_string(self):
        r = self.qs().group_by("category").having("COUNT(*) > ?", 1).values("category").all()
        self.assertGreaterEqual(len(r), 0)

    def test_chained_immutability(self):
        qs1 = self.qs().where(active=True)
        qs2 = qs1.where(value__gt=10)
        qs3 = qs2.order_by("-value")
        self.assertEqual(len(qs1.all()), 3)
        self.assertEqual(len(qs2.all()), 2)
        self.assertEqual(len(qs3.all()), 2)

    def test_order_by_clears_meta_ordering(self):
        qs = self.qs().order_by("value")
        self.assertEqual(qs._order_by_fields, ["value"])

    def test_iter(self):
        count = sum(1 for _ in self.qs().all())
        self.assertEqual(count, 5)

    def test_where_no_args(self):
        r = self.qs().all()
        self.assertEqual(len(r), 5)

    def test_qs_update(self):
        item = self.QItem.where(name="beta").first()
        old_val = item.value
        self.qs().where(name="beta").update(value=999)
        item = self.QItem.where(name="beta").first()
        self.assertEqual(item.value, 999)
        self.qs().where(name="beta").update(value=old_val)


class TestQExpressions(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db = memory_db()
        cls.tbl = unique_table("qitem2")

        class QItem(Model):
            name = String(max_length=100)
            value = Integer()

            class Meta:
                database = cls.db
                database_name = "main"
                table = cls.tbl

        cls.QItem = QItem
        QItem.create_table()
        QItem.bulk_create([
            {"name": "a", "value": 1},
            {"name": "b", "value": 2},
            {"name": "c", "value": 3},
            {"name": "d", "value": 4},
            {"name": "e", "value": 5},
        ])

    def qs(self):
        return self.QItem.where()

    def test_q_or(self):
        r = self.qs().where(Q(name="a") | Q(name="b")).all()
        self.assertEqual(len(r), 2)

    def test_q_and(self):
        r = self.qs().where(Q(name="a", value=1)).all()
        self.assertEqual(len(r), 1)

    def test_q_combined(self):
        r = self.qs().where((Q(name="a") | Q(name="b")) & Q(value=1)).all()
        self.assertEqual(len(r), 1)

    def test_q_not(self):
        r = self.qs().where(~Q(name="a")).all()
        self.assertEqual(len(r), 4)

    def test_q_complex(self):
        r = self.qs().where(
            (Q(name="a") | Q(name="b")) & (Q(value=1) | Q(value=2))
        ).all()
        self.assertEqual(len(r), 2)

    def test_q_invalid_type(self):
        with self.assertRaises(TypeError):
            Q(name="a") | "not a Q"


class TestFExpressions(unittest.TestCase):

    def setUp(self):
        self.db = memory_db()
        self.tbl = unique_table("fitem")

        class FItem(Model):
            name = String(max_length=100)
            value = Integer()
            counter = Integer(default=0)

            class Meta:
                database = self.db
                database_name = "main"
                table = self.tbl

        self.FItem = FItem
        FItem.create_table()
        FItem.bulk_create([
            {"name": "x", "value": 10},
            {"name": "y", "value": 20},
            {"name": "z", "value": 30},
        ])

    def qs(self):
        return self.FItem.where()

    def test_f_in_where(self):
        r = self.qs().where(value=F("counter")).all()
        self.assertIsInstance(r, list)

    def test_f_update_add(self):
        self.qs().update(value=F("value") + 5)
        r = self.qs().where(name="x").first()
        self.assertEqual(r.value, 15)

    def test_f_update_mul(self):
        self.qs().update(value=F("value") * 2)
        r = self.qs().where(name="y").first()
        self.assertEqual(r.value, 40)

    def test_f_update_sub(self):
        self.qs().update(value=F("value") - 10)
        r = self.qs().where(name="z").first()
        self.assertEqual(r.value, 20)


class TestRelationships(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db = memory_db()
        cls.author_tbl = unique_table("author")
        cls.book_tbl = unique_table("book")

        class Author(Model):
            name = String(max_length=100)

            class Meta:
                database = cls.db
                database_name = "main"
                table = cls.author_tbl

        class Book(Model):
            title = String(max_length=100)
            author = ForeignKey(Author, nullable=True)

            class Meta:
                database = cls.db
                database_name = "main"
                table = cls.book_tbl

        cls.Author = Author
        cls.Book = Book
        Author.create_table()
        Book.create_table()

        a1 = Author.create(name="Alice")
        a2 = Author.create(name="Bob")
        Book.bulk_create([
            {"title": "Book 1", "author": a1.pk},
            {"title": "Book 2", "author": a1.pk},
            {"title": "Book 3", "author": a2.pk},
        ])

        has_many(Book, name="books", fk_column="author")(Author)
        cls.Author = Author

    def qs(self):
        return self.Book.where()

    def test_foreign_key_lazy_load(self):
        book = self.qs().where(title="Book 1").first()
        author = book.author
        self.assertEqual(author.name, "Alice")

    def test_select_related(self):
        book = self.qs().select_related("author").where(title="Book 1").first()
        self.assertEqual(book.author.name, "Alice")

    def test_reverse_relation(self):
        author = self.Author.where(name="Alice").first()
        books = author.books
        self.assertEqual(len(books), 2)

    def test_foreign_key_none(self):
        orphan = self.Book.create(title="Orphan")
        self.assertIsNone(orphan.author)

    def test_reverse_relation_unsaved(self):
        author = self.Author()
        books = author.books
        self.assertEqual(books, [])

    def test_reverse_relation_class_access(self):
        from sparkdb.relationship import ReverseRelation
        desc = self.Author.books
        self.assertIsInstance(desc, ReverseRelation)


class TestHasManyDecorator(unittest.TestCase):

    def test_has_many_decorator(self):
        db = memory_db()
        parent_tbl = unique_table("parent")
        child_tbl = unique_table("child")

        class Parent(Model):
            name = String(max_length=50)

            class Meta:
                database = db
                database_name = "main"
                table = parent_tbl

        class Child(Model):
            name = String(max_length=50)
            parent = ForeignKey(Parent, nullable=True)

            class Meta:
                database = db
                database_name = "main"
                table = child_tbl

        Parent.create_table()
        Child.create_table()

        p = Parent.create(name="parent1")
        Child.create(name="child1", parent=p.pk)
        Child.create(name="child2", parent=p.pk)

        has_many(Child, name="children", fk_column="parent")(Parent)
        children = p.children
        self.assertEqual(len(children), 2)

    def test_has_many_default_fk_column(self):
        db = memory_db()
        parent_tbl = unique_table("parent2")
        child_tbl = unique_table("child2")

        class Parent(Model):
            name = String(max_length=50)

            class Meta:
                database = db
                table = parent_tbl

        class Child(Model):
            name = String(max_length=50)
            parent_id = ForeignKey(Parent, nullable=True, column="parent_id")

            class Meta:
                database = db
                table = child_tbl

        Parent.create_table()
        Child.create_table()
        p = Parent.create(name="p")
        has_many(Child, fk_column="parent_id")(Parent)
        rel_name = child_tbl + "_set"
        children = getattr(p, rel_name)
        self.assertEqual(len(children), 0)

    def test_has_many_default_name(self):
        db = memory_db()
        tbl = unique_table("ps")
        class P(Model):
            name = String(max_length=50)
            class Meta:
                database = db
                table = tbl
        class C(Model):
            name = String(max_length=50)
            p = ForeignKey(P, nullable=True)
            class Meta:
                database = db
                table = unique_table("cs")
        P.create_table()
        C.create_table()
        has_many(C)(P)
        default_name = C._meta.table + "_set"
        self.assertTrue(hasattr(P, default_name))


class TestBackend(unittest.TestCase):

    def test_dbapi2_backend_query_select(self):
        db = memory_db()
        result = db.query("SELECT 1 as val")
        self.assertEqual(result["columns"], ["val"])
        self.assertEqual(result["rows"], [(1,)])

    def test_dbapi2_backend_query_insert(self):
        db = memory_db()
        db.query("CREATE TABLE t (x INTEGER)")
        result = db.query("INSERT INTO t VALUES (42)")
        self.assertIn("rows", result)

    def test_dbapi2_backend_error(self):
        db = memory_db()
        with self.assertRaises(Exception):
            db.query("SELECT x FROM nonexistent")


# ====================================================================
# Additional coverage — backends, migration, expressions, relationships
# ====================================================================


class TestBackendCoverage(unittest.TestCase):
    """Covers missing backend paths."""

    def test_database_backend_raises_not_implemented(self):
        db = DatabaseBackend()
        with self.assertRaises(NotImplementedError):
            db.query("SELECT 1")
        db.close()

    def test_dbapi2_backend_close(self):
        conn = sqlite3.connect(":memory:")
        db = DBAPI2Backend(conn)
        db.close()
        with self.assertRaises(Exception):
            conn.execute("SELECT 1")

    def test_sparkdb_backend_query(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.query.return_value = {"columns": ["x"], "rows": [[1]]}
        backend = SparkDBBackend(client)
        result = backend.query("SELECT 1", params=None, database="main")
        self.assertEqual(result["rows"], [[1]])
        client.query.assert_called_once_with("SELECT 1", params=None, database="main")

    def test_sparkdb_backend_query_with_params(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.query.return_value = {"columns": ["x"], "rows": [[42]]}
        backend = SparkDBBackend(client)
        result = backend.query("SELECT ?", params=[42], database="test")
        client.query.assert_called_once_with("SELECT ?", params=[42], database="test")
        self.assertEqual(result["rows"], [[42]])

    def test_sparkdb_backend_close(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        backend = SparkDBBackend(client)
        backend.close()
        client.close.assert_called_once()


class TestMigrationCoverage(unittest.TestCase):
    """Covers the Migrator class."""

    def setUp(self):
        self.db = DBAPI2Backend(sqlite3.connect(":memory:"))
        self.migrator = Migrator(self.db)
        # Create the schema versions table manually if needed
        self.db.query("""CREATE TABLE IF NOT EXISTS "_schema_versions" (
            "version" INTEGER PRIMARY KEY,
            "applied_at" TEXT DEFAULT (datetime('now'))
        )""")

    def test_get_current_version_zero_when_empty(self):
        version = self.migrator._get_current_version()
        self.assertEqual(version, 0)

    def test_migrate_applies_operations(self):
        self.migrator.migrate(1, ['CREATE TABLE IF NOT EXISTS "mig_test" (x INTEGER)'])
        result = self.db.query("SELECT * FROM mig_test")
        self.assertIn("columns", result)
        current = self.migrator._get_current_version()
        self.assertEqual(current, 1)

    def test_migrate_skips_already_applied(self):
        self.migrator.migrate(1, ['CREATE TABLE IF NOT EXISTS "mig_skip" (x INTEGER)'])
        self.migrator.migrate(1, ['CREATE TABLE IF NOT EXISTS "should_not_run" (x INTEGER)'])
        # should_not_run was never created because migration 1 was skipped
        result = self.db.query("SELECT name FROM sqlite_master WHERE name='should_not_run'")
        self.assertEqual(len(result["rows"]), 0)

    def test_create_table(self):
        tbl = unique_table("mig_model")

        class MigModel(Model):
            name = String(max_length=50)

            class Meta:
                database = self.db
                table = tbl

        self.migrator.create_table(MigModel)
        result = self.db.query(f"PRAGMA table_info(\"{tbl}\")")
        self.assertGreater(len(result["rows"]), 0)

    def test_auto_migrate_adds_missing_column(self):
        tbl = unique_table("auto_mig")

        class AutoMigModel(Model):
            name = String(max_length=50)
            age = Integer(nullable=True)

            class Meta:
                database = self.db
                table = tbl

        # Create table with only 'name'
        self.db.query(f'CREATE TABLE "{tbl}" ("id" INTEGER PRIMARY KEY AUTOINCREMENT, "name" VARCHAR(50))')
        # Now auto-migrate to add 'age'
        self.migrator.auto_migrate(AutoMigModel)
        info = self.db.query(f"PRAGMA table_info(\"{tbl}\")")
        cols = [row[1] for row in info["rows"]]
        self.assertIn("age", cols)

    def test_record_migration(self):
        self.migrator._record_migration(99)
        current = self.migrator._get_current_version()
        self.assertEqual(current, 99)

    def test_auto_migrate_with_nullable_and_default(self):
        tbl = unique_table("auto_mig2")
        self.db.query(f'CREATE TABLE "{tbl}" ("id" INTEGER PRIMARY KEY AUTOINCREMENT)')

        class AutoMig2(Model):
            name = String(max_length=50, nullable=False, default="untitled")
            score = Integer(nullable=False, default=0)

            class Meta:
                database = self.db
                table = tbl

        self.migrator.auto_migrate(AutoMig2)
        info = self.db.query(f"PRAGMA table_info(\"{tbl}\")")
        cols = [row[1] for row in info["rows"]]
        self.assertIn("name", cols)
        self.assertIn("score", cols)

    def test_auto_migrate_str_default_escaping(self):
        tbl = unique_table("auto_mig3")
        self.db.query(f'CREATE TABLE "{tbl}" ("id" INTEGER PRIMARY KEY AUTOINCREMENT)')

        class AutoMig3(Model):
            label = String(max_length=50, default="it's fine")

            class Meta:
                database = self.db
                table = tbl

        self.migrator.auto_migrate(AutoMig3)
        info = self.db.query(f"PRAGMA table_info(\"{tbl}\")")
        cols = [row[1] for row in info["rows"]]
        self.assertIn("label", cols)

    def test_get_current_version_without_rows_key(self):
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        mock_db.query.return_value = {}
        m = Migrator(mock_db)
        m._ensure_schema_table = lambda: None
        ver = m._get_current_version()
        self.assertEqual(ver, 0)


class TestExpressionCoverage(unittest.TestCase):
    """Covers missing expression paths."""

    def test_q_connector_non_q_raises(self):
        with self.assertRaises(TypeError):
            Q(name="a") & "not a Q"

    def test_f_repr(self):
        f = F("col")
        self.assertIn("col", repr(f))

    def test_f_negate(self):
        f = -F("val")
        self.assertIsNotNone(f)

    def test_f_math_operations(self):
        f1 = F("a") + F("b")
        f2 = F("a") - F("b")
        f3 = F("a") * F("b")
        f4 = F("a") / F("b")
        for f in (f1, f2, f3, f4):
            self.assertIsNotNone(f)

    def test_f_math_with_scalar(self):
        f = F("a") + 5
        self.assertIsNotNone(f)

    def test_f_unsupported_operator_raises(self):
        f = F("x")
        with self.assertRaises(TypeError):
            _ = f % 2

    def test_parse_where_key_unsupported_lookup(self):
        from sparkdb.expressions import _parse_where_key
        result = _parse_where_key("field__unknown_op")
        self.assertEqual(result, ("field__unknown_op", "exact"))

    def test_build_where_exact(self):
        from sparkdb.expressions import _build_where
        clause, params = _build_where("name", "exact", "Alice")
        self.assertIn("?", clause)
        self.assertEqual(params, ["Alice"])

    def test_q_negation(self):
        q = ~Q(name="Alice")
        self.assertTrue(q._negated)
        q2 = ~q
        self.assertFalse(q2._negated)

    def test_f_invalid_name_raises(self):
        with self.assertRaises(TypeError):
            F(123)

    def test_fexpr_repr(self):
        from sparkdb.expressions import _FExpr
        f = _FExpr(None, "-", F("x"))
        r = repr(f)
        self.assertIn("x", r)

    def test_fexpr_arithmetic(self):
        f = F("a") + 5
        f2 = f + 1
        self.assertIsNotNone(f2)
        f3 = f - 1
        self.assertIsNotNone(f3)
        f4 = f * 2
        self.assertIsNotNone(f4)
        f5 = f / 2
        self.assertIsNotNone(f5)

    def test_parse_where_key_empty(self):
        from sparkdb.expressions import _parse_where_key
        with self.assertRaises(ValueError):
            _parse_where_key("")

    def test_build_where_in_invalid(self):
        from sparkdb.expressions import _build_where
        with self.assertRaises(ValueError):
            _build_where("x", "in", 42)

    def test_build_where_contains_none(self):
        from sparkdb.expressions import _build_where
        with self.assertRaises(ValueError):
            _build_where("x", "contains", None)

    def test_build_where_startswith_none(self):
        from sparkdb.expressions import _build_where
        with self.assertRaises(ValueError):
            _build_where("x", "startswith", None)

    def test_build_where_endswith_none(self):
        from sparkdb.expressions import _build_where
        with self.assertRaises(ValueError):
            _build_where("x", "endswith", None)


class TestRelationshipCoverage(unittest.TestCase):
    """Covers missing relationship paths."""

    def setUp(self):
        self.db = memory_db()
        self.author_tbl = unique_table("author2")
        self.book_tbl = unique_table("book2")

        class Author(Model):
            name = String(max_length=50)

            class Meta:
                database = self.db
                table = self.author_tbl

        class Book(Model):
            title = String(max_length=100)
            author_fk = ForeignKey(Author, nullable=True, column="author_id")

            class Meta:
                database = self.db
                table = self.book_tbl

        self.Author = Author
        self.Book = Book
        Author.create_table()
        Book.create_table()
        a = Author.create(name="Alice")
        self.author1 = a
        Book.bulk_create([
            {"title": "B1", "author_fk": a.pk},
            {"title": "B2", "author_fk": a.pk},
        ])

    def test_fk_stores_pk_int(self):
        b = self.Book.create(title="FK Int Test", author_fk=self.author1.pk)
        self.assertIsNotNone(b.author_fk)

    def test_fk_to_db_uses_pk(self):
        from sparkdb.relationship import ForeignKey
        fk = ForeignKey(self.Author, nullable=True)
        fk._name = "author_fk"
        val = fk.to_db(self.author1)
        self.assertEqual(val, self.author1.pk)

    def test_fk_to_db_none(self):
        from sparkdb.relationship import ForeignKey
        fk = ForeignKey(self.Author, nullable=True)
        fk._name = "author_fk"
        self.assertIsNone(fk.to_db(None))

    def test_lazy_relation_bool_false_when_none(self):
        lr = LazyRelation(self.Author, None, "author_fk")
        self.assertFalse(lr)

    def test_lazy_relation_proxies_attributes(self):
        lr = LazyRelation(self.Author, self.author1.pk, "author_fk")
        self.assertEqual(lr.name, "Alice")

    def test_lazy_relation_caches_after_first_access(self):
        lr = LazyRelation(self.Author, self.author1.pk, "author_fk")
        _ = lr.name  # triggers resolve + cache
        self.assertIsNotNone(lr._resolved)

    def test_select_related_nonexistent_field(self):
        qs = self.Book.where().select_related("nonexistent")
        result = qs.all()
        self.assertEqual(len(result), 2)

    def test_prefetch_related(self):
        qs = self.Book.where().prefetch_related("author_fk")
        result = qs.all()
        self.assertEqual(len(result), 2)

    def test_prefetch_related_nonexistent(self):
        qs = self.Book.where().prefetch_related("nonexistent")
        result = qs.all()
        self.assertEqual(len(result), 2)

    def test_fk_from_db_none(self):
        book = self.Book.create(title="No Author")
        # Reload from DB to trigger from_db(None)
        loaded = self.Book.find(book.pk)
        self.assertIsNone(loaded.author_fk)

    def test_fk_validate_with_pk_attr(self):
        from sparkdb.relationship import ForeignKey
        fk = ForeignKey(self.Author, nullable=True)
        fk._name = "author_fk"
        # validate with an int
        fk.validate(42)
        # validate with a model instance (has pk attr)
        fk.validate(self.author1)

    def test_fk_validate_raises_on_bad_value(self):
        from sparkdb.relationship import ForeignKey
        from sparkdb.exceptions import ValidationError
        fk = ForeignKey(self.Author, nullable=True)
        fk._name = "author_fk"
        with self.assertRaises(ValidationError):
            fk.validate("not_an_int")

    def test_lazy_relation_resolved_cached(self):
        lr = LazyRelation(self.Author, self.author1.pk, "author_fk")
        _ = lr.name  # triggers resolve
        # second access uses cached _resolved
        self.assertIsNotNone(lr.name)

    def test_lazy_relation_resolves_and_caches(self):
        lr = LazyRelation(self.Author, self.author1.pk, "author_fk")
        self.assertIsNone(lr._resolved)
        _ = lr.name
        self.assertIsNotNone(lr._resolved)

    def test_lazy_relation_repr_resolved(self):
        lr = LazyRelation(self.Author, self.author1.pk, "author_fk")
        r = repr(lr)
        self.assertIn("Alice", r)

    def test_lazy_relation_repr_not_found(self):
        lr = LazyRelation(self.Author, 99999, "author_fk")
        r = repr(lr)
        self.assertIn("not found", r)

    def test_lazy_relation_missing_object_raises(self):
        lr = LazyRelation(self.Author, 99999, "author_fk")
        with self.assertRaises(AttributeError):
            _ = lr.name

    def test_reverse_relation_set(self):
        author = self.Author.find(self.author1.pk)
        author.books = ["cached_list"]
        self.assertEqual(author.books, ["cached_list"])

    def test_lazy_relation_with_instance_cache(self):
        author = self.Author.create(name="Charlie")
        book = self.Book.create(title="Cached Book", author_fk=author.pk)
        # simulate eager-load cache by setting the _resolved attr
        object.__setattr__(book, "_author_fk_resolved", author)
        lr = LazyRelation(self.Author, author.pk, "author_fk", instance=book)
        self.assertEqual(lr.name, "Charlie")


class TestModelCoverage(unittest.TestCase):
    """Covers missing model edge cases."""

    def setUp(self):
        self.db = memory_db()
        self.tbl = unique_table("cov_item")

        class CovItem(Model):
            name = String(max_length=100)
            value = Integer(nullable=True)

            class Meta:
                database = self.db
                table = self.tbl

        self.CovItem = CovItem
        CovItem.create_table()

    def test_delete_no_pk(self):
        item = self.CovItem()
        # Should not raise
        item.delete()

    def test_find_nonexistent(self):
        result = self.CovItem.find(99999)
        self.assertIsNone(result)

    def test_save_new_instance_with_defaults(self):
        item = self.CovItem()
        item.save()
        self.assertTrue(item._exists)
        self.assertIsNotNone(item.pk)

    def test_bulk_update_f_expression(self):
        from sparkdb.expressions import F
        self.CovItem.create(name="a", value=10)
        self.CovItem.create(name="b", value=20)
        self.CovItem.update(value=F("value") + 5)
        r = self.CovItem.where(name="a").first()
        self.assertEqual(r.value, 15)

    def test_bulk_update_no_rows(self):
        self.CovItem.update(value=99)
        r = self.CovItem.all()
        self.assertEqual(len(r), 0)

    def test_class_meta_defaults(self):
        class NoMeta(Model):
            name = String(max_length=50)

            class Meta:
                database = self.db
        self.assertEqual(NoMeta._meta.table, "nometas")
        self.assertEqual(NoMeta._meta.database_name, "main")
        self.assertFalse(NoMeta._meta.timestamps)
        self.assertEqual(NoMeta._meta.ordering, [])

    def test_create_with_json_field(self):
        tbl = unique_table("json_test")

        class JsonModel(Model):
            data = JSON()

            class Meta:
                database = self.db
                table = tbl

        JsonModel.create_table()
        inst = JsonModel.create(data={"nested": [1, 2]})
        found = JsonModel.find(inst.pk)
        self.assertEqual(found.data, {"nested": [1, 2]})

    def test_instance_update(self):
        item = self.CovItem.create(name="original", value=1)
        item.name = "updated"
        item.value = 99
        item.save()
        found = self.CovItem.find(item.pk)
        self.assertEqual(found.name, "updated")
        self.assertEqual(found.value, 99)

    def test_reload_with_existing_pk(self):
        item = self.CovItem.create(name="reload_me")
        pk = item.pk
        item.name = "changed"
        item.reload()
        self.assertEqual(item.name, "reload_me")
        self.assertEqual(item.pk, pk)

    def test_from_row_empty(self):
        result = self.CovItem._from_row(None)
        self.assertIsNone(result)

    def test_drop_table(self):
        self.CovItem.drop_table()
        with self.assertRaises(Exception):
            self.CovItem.find(1)

    def test_class_methods_proxy(self):
        self.CovItem.create(name="proxy_test")
        item = self.CovItem.first()
        self.assertIsNotNone(item)
        qs = self.CovItem.order_by("name")
        self.assertIsNotNone(qs)
        qs2 = self.CovItem.limit(1)
        self.assertIsNotNone(qs2)
        qs3 = self.CovItem.offset(0)
        self.assertIsNotNone(qs3)

    def test_values_class_method(self):
        self.CovItem.create(name="vals_cls", value=42)
        qs = self.CovItem.values("name")
        self.assertIsNotNone(qs)
        qs2 = self.CovItem.values_list("name")
        self.assertIsNotNone(qs2)

    def test_save_no_database_raises(self):
        class NoDb(Model):
            name = String(max_length=50)
            class Meta:
                pass
        with self.assertRaises(SparkDBError):
            NoDb(name="x").save()


class TestQueryCoverage(unittest.TestCase):
    """Covers missing query builder paths."""

    @classmethod
    def setUpClass(cls):
        cls.db = memory_db()
        cls.tbl = unique_table("qcov")

        class QCov(Model):
            name = String(max_length=100)
            value = Integer(nullable=True)
            category = String(max_length=50, nullable=True)

            class Meta:
                database = cls.db
                table = cls.tbl

        cls.QCov = QCov
        QCov.create_table()
        QCov.bulk_create([
            {"name": "a", "value": 10, "category": "X"},
            {"name": "b", "value": 20, "category": "Y"},
            {"name": "c", "value": 30, "category": "X"},
            {"name": "d", "value": 40, "category": None},
        ])

    def qs(self):
        return self.QCov.where()

    def test_having_with_q(self):
        from sparkdb.expressions import Q
        r = self.qs().group_by("category").having(Q(value__gt=10)).values("category").all()
        self.assertGreaterEqual(len(r), 0)

    def test_having_with_q_and_raw(self):
        result = self.qs().group_by("category").having("COUNT(*) > 0", value=10).values("category").all()
        self.assertGreaterEqual(len(result), 0)

    def test_order_by_empty(self):
        qs = self.qs().order_by()
        self.assertEqual(qs._order_by_fields, [])

    def test_values_list_result(self):
        result = self.qs().values_list("name").all()
        self.assertEqual(len(result), 4)
        self.assertIsInstance(result[0], tuple)

    def test_lookup_not_in_list(self):
        r = self.qs().where(name__not_in=[]).all()
        self.assertEqual(len(r), 0)

    def test_where_isnull_false_on_non_null(self):
        r = self.qs().where(category__isnull=False).all()
        self.assertEqual(len(r), 3)

    def test_update_with_f_expression(self):
        from sparkdb.expressions import F
        self.qs().where(name="a").update(value=F("value") + 100)
        item = self.QCov.where(name="a").first()
        self.assertEqual(item.value, 110)

    def test_count_zero(self):
        tbl = unique_table("empty_qs")

        class EmptyModel(Model):
            x = Integer()

            class Meta:
                database = self.db
                table = tbl

        EmptyModel.create_table()
        c = EmptyModel.where().count()
        self.assertEqual(c, 0)

    def test_exists_false(self):
        self.assertFalse(self.qs().where(name="nonexistent").exists())

    def test_paginate_single_page(self):
        result = self.qs().paginate(page=1, per_page=100)
        self.assertEqual(result["total"], 4)
        self.assertFalse(result["has_next"])

    def test_where_f_expression(self):
        r = self.qs().where(value=F("value")).all()
        self.assertIsInstance(r, list)

    def test_order_by_invalid_field(self):
        with self.assertRaises(ValueError):
            self.qs().order_by("")

    def test_order_by_invalid_type(self):
        with self.assertRaises(ValueError):
            self.qs().order_by(123)

    def test_having_with_raw_string_and_params_list(self):
        r = self.qs().group_by("category").having("COUNT(*) > ?", [0]).values("category").all()
        self.assertGreaterEqual(len(r), 0)

    def test_iter_qs(self):
        count = sum(1 for _ in self.qs())
        self.assertEqual(count, 4)

    def test_qs_repr(self):
        r = repr(self.qs())
        self.assertIn("QCov", r)

    def test_first_with_values(self):
        r = self.qs().values("name").first()
        self.assertIsNotNone(r)
        self.assertIn("name", r)

    def test_qs_delete(self):
        tbl = unique_table("del_test")
        class DelModel(Model):
            name = String(max_length=50)
            class Meta:
                database = self.db
                table = tbl
        DelModel.create_table()
        DelModel.create(name="delete_me")
        self.assertEqual(DelModel.count(), 1)
        DelModel.where(name="delete_me").delete()
        self.assertEqual(DelModel.count(), 0)

    def test_qs_delete_empty(self):
        self.qs().where(name="nonexistent").delete()

    def test_update_no_sets(self):
        self.qs().update()

    def test_update_debug(self):
        tbl = unique_table("debug_upd")
        class DebugUpd(Model):
            name = String(max_length=50)
            value = Integer()
            class Meta:
                database = self.db
                table = tbl
        DebugUpd.create_table()
        DebugUpd.create(name="tgt", value=5)
        qs = DebugUpd.where().debug()
        qs = qs.where(name="tgt")
        qs.update(value=999)

    def test_aggregate_with_group_having(self):
        from sparkdb.expressions import Q
        total = self.qs().group_by("category").having(Q(value__gt=5)).sum("value")
        self.assertIsNotNone(total)

    def test_aggregate_no_rows(self):
        tbl = unique_table("empty_agg")
        class EmptyAgg(Model):
            x = Integer()
            class Meta:
                database = self.db
                table = tbl
        EmptyAgg.create_table()
        result = EmptyAgg.where().sum("x")
        self.assertIsNone(result)

    def test_getitem_negative_slice_stop(self):
        with self.assertRaises(IndexError):
            self.qs()[:(-1)]

    def test_pluck_clone(self):
        qs = self.qs().pluck("name")
        cloned = qs.where(name="a")
        self.assertIsInstance(cloned._values_fields, tuple)

    def test_count_on_empty_table(self):
        tbl = unique_table("empty_ct")
        class EmptyCt(Model):
            n = Integer()
            class Meta:
                database = self.db
                table = tbl
        EmptyCt.create_table()
        self.assertEqual(EmptyCt.where().count(), 0)


class TestFieldCoverageDetail(unittest.TestCase):
    """Covers remaining field paths."""

    def test_base_field_sql_type_raises(self):
        f = Field()
        with self.assertRaises(NotImplementedError):
            f.sql_type()

    def test_field_class_access_returns_descriptor(self):
        f = String(max_length=50)
        f._name = "name"
        self.assertIs(f.__get__(None, None), f)

    def test_field_resolved_path(self):
        db = memory_db()
        tbl = unique_table("resolved_test")

        class RModel(Model):
            name = String(max_length=50)

            class Meta:
                database = db
                table = tbl

        RModel.create_table()
        item = RModel(name="original")
        object.__setattr__(item, "_name_resolved", "overridden")
        self.assertEqual(item.name, "overridden")

    def test_float_to_db_none(self):
        f = Float()
        self.assertIsNone(f.to_db(None))

    def test_float_from_db_none(self):
        f = Float()
        self.assertIsNone(f.from_db(None))

    def test_boolean_to_db_none(self):
        f = Boolean()
        self.assertIsNone(f.to_db(None))

    def test_boolean_from_db_none(self):
        f = Boolean()
        self.assertIsNone(f.from_db(None))

    def test_datetime_auto_now_sets_nullable(self):
        dt = DateTime(auto_now=True)
        self.assertTrue(dt.nullable)
        dt2 = DateTime(auto_now_add=True)
        self.assertTrue(dt2.nullable)

    def test_datetime_to_db_none(self):
        f = DateTime()
        self.assertIsNone(f.to_db(None))

    def test_datetime_to_db_str_fallback(self):
        f = DateTime()
        result = f.to_db("2024-01-01T00:00:00")
        self.assertEqual(result, "2024-01-01T00:00:00")

    def test_datetime_from_db_none(self):
        f = DateTime()
        self.assertIsNone(f.from_db(None))

    def test_json_to_db_none(self):
        f = JSON()
        self.assertIsNone(f.to_db(None))

    def test_json_non_serializable_raises(self):
        f = JSON()
        f._name = "data"
        with self.assertRaises(ValidationError):
            f.to_db(object())

    def test_json_from_db_none(self):
        f = JSON()
        self.assertIsNone(f.from_db(None))


class TestModelCoverageDetail(unittest.TestCase):
    """Covers remaining model paths."""

    def setUp(self):
        self.db = memory_db()

    def test_model_inheritance(self):
        tbl_base = unique_table("base_tbl")
        tbl_child = unique_table("child_tbl")

        class Base(Model):
            base_name = String(max_length=50)

            class Meta:
                database = self.db
                table = tbl_base

        class Child(Base):
            child_name = String(max_length=50)

            class Meta:
                database = self.db
                table = tbl_child

        self.assertIn("base_name", Child._fields)
        self.assertIn("child_name", Child._fields)

    def test_invalid_meta_indexes_raises(self):
        with self.assertRaises(ValueError):
            class BadIndexes(Model):
                name = String(max_length=50)

                class Meta:
                    database = self.db
                    indexes = ["not_a_dict"]

    def test_create_table_no_database_raises(self):
        class NoDb(Model):
            name = String(max_length=50)

        with self.assertRaises(SparkDBError):
            NoDb.create_table()

    def test_drop_table_no_database_raises(self):
        class NoDb(Model):
            name = String(max_length=50)

        with self.assertRaises(SparkDBError):
            NoDb.drop_table()

    def test_from_row_applies_defaults(self):
        tbl = unique_table("defaults_row")

        class DModel(Model):
            name = String(max_length=50, default="unknown")
            value = Integer(default=42)

            class Meta:
                database = self.db
                table = tbl

        DModel.create_table()
        inst = DModel._from_row({"id": 1})
        self.assertEqual(inst.name, "unknown")
        self.assertEqual(inst.value, 42)

    def test_create_table_with_pk_unique_and_indexes(self):
        tbl = unique_table("full_feature")

        class FullModel(Model):
            label = String(max_length=50, unique=True)
            code = Integer(nullable=False, index=True)

            class Meta:
                database = self.db
                table = tbl
                unique_together = [("label", "code")]
                indexes = [
                    {"fields": ["label", "code"], "name": "idx_label_code"},
                ]

        FullModel.create_table()
        FullModel.create(label="hello", code=1)
        found = FullModel.find(1)
        self.assertIsNotNone(found)

    def test_create_table_with_non_autoinc_pk(self):
        tbl = unique_table("non_auto_pk")

        class NonAutoPK(Model):
            id = Integer(primary_key=True)
            name = String(max_length=50)

            class Meta:
                database = self.db
                table = tbl

        NonAutoPK.create_table()
        NonAutoPK.create(id=1, name="hello")
        found = NonAutoPK.find(1)
        self.assertEqual(found.name, "hello")

    def test_save_update_no_sets_returns_early(self):
        tbl = unique_table("only_pk")

        class OnlyPk(Model):
            class Meta:
                database = self.db
                table = tbl

        OnlyPk.create_table()
        self.db.query(f'INSERT INTO "{tbl}" DEFAULT VALUES')
        item = OnlyPk()
        item._data = {"id": 1}
        item._exists = True
        item._saved = True
        item.save()

    def test_getattr_relation_descriptor(self):
        tbl_a = unique_table("getattr_a")
        tbl_b = unique_table("getattr_b")

        class A(Model):
            name = String(max_length=50)

            class Meta:
                database = self.db
                table = tbl_a

        class B(Model):
            title = String(max_length=50)
            a = ForeignKey(A, nullable=True)

            class Meta:
                database = self.db
                table = tbl_b

        A.create_table()
        B.create_table()
        a = A.create(name="parent")
        has_many(B, name="bs", fk_column="a")(A)
        self.assertIsNotNone(a.bs)


class TestQueryCoverageDetail(unittest.TestCase):
    """Covers remaining query paths."""

    @classmethod
    def setUpClass(cls):
        cls.db = memory_db()
        cls.tbl = unique_table("qdet")
        cls.empty_tbl = unique_table("empty_qdet")

        class QDet(Model):
            name = String(max_length=100)
            value = Integer(nullable=True)
            category = String(max_length=50, nullable=True)

            class Meta:
                database = cls.db
                table = cls.tbl
                ordering = ["-value"]

        class EmptyQDet(Model):
            x = Integer()

            class Meta:
                database = cls.db
                table = cls.empty_tbl

        cls.QDet = QDet
        cls.EmptyQDet = EmptyQDet
        QDet.create_table()
        EmptyQDet.create_table()
        QDet.bulk_create([
            {"name": "a", "value": 10, "category": "X"},
            {"name": "b", "value": 20, "category": "Y"},
        ])

    def qs(self):
        return self.QDet.where()

    def test_render_fexpr_negated_and_values(self):
        from sparkdb.expressions import _FExpr
        from sparkdb.query import _render_fexpr
        expr = _FExpr(None, "-", F("val"))
        result = _render_fexpr(expr)
        self.assertIn("-", result)
        self.assertIn("val", result)
        str_result = _render_fexpr("hello")
        self.assertIn("hello", str_result)
        int_result = _render_fexpr(42)
        self.assertEqual(int_result, "42")
        bytes_result = _render_fexpr(b"test")
        self.assertIn("test", bytes_result)

    def test_meta_ordering_desc(self):
        qs = self.QDet.where()
        self.assertIn("value", qs._order_by_fields)
        self.assertTrue(qs._order_dirs.get("value"))

    def test_where_fexpr(self):
        from sparkdb.expressions import _FExpr
        expr = F("value") + 5
        qs = self.qs().where(value=expr)
        self.assertGreater(len(qs._where_clauses), 0)

    def test_order_by_empty_string_raises(self):
        with self.assertRaises(ValueError):
            self.qs().order_by("")

    def test_order_by_only_dash_raises(self):
        with self.assertRaises(ValueError):
            self.qs().order_by("-")

    def test_having_q_objects(self):
        from sparkdb.expressions import Q
        qs = self.qs().group_by("category").having(Q(value__gt=5))
        self.assertGreaterEqual(len(qs._having_clauses), 0)

    def test_select_related_no_ref_model(self):
        qs = self.qs().select_related("name")
        result = qs.all()
        self.assertGreaterEqual(len(result), 0)

    def test_select_related_no_fk_values(self):
        tbl_b = unique_table("srnfk_b")
        tbl_a = unique_table("srnfk_a")

        class A(Model):
            name = String(max_length=50)

            class Meta:
                database = self.db
                table = tbl_a

        class B(Model):
            title = String(max_length=50)
            a = ForeignKey(A, nullable=True)

            class Meta:
                database = self.db
                table = tbl_b

        A.create_table()
        B.create_table()
        B.create(title="orphan")
        results = B.where().select_related("a").all()
        self.assertEqual(len(results), 1)

    def test_select_related_caches_resolved(self):
        from sparkdb.relationship import ForeignKey
        tbl_a = unique_table("src_a")
        tbl_b = unique_table("src_b")

        class A(Model):
            name = String(max_length=50)

            class Meta:
                database = self.db
                table = tbl_a

        class B(Model):
            title = String(max_length=50)
            a = ForeignKey(A, nullable=True)

            class Meta:
                database = self.db
                table = tbl_b

        A.create_table()
        B.create_table()
        a = A.create(name="parent")
        b = B.create(title="child", a=a.pk)
        results = B.where().select_related("a").all()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].a.name, "parent")

    def test_prefetch_related_hits_loader(self):
        tbl_a = unique_table("pref_a")
        tbl_b = unique_table("pref_b")

        class A(Model):
            name = String(max_length=50)

            class Meta:
                database = self.db
                table = tbl_a

        class B(Model):
            title = String(max_length=50)
            a = ForeignKey(A, nullable=True)

            class Meta:
                database = self.db
                table = tbl_b

        A.create_table()
        B.create_table()
        a = A.create(name="parent")
        B.create(title="c1", a=a.pk)
        B.create(title="c2", a=a.pk)

        has_many(B, name="children", fk_column="a")(A)
        results = A.where().prefetch_related("children").all()
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0].children), 2)

    def test_debug_execute(self):
        import io, sys
        buf = io.StringIO()
        sys.stdout = buf
        self.qs().debug().all()
        sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertIn("[SQL]", output)

    def test_debug_aggregate(self):
        import io, sys
        buf = io.StringIO()
        sys.stdout = buf
        self.qs().debug().sum("value")
        sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertIn("[SQL]", output)

    def test_debug_delete(self):
        import io, sys
        buf = io.StringIO()
        sys.stdout = buf
        self.qs().where(name="nonexistent").debug().delete()
        sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertIn("[SQL]", output)

    def test_count_zero(self):
        c = self.EmptyQDet.where().count()
        self.assertEqual(c, 0)

    def test_aggregate_with_having_raw(self):
        result = self.qs().group_by("category").having("COUNT(*) > ?", 0).sum("value")
        self.assertIsInstance(result, (int, float))

    def test_aggregate_no_rows_returns_none(self):
        result = self.EmptyQDet.where().sum("x")
        self.assertIsNone(result)


class TestRelationshipCoverageDetail(unittest.TestCase):
    """Covers remaining relationship paths."""

    def setUp(self):
        self.db = memory_db()
        self.parent_tbl = unique_table("rcd_p")
        self.child_tbl = unique_table("rcd_c")

        class Parent(Model):
            name = String(max_length=50)

            class Meta:
                database = self.db
                table = self.parent_tbl

        class Child(Model):
            name = String(max_length=50)
            parent = ForeignKey(Parent, nullable=True, column="parent_id")

            class Meta:
                database = self.db
                table = self.child_tbl

        self.Parent = Parent
        self.Child = Child
        Parent.create_table()
        Child.create_table()

    def test_reverse_relation_descriptor_set_and_get(self):
        has_many(self.Child, name="my_children", fk_column="parent_id")(self.Parent)
        p = self.Parent.create(name="p1")
        self.Child.create(name="c1", parent=p.pk)
        p.my_children = ["cached"]
        result = p.my_children
        self.assertEqual(result, ["cached"])

    def test_has_many_populates_rel_descriptors(self):
        class X(Model):
            n = String(max_length=50)
            class Meta:
                database = self.db
                table = unique_table("x_rel_desc")
        class Y(Model):
            n = String(max_length=50)
            x = ForeignKey(X, nullable=True)
            class Meta:
                database = self.db
                table = unique_table("y_rel_desc")
        X.create_table()
        Y.create_table()
        X._rel_descriptors.clear()
        has_many(Y, name="children", fk_column="x")(X)
        self.assertIn("children", X._rel_descriptors)

    def test_prefetch_loader_empty_pks(self):
        has_many(self.Child, name="empty_children", fk_column="parent_id")(self.Parent)
        p = self.Parent.create(name="lonely")
        self.assertEqual(p.empty_children, [])

    def test_prefetch_key_property(self):
        has_many(self.Child, name="pk_children", fk_column="parent_id")(self.Parent)
        p = self.Parent.create(name="pk_test")
        key = p._prefetch_key_pk_children
        self.assertEqual(key, p.pk)


class TestExtendedFieldTypes(unittest.TestCase):
    """Tests for BLOB, Date, Time, Decimal, UUID fields."""

    def setUp(self):
        self.db = memory_db()

    def test_blob_roundtrip(self):
        tbl = unique_table("blob_test")
        class M(Model):
            data = BLOB(nullable=True)
            class Meta:
                database = self.db
                table = tbl
        M.create_table()
        blob = b"hello\x00world\xff"
        inst = M.create(data=blob)
        loaded = M.find(inst.pk)
        self.assertEqual(loaded.data, blob)

    def test_blob_none(self):
        f = BLOB()
        self.assertIsNone(f.to_db(None))
        self.assertIsNone(f.from_db(None))

    def test_blob_validation(self):
        f = BLOB()
        f._name = "data"
        with self.assertRaises(ValidationError):
            f.to_db("not bytes")

    def test_date_roundtrip(self):
        tbl = unique_table("date_test")
        class M(Model):
            d = Date(nullable=True)
            class Meta:
                database = self.db
                table = tbl
        M.create_table()
        dt = date(2025, 12, 25)
        inst = M.create(d=dt)
        loaded = M.find(inst.pk)
        self.assertEqual(loaded.d, dt)

    def test_date_none(self):
        f = Date()
        self.assertIsNone(f.to_db(None))
        self.assertIsNone(f.from_db(None))

    def test_date_from_db_str(self):
        f = Date()
        result = f.from_db("2025-12-25")
        self.assertEqual(result, date(2025, 12, 25))

    def test_date_validation(self):
        f = Date()
        f._name = "d"
        with self.assertRaises(ValidationError):
            f.to_db(42)

    def test_time_roundtrip(self):
        tbl = unique_table("time_test")
        class M(Model):
            t = Time(nullable=True)
            class Meta:
                database = self.db
                table = tbl
        M.create_table()
        tm = time(14, 30, 0)
        inst = M.create(t=tm)
        loaded = M.find(inst.pk)
        self.assertEqual(loaded.t, tm)

    def test_time_none(self):
        f = Time()
        self.assertIsNone(f.to_db(None))
        self.assertIsNone(f.from_db(None))

    def test_time_from_db_str(self):
        f = Time()
        result = f.from_db("14:30:00")
        self.assertEqual(result, time(14, 30, 0))

    def test_decimal_roundtrip(self):
        tbl = unique_table("dec_test")
        class M(Model):
            val = Decimal(max_digits=10, decimal_places=2, nullable=True)
            class Meta:
                database = self.db
                table = tbl
        M.create_table()
        d = _Decimal("123.45")
        inst = M.create(val=d)
        loaded = M.find(inst.pk)
        self.assertEqual(loaded.val, d)

    def test_decimal_from_int_float(self):
        f = Decimal()
        self.assertEqual(f.to_db(42), "42")
        self.assertEqual(f.to_db(3.14), "3.14")
        result = f.from_db("42")
        self.assertEqual(result, _Decimal("42"))

    def test_decimal_none(self):
        f = Decimal()
        self.assertIsNone(f.to_db(None))
        self.assertIsNone(f.from_db(None))

    def test_uuid_roundtrip(self):
        tbl = unique_table("uuid_test")
        class M(Model):
            uid = UUID(nullable=True)
            class Meta:
                database = self.db
                table = tbl
        M.create_table()
        u = _UUID("550e8400-e29b-41d4-a716-446655440000")
        inst = M.create(uid=u)
        loaded = M.find(inst.pk)
        self.assertEqual(loaded.uid, u)

    def test_uuid_from_str(self):
        f = UUID()
        f._name = "uid"
        val = f.to_db("550e8400-e29b-41d4-a716-446655440000")
        self.assertEqual(val, "550e8400-e29b-41d4-a716-446655440000")

    def test_uuid_none(self):
        f = UUID()
        self.assertIsNone(f.to_db(None))
        self.assertIsNone(f.from_db(None))


class TestInitImports(unittest.TestCase):
    """Verifies __init__ imports work correctly."""

    def test_import_sparkdb_package(self):
        import sparkdb
        self.assertTrue(hasattr(sparkdb, "Model"))
        self.assertTrue(hasattr(sparkdb, "SparkDB"))
        self.assertTrue(hasattr(sparkdb, "AdminNamespace"))
        self.assertTrue(hasattr(sparkdb, "validate_password_strength"))
        self.assertTrue(hasattr(sparkdb, "models"))
        self.assertTrue(hasattr(sparkdb, "fields"))
        self.assertTrue(hasattr(sparkdb, "Migrator"))

    def test_import_package_version(self):
        import sparkdb
        self.assertEqual(sparkdb.__version__, "0.3.0")

if __name__ == "__main__":
    unittest.main(verbosity=2)
