from __future__ import annotations

import unittest
import copy

from antigravity_auth.transform.schema import (
  EMPTY_SCHEMA_PLACEHOLDER_NAME,
  EMPTY_SCHEMA_PLACEHOLDER_DESCRIPTION,
  clean_json_schema,
  to_gemini_schema,
  _append_description_hint,
  _convert_refs_to_hints,
  _convert_const_to_enum,
  _add_enum_hints,
  _add_additional_properties_hints,
  _move_constraints_to_description,
  _merge_all_of,
  _flatten_any_of_one_of,
  _flatten_type_arrays,
  _remove_unsupported_keywords,
  _cleanup_required_fields,
  _add_empty_schema_placeholder,
  _try_merge_enum_from_union,
  _score_schema_option,
)


class TestAppendDescriptionHint(unittest.TestCase):
  def test_append_to_empty_description(self):
    schema = {"type": "string"}
    result = _append_description_hint(schema, "nullable")
    self.assertEqual(result["description"], "nullable")

  def test_append_to_existing_description(self):
    schema = {"type": "string", "description": "A name"}
    result = _append_description_hint(schema, "minLength: 1")
    self.assertEqual(result["description"], "A name (minLength: 1)")

  def test_non_dict_returns_as_is(self):
    self.assertIsNone(_append_description_hint(None, "hint"))
    self.assertEqual(_append_description_hint("foo", "hint"), "foo")


class TestScoreSchemaOption(unittest.TestCase):
  def test_object_scores_3(self):
    score, name = _score_schema_option({"type": "object"})
    self.assertEqual(score, 3)
    self.assertEqual(name, "object")

  def test_has_properties_scores_3(self):
    score, name = _score_schema_option({"properties": {"a": {"type": "string"}}})
    self.assertEqual(score, 3)

  def test_array_scores_2(self):
    score, name = _score_schema_option({"type": "array"})
    self.assertEqual(score, 2)
    self.assertEqual(name, "array")

  def test_has_items_scores_2(self):
    score, name = _score_schema_option({"items": {"type": "string"}})
    self.assertEqual(score, 2)

  def test_non_null_type_scores_1(self):
    score, name = _score_schema_option({"type": "string"})
    self.assertEqual(score, 1)
    self.assertEqual(name, "string")

  def test_null_type_scores_0(self):
    score, name = _score_schema_option({"type": "null"})
    self.assertEqual(score, 0)
    self.assertEqual(name, "null")

  def test_no_type_scores_0(self):
    score, name = _score_schema_option({"description": "something"})
    self.assertEqual(score, 0)
    self.assertEqual(name, "null")

  def test_non_dict_returns_unknown(self):
    score, name = _score_schema_option(None)
    self.assertEqual(score, 0)
    self.assertEqual(name, "unknown")


class TestTryMergeEnumFromUnion(unittest.TestCase):
  def test_const_values_merged(self):
    options = [{"const": "a"}, {"const": "b"}, {"const": "c"}]
    result = _try_merge_enum_from_union(options)
    self.assertEqual(result, ["a", "b", "c"])

  def test_single_enum_values_merged(self):
    options = [{"enum": ["a"]}, {"enum": ["b"]}]
    result = _try_merge_enum_from_union(options)
    self.assertEqual(result, ["a", "b"])

  def test_mixed_const_and_enum(self):
    options = [{"const": "x"}, {"enum": ["y", "z"]}]
    result = _try_merge_enum_from_union(options)
    self.assertEqual(result, ["x", "y", "z"])

  def test_returns_none_for_type_options(self):
    options = [{"type": "string"}, {"type": "number"}]
    result = _try_merge_enum_from_union(options)
    self.assertIsNone(result)

  def test_returns_none_for_complex_options(self):
    options = [{"type": "object", "properties": {"a": {"type": "string"}}}]
    result = _try_merge_enum_from_union(options)
    self.assertIsNone(result)

  def test_returns_none_for_empty_list(self):
    self.assertIsNone(_try_merge_enum_from_union([]))

  def test_returns_none_for_non_list(self):
    self.assertIsNone(_try_merge_enum_from_union("not a list"))


class TestConvertRefsToHints(unittest.TestCase):
  def test_simple_ref(self):
    schema = {"$ref": "#/$defs/Foo"}
    result = _convert_refs_to_hints(schema)
    self.assertEqual(result["type"], "object")
    self.assertEqual(result["description"], "See: Foo")

  def test_ref_preserves_existing_description(self):
    schema = {"$ref": "#/$defs/Foo", "description": "A foo object"}
    result = _convert_refs_to_hints(schema)
    self.assertEqual(result["description"], "A foo object (See: Foo)")

  def test_nested_ref(self):
    schema = {"properties": {"foo": {"$ref": "#/$defs/Foo"}}}
    result = _convert_refs_to_hints(schema)
    self.assertEqual(result["properties"]["foo"]["type"], "object")
    self.assertEqual(result["properties"]["foo"]["description"], "See: Foo")

  def test_non_ref_passthrough(self):
    schema = {"type": "string"}
    result = _convert_refs_to_hints(schema)
    self.assertEqual(result, {"type": "string"})

  def test_array_handling(self):
    schema = [{"$ref": "#/$defs/Foo"}, {"type": "string"}]
    result = _convert_refs_to_hints(schema)
    self.assertEqual(result[0]["type"], "object")
    self.assertEqual(result[1], {"type": "string"})


class TestConvertConstToEnum(unittest.TestCase):
  def test_const_string(self):
    schema = {"const": "foo"}
    result = _convert_const_to_enum(schema)
    self.assertEqual(result, {"enum": ["foo"]})

  def test_const_number(self):
    schema = {"const": 42}
    result = _convert_const_to_enum(schema)
    self.assertEqual(result, {"enum": [42]})

  def test_const_with_type(self):
    schema = {"type": "string", "const": "hello"}
    result = _convert_const_to_enum(schema)
    self.assertEqual(result, {"type": "string", "enum": ["hello"]})

  def test_existing_enum_not_overwritten(self):
    schema = {"const": "foo", "enum": ["a", "b"]}
    result = _convert_const_to_enum(schema)
    self.assertEqual(result, {"const": "foo", "enum": ["a", "b"]})

  def test_nested_const(self):
    schema = {"properties": {"color": {"const": "red"}}}
    result = _convert_const_to_enum(schema)
    self.assertEqual(result["properties"]["color"]["enum"], ["red"])

  def test_non_const_passthrough(self):
    schema = {"type": "string"}
    result = _convert_const_to_enum(schema)
    self.assertEqual(result, {"type": "string"})


class TestAddEnumHints(unittest.TestCase):
  def test_enum_hint_added(self):
    schema = {"type": "string", "enum": ["a", "b", "c"]}
    result = _add_enum_hints(schema)
    self.assertIn("Allowed: a, b, c", result["description"])

  def test_enum_hint_preserves_existing_description(self):
    schema = {"type": "string", "enum": ["a", "b"], "description": "Choices"}
    result = _add_enum_hints(schema)
    self.assertEqual(result["description"], "Choices (Allowed: a, b)")

  def test_single_value_enum_no_hint(self):
    schema = {"type": "string", "enum": ["a"]}
    result = _add_enum_hints(schema)
    self.assertNotIn("description", result)

  def test_many_values_enum_no_hint(self):
    schema = {"type": "string", "enum": list("abcdefghijk")}
    result = _add_enum_hints(schema)
    self.assertNotIn("description", result)

  def test_no_enum_passthrough(self):
    schema = {"type": "string"}
    result = _add_enum_hints(schema)
    self.assertEqual(result, {"type": "string"})

  def test_nested_enum_hint(self):
    schema = {"properties": {"color": {"type": "string", "enum": ["red", "green", "blue"]}}}
    result = _add_enum_hints(schema)
    desc = result["properties"]["color"]["description"]
    self.assertIn("Allowed: red, green, blue", desc)


class TestAddAdditionalPropertiesHints(unittest.TestCase):
  def test_additional_properties_false_adds_hint(self):
    schema = {"type": "object", "additionalProperties": False}
    result = _add_additional_properties_hints(schema)
    self.assertIn("No extra properties allowed", result["description"])

  def test_additional_properties_true_no_hint(self):
    schema = {"type": "object", "additionalProperties": True}
    result = _add_additional_properties_hints(schema)
    self.assertNotIn("description", result)

  def test_no_additional_properties_passthrough(self):
    schema = {"type": "object"}
    result = _add_additional_properties_hints(schema)
    self.assertEqual(result, {"type": "object"})

  def test_nested_additional_properties(self):
    schema = {
      "type": "object",
      "properties": {
        "inner": {"type": "object", "additionalProperties": False},
      },
    }
    result = _add_additional_properties_hints(schema)
    self.assertIn("No extra properties allowed", result["properties"]["inner"]["description"])


class TestMoveConstraintsToDescription(unittest.TestCase):
  def test_min_length(self):
    schema = {"type": "string", "minLength": 1}
    result = _move_constraints_to_description(schema)
    self.assertIn("minLength: 1", result["description"])

  def test_max_length(self):
    schema = {"type": "string", "maxLength": 100}
    result = _move_constraints_to_description(schema)
    self.assertIn("maxLength: 100", result["description"])

  def test_pattern(self):
    schema = {"type": "string", "pattern": "^[a-z]+$"}
    result = _move_constraints_to_description(schema)
    self.assertIn("pattern: ^[a-z]+$", result["description"])

  def test_multiple_constraints(self):
    schema = {"type": "string", "minLength": 1, "maxLength": 100}
    result = _move_constraints_to_description(schema)
    self.assertIn("minLength: 1", result["description"])
    self.assertIn("maxLength: 100", result["description"])

  def test_preserves_existing_description(self):
    schema = {"type": "string", "minLength": 1, "description": "A name"}
    result = _move_constraints_to_description(schema)
    self.assertEqual(result["description"], "A name (minLength: 1)")

  def test_nested_constraint(self):
    schema = {"properties": {"name": {"type": "string", "minLength": 1}}}
    result = _move_constraints_to_description(schema)
    self.assertIn("minLength: 1", result["properties"]["name"]["description"])

  def test_no_constraint_passthrough(self):
    schema = {"type": "string"}
    result = _move_constraints_to_description(schema)
    self.assertEqual(result, {"type": "string"})


class TestMergeAllOf(unittest.TestCase):
  def test_merge_properties(self):
    schema = {
      "allOf": [
        {"properties": {"a": {"type": "string"}}},
        {"properties": {"b": {"type": "number"}}},
      ],
    }
    result = _merge_all_of(schema)
    self.assertIn("a", result["properties"])
    self.assertIn("b", result["properties"])
    self.assertNotIn("allOf", result)

  def test_merge_required(self):
    schema = {
      "allOf": [
        {"required": ["a"]},
        {"required": ["b"]},
      ],
    }
    result = _merge_all_of(schema)
    self.assertIn("a", result["required"])
    self.assertIn("b", result["required"])
    self.assertNotIn("allOf", result)

  def test_merge_required_dedup(self):
    schema = {
      "allOf": [
        {"required": ["a", "b"]},
        {"required": ["b", "c"]},
      ],
    }
    result = _merge_all_of(schema)
    self.assertEqual(len(result["required"]), 3)

  def test_merge_with_main_schema_properties(self):
    schema = {
      "type": "object",
      "properties": {"a": {"type": "string"}},
      "allOf": [
        {"properties": {"b": {"type": "number"}}},
      ],
    }
    result = _merge_all_of(schema)
    self.assertIn("a", result["properties"])
    self.assertIn("b", result["properties"])
    self.assertNotIn("allOf", result)

  def test_main_properties_take_priority(self):
    schema = {
      "properties": {"a": {"type": "string"}},
      "allOf": [
        {"properties": {"a": {"type": "number"}}},
      ],
    }
    result = _merge_all_of(schema)
    self.assertEqual(result["properties"]["a"]["type"], "number")

  def test_no_allof_passthrough(self):
    schema = {"type": "string"}
    result = _merge_all_of(schema)
    self.assertEqual(result, {"type": "string"})

  def test_nested_allof(self):
    schema = {
      "properties": {
        "nested": {
          "allOf": [
            {"properties": {"x": {"type": "string"}}},
            {"properties": {"y": {"type": "number"}}},
          ],
        },
      },
    }
    result = _merge_all_of(schema)
    self.assertIn("x", result["properties"]["nested"]["properties"])
    self.assertIn("y", result["properties"]["nested"]["properties"])
    self.assertNotIn("allOf", result["properties"]["nested"])


class TestFlattenAnyOfOneOf(unittest.TestCase):
  def test_anyof_enum_pattern(self):
    schema = {"anyOf": [{"const": "a"}, {"const": "b"}, {"const": "c"}]}
    result = _flatten_any_of_one_of(schema)
    self.assertEqual(result["type"], "string")
    self.assertEqual(result["enum"], ["a", "b", "c"])

  def test_anyof_type_union(self):
    schema = {"anyOf": [{"type": "string"}, {"type": "number"}]}
    result = _flatten_any_of_one_of(schema)
    self.assertEqual(result["type"], "string")
    self.assertIn("Accepts: string | number", result["description"])

  def test_anyof_with_object_preferred(self):
    schema = {
      "anyOf": [
        {"type": "string"},
        {"type": "object", "properties": {"a": {"type": "string"}}},
      ],
    }
    result = _flatten_any_of_one_of(schema)
    self.assertEqual(result["type"], "object")

  def test_oneof_flatten(self):
    schema = {"oneOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}]}
    result = _flatten_any_of_one_of(schema)
    self.assertEqual(result["type"], "string")
    self.assertIn("Accepts: string | number | boolean", result["description"])

  def test_preserves_parent_description(self):
    schema = {
      "description": "A value",
      "anyOf": [{"type": "string"}, {"type": "number"}],
    }
    result = _flatten_any_of_one_of(schema)
    self.assertEqual(result["type"], "string")
    self.assertIn("A value", result["description"])

  def test_no_anyof_oneof_passthrough(self):
    schema = {"type": "string"}
    result = _flatten_any_of_one_of(schema)
    self.assertEqual(result, {"type": "string"})

  def test_nested_anyof(self):
    schema = {
      "properties": {
        "value": {"anyOf": [{"const": "yes"}, {"const": "no"}]},
      },
    }
    result = _flatten_any_of_one_of(schema)
    self.assertEqual(result["properties"]["value"]["type"], "string")
    self.assertEqual(result["properties"]["value"]["enum"], ["yes", "no"])


class TestFlattenTypeArrays(unittest.TestCase):
  def test_string_null_becomes_string_with_nullable(self):
    schema = {"type": ["string", "null"]}
    result = _flatten_type_arrays(schema)
    self.assertEqual(result["type"], "string")
    self.assertIn("nullable", result["description"])

  def test_multiple_types(self):
    schema = {"type": ["string", "number", "boolean"]}
    result = _flatten_type_arrays(schema)
    self.assertEqual(result["type"], "string")
    self.assertIn("Accepts: string | number | boolean", result["description"])

  def test_single_type_passthrough(self):
    schema = {"type": "string"}
    result = _flatten_type_arrays(schema)
    self.assertEqual(result, {"type": "string"})

  def test_nullable_field_preserved_in_required(self):
    schema = {
      "type": "object",
      "properties": {
        "name": {"type": ["string", "null"]},
        "age": {"type": "number"},
      },
      "required": ["name", "age"],
    }
    result = _flatten_type_arrays(schema)
    self.assertEqual(result["required"], ["name", "age"])

  def test_all_nullable_preserves_required(self):
    schema = {
      "type": "object",
      "properties": {
        "name": {"type": ["string", "null"]},
      },
      "required": ["name"],
    }
    result = _flatten_type_arrays(schema)
    self.assertEqual(result["required"], ["name"])

  def test_no_type_array_passthrough(self):
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    result = _flatten_type_arrays(schema)
    self.assertEqual(result, schema)


class TestRemoveUnsupportedKeywords(unittest.TestCase):
  def test_removes_schema(self):
    schema = {"type": "string", "$schema": "http://json-schema.org/draft-07/schema#"}
    result = _remove_unsupported_keywords(schema)
    self.assertNotIn("$schema", result)

  def test_removes_defs(self):
    schema = {"type": "object", "$defs": {"Foo": {"type": "string"}}}
    result = _remove_unsupported_keywords(schema)
    self.assertNotIn("$defs", result)

  def test_removes_title(self):
    schema = {"type": "string", "title": "My String"}
    result = _remove_unsupported_keywords(schema)
    self.assertNotIn("title", result)

  def test_removes_multiple_keywords(self):
    schema = {"type": "string", "minLength": 1, "maxLength": 100, "pattern": "^foo"}
    result = _remove_unsupported_keywords(schema)
    self.assertNotIn("minLength", result)
    self.assertNotIn("maxLength", result)
    self.assertNotIn("pattern", result)

  def test_preserves_type_and_description(self):
    schema = {"type": "object", "description": "A thing", "$id": "thing"}
    result = _remove_unsupported_keywords(schema)
    self.assertIn("type", result)
    self.assertIn("description", result)
    self.assertNotIn("$id", result)

  def test_properties_names_preserved(self):
    schema = {"properties": {"minLength": {"type": "string"}}}
    result = _remove_unsupported_keywords(schema)
    self.assertIn("properties", result)

    self.assertIn("minLength", result["properties"])


class TestCleanupRequiredFields(unittest.TestCase):
  def test_removes_non_existent_required(self):
    schema = {
      "type": "object",
      "properties": {"a": {"type": "string"}},
      "required": ["a", "b", "c"],
    }
    result = _cleanup_required_fields(schema)
    self.assertEqual(result["required"], ["a"])

  def test_removes_required_when_all_missing(self):
    schema = {
      "type": "object",
      "properties": {"a": {"type": "string"}},
      "required": ["x", "y"],
    }
    result = _cleanup_required_fields(schema)
    self.assertNotIn("required", result)

  def test_valid_required_unchanged(self):
    schema = {
      "type": "object",
      "properties": {"a": {"type": "string"}, "b": {"type": "number"}},
      "required": ["a", "b"],
    }
    result = _cleanup_required_fields(schema)
    self.assertEqual(result["required"], ["a", "b"])

  def test_no_properties_keeps_required(self):
    schema = {"required": ["a"]}
    result = _cleanup_required_fields(schema)
    self.assertEqual(result["required"], ["a"])

  def test_nested_cleanup(self):
    schema = {
      "type": "object",
      "properties": {
        "inner": {
          "type": "object",
          "properties": {"x": {"type": "string"}},
          "required": ["x", "y"],
        },
      },
    }
    result = _cleanup_required_fields(schema)
    self.assertEqual(result["properties"]["inner"]["required"], ["x"])


class TestAddEmptySchemaPlaceholder(unittest.TestCase):
  def test_empty_object_adds_placeholder(self):
    schema = {"type": "object"}
    result = _add_empty_schema_placeholder(schema)
    self.assertIn(EMPTY_SCHEMA_PLACEHOLDER_NAME, result["properties"])
    self.assertEqual(
      result["properties"][EMPTY_SCHEMA_PLACEHOLDER_NAME]["type"], "boolean",
    )
    self.assertEqual(
      result["properties"][EMPTY_SCHEMA_PLACEHOLDER_NAME]["description"],
      EMPTY_SCHEMA_PLACEHOLDER_DESCRIPTION,
    )
    self.assertEqual(result["required"], [EMPTY_SCHEMA_PLACEHOLDER_NAME])

  def test_object_with_properties_no_placeholder(self):
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    result = _add_empty_schema_placeholder(schema)
    self.assertNotIn(EMPTY_SCHEMA_PLACEHOLDER_NAME, result["properties"])
    self.assertIn("a", result["properties"])

  def test_non_object_type_no_placeholder(self):
    schema = {"type": "string"}
    result = _add_empty_schema_placeholder(schema)
    self.assertNotIn("properties", result)

  def test_nested_empty_object(self):
    schema = {
      "type": "object",
      "properties": {
        "inner": {"type": "object"},
      },
    }
    result = _add_empty_schema_placeholder(schema)
    inner = result["properties"]["inner"]
    self.assertIn(EMPTY_SCHEMA_PLACEHOLDER_NAME, inner["properties"])


class TestCleanJsonSchemaIntegration(unittest.TestCase):
  def test_basic_passthrough(self):
    schema = {"type": "string"}
    result = clean_json_schema(schema)
    self.assertEqual(result, {"type": "string"})

  def test_ref_conversion(self):
    schema = {"$ref": "#/$defs/Foo"}
    result = clean_json_schema(schema)
    self.assertEqual(result["type"], "object")
    self.assertEqual(result["description"], "See: Foo")

  def test_const_to_enum(self):
    schema = {"const": "foo"}
    result = clean_json_schema(schema)
    self.assertEqual(result, {"enum": ["foo"], "type": "string"})

  def test_enum_hint_integration(self):
    schema = {"type": "string", "enum": ["a", "b", "c"]}
    result = clean_json_schema(schema)
    self.assertIn("Allowed: a, b, c", result["description"])

  def test_additional_properties_false(self):
    schema = {"type": "object", "additionalProperties": False}
    result = clean_json_schema(schema)
    self.assertNotIn("additionalProperties", result)
    self.assertIn("No extra properties allowed", result["description"])

  def test_constraints_moved_to_description(self):
    schema = {"type": "string", "minLength": 1, "maxLength": 100}
    result = clean_json_schema(schema)
    self.assertNotIn("minLength", result)
    self.assertNotIn("maxLength", result)
    self.assertIn("minLength: 1", result["description"])
    self.assertIn("maxLength: 100", result["description"])

  def test_allof_merged(self):
    schema = {
      "allOf": [
        {"properties": {"a": {"type": "string"}}},
        {"properties": {"b": {"type": "number"}}},
      ],
    }
    result = clean_json_schema(schema)
    self.assertIn("a", result["properties"])
    self.assertIn("b", result["properties"])
    self.assertNotIn("allOf", result)

  def test_anyof_enum_pattern(self):
    schema = {"anyOf": [{"const": "text"}, {"const": "markdown"}, {"const": "html"}]}
    result = clean_json_schema(schema)
    self.assertEqual(result["type"], "string")
    self.assertEqual(result["enum"], ["text", "markdown", "html"])

  def test_anyof_type_union(self):
    schema = {"anyOf": [{"type": "string"}, {"type": "number"}]}
    result = clean_json_schema(schema)
    self.assertEqual(result["type"], "string")
    self.assertIn("Accepts: string | number", result["description"])

  def test_oneof_flatten(self):
    schema = {"oneOf": [{"type": "string"}, {"type": "boolean"}]}
    result = clean_json_schema(schema)
    self.assertEqual(result["type"], "string")
    self.assertIn("Accepts: string | boolean", result["description"])

  def test_type_arrays_flattened(self):
    schema = {
      "type": "object",
      "properties": {
        "name": {"type": ["string", "null"]},
      },
      "required": ["name"],
    }
    result = clean_json_schema(schema)
    self.assertEqual(result["properties"]["name"]["type"], "string")
    self.assertIn("nullable", result["properties"]["name"]["description"])
    self.assertEqual(result["required"], ["name"])

  def test_non_nullable_description_does_not_remove_required(self):
    schema = {
      "type": "object",
      "properties": {"id": {"type": "string", "description": "non-nullable identifier"}},
      "required": ["id"],
    }
    cleaned = clean_json_schema(schema)
    self.assertEqual(cleaned.get("required"), ["id"])

  def test_nullable_type_array_preserves_required_presence(self):
    schema = {
      "type": "object",
      "properties": {"id": {"type": ["string", "null"]}},
      "required": ["id"],
    }
    cleaned = clean_json_schema(schema)
    self.assertEqual(cleaned.get("required"), ["id"])
    self.assertIn("nullable", cleaned["properties"]["id"].get("description", ""))

  def test_numeric_enum_values_are_not_stringified(self):
    schema = {"type": "object", "properties": {"n": {"enum": [1, 2, 3]}}}
    out = clean_json_schema(schema)
    self.assertEqual(out["properties"]["n"]["enum"], [1, 2, 3])
    self.assertEqual(out["properties"]["n"]["type"], "integer")

  def test_boolean_enum_values_are_not_stringified(self):
    schema = {"type": "object", "properties": {"flag": {"enum": [True, False]}}}
    out = clean_json_schema(schema)
    self.assertEqual(out["properties"]["flag"]["enum"], [True, False])
    self.assertEqual(out["properties"]["flag"]["type"], "boolean")

  def test_property_named_enum_still_gets_enum_type_inferred(self):
    schema = {"type": "object", "properties": {"enum": {"enum": ["a", "b"]}}}
    out = clean_json_schema(schema)
    self.assertEqual(out["properties"]["enum"]["enum"], ["a", "b"])
    self.assertEqual(out["properties"]["enum"]["type"], "string")

  def test_nested_nullable_required_field_preserved_in_required(self):
    schema = {
      "type": "object",
      "properties": {
        "outer": {
          "type": "object",
          "required": ["maybe"],
          "properties": {"maybe": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        }
      },
    }
    out = clean_json_schema(schema)
    self.assertEqual(out["properties"]["outer"].get("required"), ["maybe"])

  def test_unsupported_keywords_removed(self):
    schema = {"type": "string", "title": "Test", "$schema": "http://...", "$id": "test"}
    result = clean_json_schema(schema)
    self.assertNotIn("title", result)
    self.assertNotIn("$schema", result)
    self.assertNotIn("$id", result)

  def test_required_cleanup(self):
    schema = {
      "type": "object",
      "properties": {"a": {"type": "string"}},
      "required": ["a", "missing_field"],
    }
    result = clean_json_schema(schema)
    self.assertEqual(result["required"], ["a"])

  def test_empty_object_placeholder(self):
    schema = {"type": "object"}
    result = clean_json_schema(schema)
    self.assertIn(EMPTY_SCHEMA_PLACEHOLDER_NAME, result["properties"])
    self.assertEqual(result["required"], [EMPTY_SCHEMA_PLACEHOLDER_NAME])

  def test_already_clean_schema_noop(self):
    schema = {"type": "string", "description": "A simple string"}
    result = clean_json_schema(schema)
    self.assertEqual(result, {"type": "string", "description": "A simple string"})

  def test_deeply_nested_schema(self):
    schema = {
      "type": "object",
      "properties": {
        "metadata": {
          "type": "object",
          "properties": {
            "tags": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "name": {"type": "string", "minLength": 1},
                  "value": {"type": ["string", "null"]},
                },
                "required": ["name", "value"],
              },
            },
          },
        },
      },
      "required": ["metadata"],
    }
    result = clean_json_schema(schema)
    self.assertEqual(result["type"], "object")
    self.assertIn("metadata", result["properties"])
    tags = result["properties"]["metadata"]["properties"]["tags"]
    self.assertEqual(tags["type"], "array")
    items = tags["items"]
    self.assertIn("name", items["properties"])
    self.assertIn("value", items["properties"])
    self.assertIn("minLength: 1", items["properties"]["name"]["description"])
    self.assertIn("nullable", items["properties"]["value"]["description"])
    self.assertEqual(items["required"], ["name", "value"])

  def test_immutable_input(self):
    original = {"type": "string", "enum": ["a", "b", "c"]}
    original_copy = copy.deepcopy(original)
    clean_json_schema(original)
    self.assertEqual(original, original_copy)


class TestToGeminiSchema(unittest.TestCase):
  def test_type_uppercased(self):
    schema = {"type": "object"}
    result = to_gemini_schema(schema)
    self.assertEqual(result["type"], "OBJECT")

  def test_nullable_string_type_array(self):
    schema = {"type": ["string", "null"]}
    result = to_gemini_schema(schema)
    self.assertEqual(result["type"], "STRING")
    self.assertIs(result["nullable"], True)

  def test_nullable_integer_type_array(self):
    schema = {"type": ["integer", "null"]}
    result = to_gemini_schema(schema)
    self.assertEqual(result["type"], "INTEGER")
    self.assertIs(result["nullable"], True)

  def test_unsupported_fields_removed(self):
    schema = {"type": "object", "additionalProperties": False, "$schema": "http://..."}
    result = to_gemini_schema(schema)
    self.assertNotIn("additionalProperties", result)
    self.assertNotIn("$schema", result)

  def test_nested_properties(self):
    schema = {
      "type": "object",
      "properties": {
        "name": {"type": "string"},
        "age": {"type": "number"},
      },
    }
    result = to_gemini_schema(schema)
    self.assertEqual(result["properties"]["name"]["type"], "STRING")
    self.assertEqual(result["properties"]["age"]["type"], "NUMBER")

  def test_array_without_items_gets_default(self):
    schema = {"type": "array"}
    result = to_gemini_schema(schema)
    self.assertEqual(result["type"], "ARRAY")
    self.assertEqual(result["items"], {"type": "STRING"})

  def test_array_with_items_preserved(self):
    schema = {"type": "array", "items": {"type": "string"}}
    result = to_gemini_schema(schema)
    self.assertEqual(result["type"], "ARRAY")
    self.assertEqual(result["items"]["type"], "STRING")

  def test_required_filtered_to_existing_properties(self):
    schema = {
      "type": "object",
      "properties": {"a": {"type": "string"}},
      "required": ["a", "nonexistent"],
    }
    result = to_gemini_schema(schema)
    self.assertEqual(result["required"], ["a"])

  def test_anyof_transformed(self):
    schema = {"anyOf": [{"type": "string"}, {"type": "number"}]}
    result = to_gemini_schema(schema)
    self.assertEqual(result["anyOf"][0]["type"], "STRING")
    self.assertEqual(result["anyOf"][1]["type"], "NUMBER")

  def test_allof_transformed(self):
    schema = {"allOf": [{"type": "string"}, {"type": "number"}]}
    result = to_gemini_schema(schema)
    self.assertEqual(result["allOf"][0]["type"], "STRING")

  def test_enum_preserved(self):
    schema = {"type": "string", "enum": ["a", "b", "c"]}
    result = to_gemini_schema(schema)
    self.assertEqual(result["enum"], ["a", "b", "c"])

  def test_non_dict_returns_as_is(self):
    self.assertIsNone(to_gemini_schema(None))
    self.assertEqual(to_gemini_schema("string"), "string")


if __name__ == "__main__":
  unittest.main()
