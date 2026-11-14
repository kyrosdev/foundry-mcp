"""Regenerate the bundled MCP catalog from the tool definitions."""
import json
from pathlib import Path

from workbench import definitions
from server import INSTRUCTIONS, definitions as server_definitions
from catalog import NATIVE_DESCRIPTIONS, SHARED_NATIVE_FIELDS, SHORT_NATIVE_FIELDS, advertise


if __name__ == "__main__":
    path = Path(__file__).with_name("workbench_catalog.json")
    catalog = json.dumps(definitions(), ensure_ascii=True, separators=(",", ":"))
    path.write_text(catalog + "\n", encoding="utf-8")
    bootstrap = {
        "instructions": INSTRUCTIONS,
        "extensions": [advertise(tool) for tool in server_definitions(definitions())],
        "native_descriptions": NATIVE_DESCRIPTIONS,
        "shared_fields": SHARED_NATIVE_FIELDS,
        "native_fields": [[tool, key, value] for (tool, key), value in SHORT_NATIVE_FIELDS.items()],
    }
    path.with_name("bootstrap_catalog.json").write_text(
        json.dumps(bootstrap, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
