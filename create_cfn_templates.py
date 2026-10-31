import json
from typing import Any
from pathlib import Path

TYPE_DEFAULTS = {
    "string": "\"\"",
    "integer": "0",
    "number": "0",
    "boolean": "false",
}
SCHEMA_DEFAULT_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
}
SCHEMA_DIR = Path("./schema")
OUTPUT_DIR = Path("./output")

INDENT_LEVEL = 2
TEMPLATE_FORMAT_VERSION = "2010-09-09"
LOGICAL_ID_PREFIX = "CfnSample"
ALTERNATIVE_KEYS = ["oneOf", "anyOf", "allOf"]

def node_type(node: dict) -> str | None:
    base_type = node.get("type")
    if isinstance(base_type, list):
        base_type = base_type[0]
    if base_type is None:
        if "properties" in node or "patternProperties" in node:
            return "object"
        if "items" in node:
            return "array"
    return base_type

def default_value(base_type: str | None) -> str:
    if base_type not in TYPE_DEFAULTS:
        raise ValueError(f"unsupported property type: {base_type}")
    return TYPE_DEFAULTS[base_type]

def schema_default(node: dict, base_type: str | None, value: str) -> str:
    # スキーマの default は null や型の食い違いがあるので、型が合うものだけ使う
    default = node.get("default")
    if base_type not in SCHEMA_DEFAULT_TYPES or not isinstance(default, SCHEMA_DEFAULT_TYPES[base_type]):
        return value
    if isinstance(default, bool) and base_type != "boolean":
        return value
    return json.dumps(default, ensure_ascii=False)

def merge_alternatives(node: dict) -> dict:
    # oneOf/anyOf/allOf は選択肢の properties をすべてまとめて出力する
    alternatives = [alternative for key in ALTERNATIVE_KEYS for alternative in node.get(key, [])]
    properties = dict(node.get("properties", {}))
    for alternative in alternatives:
        properties.update(alternative.get("properties", {}))
    merged = {key: value for key, value in node.items() if key not in ALTERNATIVE_KEYS}
    if properties:
        merged["properties"] = properties
    if node_type(merged) is None:
        typed = [alternative for alternative in alternatives if "$ref" in alternative or node_type(alternative)]
        if typed:
            return typed[0]
    return merged

def lookup(schema: dict, ref: str) -> dict:
    node = schema
    for key in ref.removeprefix("#/").split("/"):
        node = node[key]
    return node

def resolve(node: dict, path: str, schema: dict, excluded: set[str], refs: tuple[str, ...]) -> tuple[str, Any]:
    if "$ref" in node:
        ref = node["$ref"]
        if ref in refs:
            # 循環参照はそこで展開を止める
            base_type = node_type(merge_alternatives(lookup(schema, ref)))
            if base_type == "object":
                return ("map", [])
            if base_type == "array":
                return ("scalar", "[]")
            return ("scalar", default_value(base_type))
        kind, content = resolve(lookup(schema, ref), path, schema, excluded, refs + (ref,))
        if kind == "scalar":
            content = schema_default(node, node_type(merge_alternatives(lookup(schema, ref))), content)
        return (kind, content)

    node = merge_alternatives(node)
    if "$ref" in node:
        return resolve(node, path, schema, excluded, refs)

    base_type = node_type(node)
    if base_type == "object":
        return ("map", [
            (name, resolve(child, f"{path}/{name}", schema, excluded, refs))
            for name, child in node.get("properties", {}).items()
            if f"{path}/{name}" not in excluded])
    if base_type == "array":
        kind, content = resolve(node.get("items", {}), f"{path}/*", schema, excluded, refs)
        return ("list", content) if kind == "map" and content else ("scalar", schema_default(node, base_type, "[]"))
    if base_type is None and not node.get("enum"):
        return ("map", [])
    return ("scalar", schema_default(node, base_type or "string", default_value(base_type or "string")))

def make_mapping(children: list, indent_level: int) -> list[str]:
    ordered = sorted(children, key=lambda child: (child[1][0] != "scalar", child[0]))
    return [line for name, value in ordered for line in make_lines(name, value, indent_level)]

def make_lines(name: str, value: tuple[str, Any], indent_level: int) -> list[str]:
    kind, content = value
    if kind == "scalar":
        return [f"{' '*indent_level}{name}: {content}"]
    if not content:
        return [f"{' '*indent_level}{name}: {{}}"]
    if kind == "map":
        return [f"{' '*indent_level}{name}:"] + make_mapping(content, indent_level + INDENT_LEVEL)
    items = make_mapping(content, indent_level + INDENT_LEVEL * 2)
    items[0] = f"{' '*(indent_level+INDENT_LEVEL)}- {items[0].lstrip()}"
    return [f"{' '*indent_level}{name}:"] + items

def main() -> None:
    for schema_path in sorted(SCHEMA_DIR.glob("*.json")):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        type_name = schema["typeName"]
        names = type_name.split("::")[1:]
        output_path = OUTPUT_DIR / names[0]
        output_path.mkdir(parents=True, exist_ok=True)

        excluded = set(schema.get("readOnlyProperties", []) + schema.get("deprecatedProperties", []))
        properties = resolve(schema, "/properties", schema, excluded, ())
        logical_id = LOGICAL_ID_PREFIX + "".join(name.capitalize() for name in names)
        lines = [
            f"AWSTemplateFormatVersion: \"{TEMPLATE_FORMAT_VERSION}\"",
            "",
            "Resources:",
            f"{' '*INDENT_LEVEL}{logical_id}:",
            f"{' '*INDENT_LEVEL*2}Type: {type_name}",
        ] + make_lines("Properties", properties, INDENT_LEVEL * 2)
        (output_path / f"{'_'.join(names).lower()}.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
