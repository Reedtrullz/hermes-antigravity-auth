"""JSON Schema sanitization for Antigravity API compatibility (const, $ref removal)."""
from __future__ import annotations

# ============================================================================
# JSON SCHEMA CLEANING FOR ANTIGRAVITY API
# Ported from TypeScript src/plugin/request-helpers.ts
# ============================================================================

EMPTY_SCHEMA_PLACEHOLDER_NAME = "_placeholder"
EMPTY_SCHEMA_PLACEHOLDER_DESCRIPTION = "Placeholder. Always pass true."

UNSUPPORTED_CONSTRAINTS = [
  "minLength", "maxLength", "exclusiveMinimum", "exclusiveMaximum",
  "pattern", "minItems", "maxItems", "format", "default", "examples",
]

UNSUPPORTED_KEYWORDS = [
  *UNSUPPORTED_CONSTRAINTS,
  "$schema", "$defs", "definitions", "const", "$ref", "additionalProperties",
  "propertyNames", "title", "$id", "$comment",
]

# ============================================================================
# HELPERS
# ============================================================================


def _append_description_hint(schema: dict, hint: str) -> dict:
  """Appends a hint to a schema's description field."""
  if not isinstance(schema, dict):
    return schema
  existing = schema.get("description", "")
  if not isinstance(existing, str):
    existing = ""
  new_description = f"{existing} ({hint})" if existing else hint
  return {**schema, "description": new_description}


def _score_schema_option(schema) -> tuple[int, str]:
  """Scores a schema option for selection in anyOf/oneOf flattening.
  Higher score = more preferred.
  """
  if not isinstance(schema, dict):
    return (0, "unknown")

  schema_type = schema.get("type")

  # Object or has properties = highest priority
  if schema_type == "object" or "properties" in schema:
    return (3, "object")

  # Array or has items = second priority
  if schema_type == "array" or "items" in schema:
    return (2, "array")

  # Any other non-null type
  if schema_type and schema_type != "null":
    return (1, schema_type)

  # Null or no type
  return (0, schema_type or "null")


def _try_merge_enum_from_union(options: list) -> list[str] | None:
  """Checks if an anyOf/oneOf array represents enum choices.
  Returns merged enum values if so, otherwise None.
  """
  if not isinstance(options, list) or not options:
    return None

  enum_values: list[str] = []

  for option in options:
    if not isinstance(option, dict):
      return None

    # Check for const value
    if "const" in option:
      enum_values.append(str(option["const"]))
      continue

    # Check for single-value enum
    if isinstance(option.get("enum"), list) and len(option["enum"]) == 1:
      enum_values.append(str(option["enum"][0]))
      continue

    # Check for multi-value enum (merge all values)
    if isinstance(option.get("enum"), list) and len(option["enum"]) > 0:
      for val in option["enum"]:
        enum_values.append(str(val))
      continue

    # If option has complex structure, it's not a simple enum
    if option.get("properties") or option.get("items") or \
       option.get("anyOf") or option.get("oneOf") or option.get("allOf"):
      return None

    # If option has only type (no const/enum), it's not an enum pattern
    if option.get("type") and "const" not in option and "enum" not in option:
      return None

  # Only return if we found actual enum values
  return enum_values if enum_values else None


# ============================================================================
# PHASE 1: CONVERT AND ADD HINTS
# ============================================================================


def _convert_refs_to_hints(schema):
  """Phase 1a: Converts $ref to description hints."""
  if isinstance(schema, list):
    return [_convert_refs_to_hints(item) for item in schema]

  if not isinstance(schema, dict):
    return schema

  # If this object has $ref, replace it with a hint
  if isinstance(schema.get("$ref"), str):
    ref_val = schema["$ref"]
    def_name = ref_val.split("/")[-1] if "/" in ref_val else ref_val
    hint = f"See: {def_name}"
    existing_desc = schema.get("description", "")
    if not isinstance(existing_desc, str):
      existing_desc = ""
    new_description = f"{existing_desc} ({hint})" if existing_desc else hint
    return {"type": "object", "description": new_description}

  # Recursively process all properties
  result = {}
  for key, value in schema.items():
    result[key] = _convert_refs_to_hints(value)
  return result


def _convert_const_to_enum(schema):
  """Phase 1b: Converts const to enum."""
  if isinstance(schema, list):
    return [_convert_const_to_enum(item) for item in schema]

  if not isinstance(schema, dict):
    return schema

  result = {}
  for key, value in schema.items():
    if key == "const" and "enum" not in schema:
      result["enum"] = [value]
    else:
      result[key] = _convert_const_to_enum(value)
  return result


def _add_enum_hints(schema):
  """Phase 1c: Adds enum hints to description.
  Only for 2-10 items.
  """
  if isinstance(schema, list):
    return [_add_enum_hints(item) for item in schema]

  if not isinstance(schema, dict):
    return schema

  result = {**schema}

  # Add enum hint if enum has 2-10 items
  if isinstance(result.get("enum"), list) and 1 < len(result["enum"]) <= 10:
    vals = ", ".join(str(v) for v in result["enum"])
    result = _append_description_hint(result, f"Allowed: {vals}")

  # Recursively process nested objects
  for key, value in result.items():
    if key != "enum" and isinstance(value, (dict, list)):
      result[key] = _add_enum_hints(value)

  return result


def _add_additional_properties_hints(schema):
  """Phase 1d: Adds additionalProperties hints."""
  if isinstance(schema, list):
    return [_add_additional_properties_hints(item) for item in schema]

  if not isinstance(schema, dict):
    return schema

  result = {**schema}

  if result.get("additionalProperties") is False:
    result = _append_description_hint(result, "No extra properties allowed")

  # Recursively process nested objects
  for key, value in result.items():
    if key != "additionalProperties" and isinstance(value, (dict, list)):
      result[key] = _add_additional_properties_hints(value)

  return result


def _move_constraints_to_description(schema):
  """Phase 1e: Moves unsupported constraints to description hints."""
  if isinstance(schema, list):
    return [_move_constraints_to_description(item) for item in schema]

  if not isinstance(schema, dict):
    return schema

  result = {**schema}

  # Move constraint values to description
  for constraint in UNSUPPORTED_CONSTRAINTS:
    if constraint in result and not isinstance(result[constraint], dict):
      result = _append_description_hint(result, f"{constraint}: {result[constraint]}")

  # Recursively process nested objects
  for key, value in result.items():
    if isinstance(value, (dict, list)):
      result[key] = _move_constraints_to_description(value)

  return result


# ============================================================================
# PHASE 2: FLATTEN COMPLEX STRUCTURES
# ============================================================================


def _merge_all_of(schema):
  """Phase 2a: Merges allOf schemas into a single object."""
  if isinstance(schema, list):
    return [_merge_all_of(item) for item in schema]

  if not isinstance(schema, dict):
    return schema

  result = {**schema}

  # If this object has allOf, merge its contents
  if isinstance(result.get("allOf"), list):
    merged = {}
    merged_required: list[str] = []

    for item in result["allOf"]:
      if not isinstance(item, dict):
        continue

      # Merge properties
      if isinstance(item.get("properties"), dict):
        if "properties" not in merged:
          merged["properties"] = {}
        merged["properties"].update(item["properties"])

      # Merge required arrays
      if isinstance(item.get("required"), list):
        for req in item["required"]:
          if req not in merged_required:
            merged_required.append(req)

      # Copy other fields from allOf items
      for key, value in item.items():
        if key not in ("properties", "required") and key not in merged:
          merged[key] = value

    # Apply merged content to result
    if "properties" in merged:
      if "properties" not in result:
        result["properties"] = {}
      result["properties"].update(merged["properties"])

    if merged_required:
      existing_required = result.get("required", [])
      if not isinstance(existing_required, list):
        existing_required = []
      result["required"] = list(dict.fromkeys(existing_required + merged_required))

    # Copy other merged fields
    for key, value in merged.items():
      if key not in ("properties", "required") and key not in result:
        result[key] = value

    del result["allOf"]

  # Recursively process nested objects
  for key, value in list(result.items()):
    if isinstance(value, (dict, list)):
      result[key] = _merge_all_of(value)

  return result


def _flatten_any_of_one_of(schema):
  """Phase 2b: Flattens anyOf/oneOf to best option with type hints."""
  if isinstance(schema, list):
    return [_flatten_any_of_one_of(item) for item in schema]

  if not isinstance(schema, dict):
    return schema

  result = {**schema}

  # Process anyOf or oneOf
  for union_key in ("anyOf", "oneOf"):
    if isinstance(result.get(union_key), list) and len(result[union_key]) > 0:
      options = result[union_key]
      parent_desc = result.get("description", "")
      if not isinstance(parent_desc, str):
        parent_desc = ""

      # First, check if this is an enum pattern
      merged_enum = _try_merge_enum_from_union(options)
      if merged_enum is not None:
        rest = {k: v for k, v in result.items() if k != union_key}
        result = {**rest, "type": "string", "enum": merged_enum}
        if parent_desc:
          result["description"] = parent_desc
        continue

      # Not an enum pattern - use standard flattening logic
      best_idx = 0
      best_score = -1
      all_types: list[str] = []

      for i, option in enumerate(options):
        score, type_name = _score_schema_option(option)
        if type_name:
          all_types.append(type_name)
        if score > best_score:
          best_score = score
          best_idx = i

      # Select the best option and flatten it recursively
      best_option = options[best_idx]
      if isinstance(best_option, dict):
        selected = _flatten_any_of_one_of(best_option)
      else:
        selected = {"type": "string"}

      # Preserve parent description
      if parent_desc:
        child_desc = selected.get("description", "")
        if not isinstance(child_desc, str):
          child_desc = ""
        if child_desc and child_desc != parent_desc:
          selected = {**selected, "description": f"{parent_desc} ({child_desc})"}
        elif not child_desc:
          selected = {**selected, "description": parent_desc}

      if len(all_types) > 1:
        unique_types = list(dict.fromkeys(all_types))
        hint = f"Accepts: {' | '.join(unique_types)}"
        selected = _append_description_hint(selected, hint)

      # Replace result with selected schema, preserving other fields
      rest = {k: v for k, v in result.items() if k not in (union_key, "description")}
      result = {**rest, **selected}

  # Recursively process nested objects
  for key, value in list(result.items()):
    if isinstance(value, (dict, list)):
      result[key] = _flatten_any_of_one_of(value)

  return result


def _flatten_type_arrays(schema, _nullable_fields: set | None = None):
  """Phase 2c: Flattens type arrays to single type with nullable hint."""
  if isinstance(schema, list):
    return [_flatten_type_arrays(item) for item in schema]

  if not isinstance(schema, dict):
    return schema

  result = {**schema}
  nullable_fields = set() if _nullable_fields is None else _nullable_fields
  is_root = _nullable_fields is None

  # Handle type array at this level
  if isinstance(result.get("type"), list):
    types = result["type"]
    has_null = "null" in types
    non_null_types = [t for t in types if t != "null"]

    first_type = non_null_types[0] if non_null_types else "string"
    result["type"] = first_type

    # Add hint for multiple types
    if len(non_null_types) > 1:
      result = _append_description_hint(
        result, f"Accepts: {' | '.join(non_null_types)}"
      )

    # Add nullable hint
    if has_null:
      result = _append_description_hint(result, "nullable")

  # Recursively process properties
  if isinstance(result.get("properties"), dict):
    new_props = {}
    for prop_key, prop_value in result["properties"].items():
      processed = _flatten_type_arrays(prop_value, nullable_fields)
      new_props[prop_key] = processed
      # Track nullable fields for required cleanup
      if isinstance(processed, dict) and \
         isinstance(processed.get("description"), str) and \
         "nullable" in processed["description"]:
        nullable_fields.add(prop_key)
    result["properties"] = new_props

  # Remove nullable fields from required (only at root)
  if is_root and isinstance(result.get("required"), list) and nullable_fields:
    filtered = [r for r in result["required"] if r not in nullable_fields]
    if filtered:
      result["required"] = filtered
    else:
      del result["required"]

  # Recursively process other nested objects
  for key, value in list(result.items()):
    if key != "properties" and isinstance(value, (dict, list)):
      result[key] = _flatten_type_arrays(value)

  return result


# ============================================================================
# PHASE 3: CLEANUP
# ============================================================================


def _remove_unsupported_keywords(schema):
  """Phase 3a: Removes unsupported keywords after hints have been extracted."""
  if isinstance(schema, list):
    return [_remove_unsupported_keywords(item) for item in schema]

  if not isinstance(schema, dict):
    return schema

  result = {}
  for key, value in schema.items():
    if key in UNSUPPORTED_KEYWORDS:
      continue

    if isinstance(value, dict):
      if key == "properties":
        props_result = {}
        for prop_name, prop_schema in value.items():
          props_result[prop_name] = _remove_unsupported_keywords(prop_schema)
        result[key] = props_result
      else:
        result[key] = _remove_unsupported_keywords(value)
    elif isinstance(value, list):
      result[key] = [_remove_unsupported_keywords(item) for item in value]
    else:
      result[key] = value

  return result


def _cleanup_required_fields(schema):
  """Phase 3b: Removes required entries that don't exist in properties."""
  if isinstance(schema, list):
    return [_cleanup_required_fields(item) for item in schema]

  if not isinstance(schema, dict):
    return schema

  result = {**schema}

  # Clean up required array if properties exist
  if isinstance(result.get("required"), list) and \
     isinstance(result.get("properties"), dict):
    valid_required = [r for r in result["required"] if r in result["properties"]]
    if not valid_required:
      del result["required"]
    elif len(valid_required) != len(result["required"]):
      result["required"] = valid_required

  # Recursively process nested objects
  for key, value in list(result.items()):
    if isinstance(value, (dict, list)):
      result[key] = _cleanup_required_fields(value)

  return result


# ============================================================================
# PHASE 4: PLACEHOLDER
# ============================================================================


def _add_empty_schema_placeholder(schema):
  """Phase 4: Adds placeholder property for empty object schemas."""
  if isinstance(schema, list):
    return [_add_empty_schema_placeholder(item) for item in schema]

  if not isinstance(schema, dict):
    return schema

  result = {**schema}

  # Check if this is an empty object schema
  if result.get("type") == "object":
    has_properties = isinstance(result.get("properties"), dict) and \
      len(result["properties"]) > 0

    if not has_properties:
      result["properties"] = {
        EMPTY_SCHEMA_PLACEHOLDER_NAME: {
          "type": "boolean",
          "description": EMPTY_SCHEMA_PLACEHOLDER_DESCRIPTION,
        },
      }
      result["required"] = [EMPTY_SCHEMA_PLACEHOLDER_NAME]

  # Recursively process nested objects
  for key, value in list(result.items()):
    if isinstance(value, (dict, list)):
      result[key] = _add_empty_schema_placeholder(value)

  return result


# ============================================================================
# MAIN ORCHESTRATOR
# ============================================================================


def clean_json_schema(schema: dict) -> dict:
  """Cleans a JSON schema for Antigravity API compatibility.
  Transforms unsupported features into description hints while preserving
  semantic information.

  Ported from TypeScript cleanJSONSchemaForAntigravity().
  """
  if not isinstance(schema, dict):
    return schema

  result = schema

  # Phase 1: Convert and add hints
  result = _convert_refs_to_hints(result)
  result = _convert_const_to_enum(result)
  result = _add_enum_hints(result)
  result = _add_additional_properties_hints(result)
  result = _move_constraints_to_description(result)

  # Phase 2: Flatten complex structures
  result = _merge_all_of(result)
  result = _flatten_any_of_one_of(result)
  result = _flatten_type_arrays(result)

  # Phase 3: Cleanup
  result = _remove_unsupported_keywords(result)
  result = _cleanup_required_fields(result)

  # Phase 4: Add placeholder for empty object schemas
  result = _add_empty_schema_placeholder(result)

  return result


# ============================================================================
# GEMINI SCHEMA TRANSFORMER
# Ported from TypeScript src/plugin/transform/gemini.ts
# ============================================================================

UNSUPPORTED_GEMINI_FIELDS = {
  "additionalProperties", "$schema", "$id", "$comment", "$ref", "$defs",
  "definitions", "const", "contentMediaType", "contentEncoding", "if",
  "then", "else", "not", "patternProperties", "unevaluatedProperties",
  "unevaluatedItems", "dependentRequired", "dependentSchemas",
  "propertyNames", "minContains", "maxContains",
}


def to_gemini_schema(schema) -> dict:
  """Transform a JSON Schema to Gemini-compatible format.

  Key transformations:
  - Converts type values to uppercase (object -> OBJECT)
  - Removes unsupported fields like additionalProperties, $schema
  - Recursively processes nested schemas (properties, items, anyOf, etc.)
  - Filters required to only include properties that exist
  - Ensures array schemas have an 'items' field

  Ported from TypeScript toGeminiSchema().
  """
  if not isinstance(schema, dict):
    return schema

  input_schema = schema

  # Collect property names for required validation
  property_names: set[str] = set()
  if isinstance(input_schema.get("properties"), dict):
    property_names.update(input_schema["properties"].keys())

  result = {}
  for key, value in input_schema.items():
    # Skip unsupported fields that Gemini API rejects
    if key in UNSUPPORTED_GEMINI_FIELDS:
      continue

    if key == "type" and isinstance(value, str):
      # Convert type to uppercase for Gemini API
      result[key] = value.upper()
    elif key == "properties" and isinstance(value, dict):
      # Recursively transform nested property schemas
      result[key] = {k: to_gemini_schema(v) for k, v in value.items()}
    elif key == "items" and isinstance(value, dict):
      result[key] = to_gemini_schema(value)
    elif key in ("anyOf", "oneOf", "allOf") and isinstance(value, list):
      result[key] = [to_gemini_schema(item) for item in value]
    elif key == "enum" and isinstance(value, list):
      result[key] = value
    elif key in ("default", "examples"):
      result[key] = value
    elif key == "required" and isinstance(value, list):
      # Filter required to only include properties that exist
      if property_names:
        valid_required = [r for r in value if isinstance(r, str) and r in property_names]
        if valid_required:
          result[key] = valid_required
      else:
        result[key] = value
    else:
      result[key] = value

  # Ensure array schemas have an 'items' field
  if result.get("type") == "ARRAY" and "items" not in result:
    result["items"] = {"type": "STRING"}

  return result
