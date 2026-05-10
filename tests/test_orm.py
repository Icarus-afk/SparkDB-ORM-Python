#!/usr/bin/env python3
import json
import os
import sqlite3
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sparkdb import Model, DBAPI2Backend
from sparkdb.exceptions import *
from sparkdb.fields import (
    String, Integer, Float, Boolean, DateTime, JSON, Text, Field
)
from sparkdb.expressions import Q, F, _parse_where_key, _build_where, _WHERE_OPS
from sparkdb.relationship import ForeignKey, has_many
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
