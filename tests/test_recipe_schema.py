"""recipe_schema 的单元测试（新增测试面，与既有测试不重名、不改既有文件）。"""

import ast
import json
import os
import sys
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    from memory_agent import recipe_schema as rs
except Exception:  # pragma: no cover - 兜底：直接按文件路径加载，避免包 __init__ 干扰
    import importlib.util

    _MODULE_PATH = os.path.join(_SRC, "memory_agent", "recipe_schema.py")
    _SPEC = importlib.util.spec_from_file_location("recipe_schema", _MODULE_PATH)
    rs = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(rs)


# ------------------------------------------------------------------ helpers

def _valid_recipe_dict():
    data = {
        "intent": "device_usage",
        "object_type": "device",
        "metric": "duration",
        "time_window": "last_7_days",
        "person": "",
        "tool_sequence": [
            {"tool_name": "get_device_usage_summary",
             "params": {"entity_id": "{entity_id}", "days": "{days}"}},
            {"tool_name": "get_room_usage_detail",
             "params": {"room": "{room}", "days": "{days}"}, "depends_on": "usage_summary"},
        ],
        "confidence": 0.8,
        "sample_count": 3,
        "created_at": "2026-10-08T13:10:11Z",
        "source_session": "sess-2026-10-08-0001",
        "status": "staging",
    }
    data["recipe_id"] = rs.make_recipe_id(data)
    return data


# --------------------------------------------------------------- make_recipe_id

class TestMakeRecipeId(unittest.TestCase):

    def test_same_content_gives_same_id(self):
        left = _valid_recipe_dict()
        right = _valid_recipe_dict()
        self.assertEqual(rs.make_recipe_id(left), rs.make_recipe_id(right))
        rid = rs.make_recipe_id(left)
        self.assertTrue(rid.startswith("recipe_"))
        self.assertEqual(len(rid), len("recipe_") + 8)

    def test_key_order_does_not_change_id(self):
        base = _valid_recipe_dict()
        shuffled = dict(reversed(list(base.items())))
        self.assertEqual(rs.make_recipe_id(base), rs.make_recipe_id(shuffled))

    def test_different_content_gives_different_id(self):
        base = _valid_recipe_dict()
        for name, value in (("metric", "count"), ("intent", "compare"),
                            ("time_window", "today"), ("person", "小明"),
                            ("object_type", "room")):
            other = _valid_recipe_dict()
            other[name] = value
            with self.subTest(field=name):
                self.assertNotEqual(rs.make_recipe_id(base), rs.make_recipe_id(other))

    def test_tool_sequence_changes_id(self):
        base = _valid_recipe_dict()
        tweaked = _valid_recipe_dict()
        tweaked["tool_sequence"][0]["params"]["days"] = 30
        self.assertNotEqual(rs.make_recipe_id(base), rs.make_recipe_id(tweaked))
        extended = _valid_recipe_dict()
        extended["tool_sequence"].append({"tool_name": "get_room_usage_detail", "params": {}})
        self.assertNotEqual(rs.make_recipe_id(base), rs.make_recipe_id(extended))

    def test_metadata_does_not_change_id(self):
        base = _valid_recipe_dict()
        other = _valid_recipe_dict()
        other.update({
            "recipe_id": "recipe_deadbeef", "confidence": 0.1, "sample_count": 99,
            "created_at": "2030-01-01T00:00:00Z", "source_session": "sess-other", "status": "live",
        })
        self.assertEqual(rs.make_recipe_id(base), rs.make_recipe_id(other))

    def test_accepts_recipe_instance(self):
        data = _valid_recipe_dict()
        recipe = rs.Recipe.from_dict(data)
        self.assertEqual(rs.make_recipe_id(recipe), data["recipe_id"])

    def test_rejects_invalid_input(self):
        for value in ("not-a-recipe", 7, None, ["x"]):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    rs.make_recipe_id(value)


# ----------------------------------------------------------------- validate

class TestValidateRecipe(unittest.TestCase):

    def test_valid_dict_passes(self):
        self.assertEqual(rs.validate_recipe(_valid_recipe_dict()), [])

    def test_valid_recipe_object_passes(self):
        recipe = rs.Recipe.from_dict(_valid_recipe_dict())
        self.assertEqual(rs.validate_recipe(recipe), [])

    def test_missing_required_field_is_reported(self):
        for name in rs.REQUIRED_FIELDS:
            data = _valid_recipe_dict()
            del data[name]
            with self.subTest(field=name):
                errors = rs.validate_recipe(data)
                self.assertTrue(errors)
                self.assertTrue(any(e.startswith(name + ":") for e in errors), errors)

    def test_wrong_type_is_reported(self):
        cases = [
            ("intent", 7), ("object_type", None), ("metric", ["duration"]), ("time_window", 3.5),
            ("person", 5), ("confidence", "0.5"), ("confidence", True), ("sample_count", 1.5),
            ("sample_count", "3"), ("sample_count", False), ("created_at", 12345),
            ("source_session", 9), ("status", ["live"]), ("tool_sequence", "steps"), ("recipe_id", 123),
        ]
        for name, value in cases:
            data = _valid_recipe_dict()
            data[name] = value
            with self.subTest(field=name, value=value):
                errors = rs.validate_recipe(data)
                self.assertTrue(any(e.startswith(name + ":") for e in errors), errors)

    def test_unknown_enum_value_is_reported(self):
        cases = [("intent", "cook_dinner"), ("object_type", "building"), ("metric", "average"),
                 ("time_window", "last_year"), ("status", "draft")]
        for name, value in cases:
            data = _valid_recipe_dict()
            data[name] = value
            with self.subTest(field=name):
                errors = rs.validate_recipe(data)
                self.assertTrue(any(e.startswith(name + ":") for e in errors), errors)
                self.assertTrue(any("unknown value" in e for e in errors), errors)

    def test_empty_tool_sequence_rejected(self):
        data = _valid_recipe_dict()
        data["tool_sequence"] = []
        errors = rs.validate_recipe(data)
        self.assertTrue(any("tool_sequence:" in e for e in errors), errors)

    def test_tool_sequence_must_be_list(self):
        for value in ("steps", {}, None, 7):
            data = _valid_recipe_dict()
            data["tool_sequence"] = value
            with self.subTest(value=value):
                self.assertTrue(rs.validate_recipe(data))

    def test_confidence_range(self):
        for value, ok in ((0, True), (0.0, True), (1, True), (1.0, True), (0.42, True),
                          (-0.01, False), (1.01, False), (2, False), (-1, False)):
            data = _valid_recipe_dict()
            data["confidence"] = value
            with self.subTest(value=value):
                errors = rs.validate_recipe(data)
                self.assertEqual(not errors, ok, errors)

    def test_sample_count_rules(self):
        for value, ok in ((0, True), (7, True), (-1, False), (1.5, False), ("3", False)):
            data = _valid_recipe_dict()
            data["sample_count"] = value
            with self.subTest(value=value):
                errors = rs.validate_recipe(data)
                self.assertEqual(not errors, ok, errors)

    def test_created_at_must_be_iso8601(self):
        good = ["2026-10-08T13:10:11Z", "2026-10-08T13:10:11", "2026-10-08 13:10:11",
                "2026-10-08T13:10:11.123456+08:00", "2026-10-08T13:10:11+0800"]
        bad = ["", "not-a-date", "2026-10-08", "2026-13-08T13:10:11Z",
               "2026-10-08T25:10:11Z", "2026-10-08T13:61:11Z", "08/10/2026"]
        for value in good:
            data = _valid_recipe_dict()
            data["created_at"] = value
            with self.subTest(created_at=value, expect="valid"):
                self.assertEqual(rs.validate_recipe(data), [])
        for value in bad:
            data = _valid_recipe_dict()
            data["created_at"] = value
            with self.subTest(created_at=value, expect="invalid"):
                errors = rs.validate_recipe(data)
                self.assertTrue(any(e.startswith("created_at:") for e in errors), errors)

    def test_recipe_id_format(self):
        good = ["recipe_deadbeef", "recipe_00000000", "recipe_a1b2c3d4"]
        bad = ["", "recipe_xyz", "recipe_1234567", "recipe_123456789", "deadbeef", "recipe_DEADBEEF"]
        for value in good:
            data = _valid_recipe_dict()
            data["recipe_id"] = value
            with self.subTest(recipe_id=value, expect="valid"):
                self.assertEqual(rs.validate_recipe(data), [])
        for value in bad:
            data = _valid_recipe_dict()
            data["recipe_id"] = value
            with self.subTest(recipe_id=value, expect="invalid"):
                errors = rs.validate_recipe(data)
                self.assertTrue(any(e.startswith("recipe_id:") for e in errors), errors)

    def test_tool_step_errors(self):
        cases = [
            [{"params": {}}],
            [{"tool_name": ""}],
            [{"tool_name": 7}],
            [{"tool_name": "x", "params": []}],
            [{"tool_name": "x", "depends_on": 3}],
            [{"tool_name": "x", "params": {1: "a"}}],
            ["not-a-step"],
        ]
        for steps in cases:
            data = _valid_recipe_dict()
            data["tool_sequence"] = steps
            with self.subTest(steps=steps):
                self.assertTrue(rs.validate_recipe(data))

    def test_person_is_optional(self):
        data = _valid_recipe_dict()
        del data["person"]
        self.assertEqual(rs.validate_recipe(data), [])
        data["person"] = ""
        self.assertEqual(rs.validate_recipe(data), [])
        data["person"] = "小明"
        self.assertEqual(rs.validate_recipe(data), [])

    def test_collects_multiple_errors_at_once(self):
        data = _valid_recipe_dict()
        data["intent"] = "cook"
        data["metric"] = "average"
        data["confidence"] = 5
        data["sample_count"] = -1
        data["created_at"] = "yesterday"
        errors = rs.validate_recipe(data)
        self.assertGreaterEqual(len(errors), 5)

    def test_never_raises_on_non_mapping_input(self):
        for value in (None, 7, "recipe", [], object()):
            with self.subTest(value=value):
                errors = rs.validate_recipe(value)
                self.assertIsInstance(errors, list)
                self.assertTrue(errors)

    def test_id_consistency_check_is_opt_in(self):
        data = _valid_recipe_dict()
        expected = rs.make_recipe_id(data)
        data["recipe_id"] = "recipe_deadbeef"
        self.assertNotEqual(expected, "recipe_deadbeef")
        self.assertEqual(rs.validate_recipe(data), [])
        errors = rs.validate_recipe(data, check_id_consistency=True)
        self.assertTrue(any(e.startswith("recipe_id:") for e in errors), errors)


# ------------------------------------------------------------- serialization

class TestSerializationRoundTrip(unittest.TestCase):

    def test_round_trip_is_lossless(self):
        original = rs.Recipe.from_dict(_valid_recipe_dict())
        restored = rs.deserialize_recipe(rs.serialize_recipe(original))
        self.assertEqual(restored, original)
        for name in ("recipe_id", "intent", "object_type", "metric", "time_window",
                     "person", "confidence", "sample_count", "created_at",
                     "source_session", "status"):
            self.assertEqual(getattr(restored, name), getattr(original, name), name)
        self.assertEqual(len(restored.tool_sequence), len(original.tool_sequence))
        for left, right in zip(restored.tool_sequence, original.tool_sequence):
            self.assertEqual(left, right)

    def test_round_trip_preserves_non_ascii(self):
        data = _valid_recipe_dict()
        data["person"] = "小明"
        data["tool_sequence"][0]["params"]["label"] = "客厅电视"
        original = rs.Recipe.from_dict(data)
        restored = rs.deserialize_recipe(rs.serialize_recipe(original))
        self.assertEqual(restored.person, "小明")
        self.assertEqual(restored.tool_sequence[0].params["label"], "客厅电视")
        self.assertEqual(restored, original)

    def test_round_trip_preserves_unknown_enum_values(self):
        data = _valid_recipe_dict()
        data["intent"] = "future_intent"
        data["status"] = "draft"
        recipe = rs.Recipe.from_dict(data)          # from_dict 不校验枚举
        restored = rs.deserialize_recipe(rs.serialize_recipe(recipe))
        self.assertEqual(restored.intent, "future_intent")
        self.assertEqual(restored.status, "draft")
        self.assertTrue(rs.validate_recipe(restored))  # 但 validate 会拒绝

    def test_serialize_outputs_json_object(self):
        text = rs.serialize_recipe(rs.Recipe.from_dict(_valid_recipe_dict()))
        parsed = json.loads(text)
        for name in rs.REQUIRED_FIELDS:
            self.assertIn(name, parsed)
        self.assertIn("person", parsed)

    def test_serialize_accepts_mapping(self):
        text = rs.serialize_recipe(_valid_recipe_dict())
        parsed = json.loads(text)
        self.assertEqual(parsed["intent"], "device_usage")
        self.assertEqual(len(parsed["tool_sequence"]), 2)

    def test_to_dict_shape(self):
        recipe = rs.Recipe.from_dict(_valid_recipe_dict())
        data = recipe.to_dict()
        self.assertEqual(set(data), set(rs.REQUIRED_FIELDS) | {"person"})
        self.assertIsInstance(data["tool_sequence"][0], dict)
        self.assertEqual(set(data["tool_sequence"][0]), {"tool_name", "params", "depends_on"})

    def test_deserialize_invalid_json_raises(self):
        for text in ("{not json", "", "[]", '"a string"'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    rs.deserialize_recipe(text)

    def test_deserialize_structural_problems_raise(self):
        cases = []
        missing = _valid_recipe_dict()
        del missing["metric"]
        cases.append(missing)
        wrong_type = _valid_recipe_dict()
        wrong_type["tool_sequence"] = "steps"
        cases.append(wrong_type)
        bad_step = _valid_recipe_dict()
        bad_step["tool_sequence"] = ["not-a-step"]
        cases.append(bad_step)
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    rs.deserialize_recipe(json.dumps(payload))

    def test_deserialize_rejects_non_str(self):
        with self.assertRaises(TypeError):
            rs.deserialize_recipe(123)


# -------------------------------------------------------------- resolve_params

class TestResolveParams(unittest.TestCase):

    def test_replaces_named_placeholders(self):
        template = {"entity_id": "{entity_id}", "room": "{room}", "days": "{days}"}
        out = rs.resolve_params(template, {"entity_id": "sensor.kitchen", "room": "厨房", "days": 7})
        self.assertEqual(out, {"entity_id": "sensor.kitchen", "room": "厨房", "days": 7})

    def test_full_token_keeps_context_type(self):
        out = rs.resolve_params({"days": "{days}", "flag": "{flag}", "ratio": "{ratio}"},
                                {"days": 7, "flag": True, "ratio": 0.5})
        self.assertIsInstance(out["days"], int)
        self.assertEqual(out["days"], 7)
        self.assertIs(out["flag"], True)
        self.assertEqual(out["ratio"], 0.5)

    def test_embedded_token_becomes_string(self):
        out = rs.resolve_params({"label": "sensor_{entity_id}_over_{days}_days"},
                                {"entity_id": "lamp", "days": 7})
        self.assertEqual(out["label"], "sensor_lamp_over_7_days")
        self.assertIsInstance(out["label"], str)

    def test_unknown_placeholder_kept_verbatim(self):
        template = {"a": "{unknown}", "b": "x{unknown}y", "c": "{entity_id}{unknown}"}
        out = rs.resolve_params(template, {"entity_id": "e1"})
        self.assertEqual(out["a"], "{unknown}")
        self.assertEqual(out["b"], "x{unknown}y")
        self.assertEqual(out["c"], "e1{unknown}")

    def test_malformed_placeholder_kept_verbatim(self):
        template = {"a": "{}", "b": "{ }", "c": "{1bad}", "d": "{unclosed", "e": "a}b"}
        out = rs.resolve_params(template, {"": "x", "1bad": "y", "unclosed": "z"})
        self.assertEqual(out, template)

    def test_nested_containers_are_resolved(self):
        template = {
            "nested": {"window": "last {days} days", "rooms": ["{room}", "fixed"],
                       "deep": [{"who": "{person}"}]},
            "top": "{room}",
        }
        out = rs.resolve_params(template, {"days": 7, "room": "厨房", "person": "小明"})
        self.assertEqual(out["nested"]["window"], "last 7 days")
        self.assertEqual(out["nested"]["rooms"], ["厨房", "fixed"])
        self.assertEqual(out["nested"]["deep"][0]["who"], "小明")
        self.assertEqual(out["top"], "厨房")

    def test_non_string_values_untouched(self):
        template = {"count": 3, "flag": True, "nothing": None, "list": [1, 2], "blank": ""}
        out = rs.resolve_params(template, {"count": 99})
        self.assertEqual(out, {"count": 3, "flag": True, "nothing": None, "list": [1, 2], "blank": ""})

    def test_input_template_is_not_mutated(self):
        template = {"a": "{x}"}
        rs.resolve_params(template, {"x": 1})
        self.assertEqual(template, {"a": "{x}"})

    def test_unbound_or_none_binding_keeps_placeholder(self):
        out = rs.resolve_params({"a": "{x}", "b": "{y}"}, {"x": None})
        self.assertEqual(out, {"a": "{x}", "b": "{y}"})

    def test_context_defaults_to_empty(self):
        self.assertEqual(rs.resolve_params({"a": "{x}"}), {"a": "{x}"})

    def test_rejects_invalid_arguments(self):
        with self.assertRaises(TypeError):
            rs.resolve_params(["not", "a", "dict"], {})
        with self.assertRaises(TypeError):
            rs.resolve_params({}, 5)


# -------------------------------------------------------------- enumerations

class TestKnownEnumerations(unittest.TestCase):

    def test_known_intents(self):
        intents = rs.list_known_intents()
        self.assertIsInstance(intents, list)
        self.assertTrue(intents)
        self.assertEqual(intents,
                         ["device_usage", "compare", "anomaly", "presence", "routine", "arrival"])

    def test_known_metrics(self):
        metrics = rs.list_known_metrics()
        self.assertTrue(metrics)
        self.assertEqual(metrics, ["duration", "count", "numeric_sum", "state_share", "none"])
        self.assertIn("none", metrics)

    def test_returns_fresh_list_each_call(self):
        first = rs.list_known_intents()
        first.append("mutated")
        self.assertNotIn("mutated", rs.list_known_intents())
        second = rs.list_known_metrics()
        second.clear()
        self.assertTrue(rs.list_known_metrics())

    def test_supporting_enums_non_empty(self):
        self.assertTrue(rs.list_known_object_types())
        self.assertTrue(rs.list_known_time_windows())
        self.assertTrue(rs.list_known_statuses())


# ---------------------------------------------------------------- build_recipe

class TestBuildRecipe(unittest.TestCase):

    def test_build_recipe_fills_derived_fields(self):
        recipe = rs.build_recipe(
            intent="presence", object_type="person", metric="count", time_window="today",
            tool_sequence=[{"tool_name": "count_presence", "params": {"person": "{person}"}}],
            created_at="2026-10-08T13:10:11Z", source_session="sess-001", person="小明",
        )
        self.assertEqual(recipe.recipe_id, rs.make_recipe_id(recipe))
        self.assertEqual(recipe.status, "staging")
        self.assertEqual(recipe.sample_count, 0)
        self.assertEqual(recipe.person, "小明")
        self.assertIsInstance(recipe.tool_sequence[0], rs.ToolStep)

    def test_build_recipe_is_directly_valid(self):
        recipe = rs.build_recipe(
            intent="anomaly", object_type="device", metric="count", time_window="today",
            tool_sequence=[{"tool_name": "count_anomalies", "params": {"days": "{days}"}}],
            created_at="2026-10-08T13:10:11Z", source_session="sess-002",
        )
        self.assertEqual(rs.validate_recipe(recipe, check_id_consistency=True), [])


# ------------------------------------------------------------- dependency rule

class TestDependencyContract(unittest.TestCase):

    def test_module_only_uses_allowed_stdlib(self):
        path = os.path.join(_SRC, "memory_agent", "recipe_schema.py")
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    imported.add(node.module.split(".")[0])
        self.assertTrue(imported.issubset({"json", "hashlib", "dataclasses", "typing"}), imported)


if __name__ == "__main__":
    unittest.main()
