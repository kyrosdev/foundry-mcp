"""Generate local Foundry configuration for an MCP client."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
LABELS = {
    "claude-desktop": "Claude Desktop",
    "claude-code": "Claude Code",
    "codex": "Codex",
    "cursor": "Cursor",
    "generic": None,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=ROOT.parent)
    parser.add_argument("--client", choices=LABELS, default="claude-desktop")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--label")
    parser.add_argument("--python", action="store_true", help="Launch the Python server directly")
    parser.add_argument("--stdout", action="store_true", help="Print configuration instead of writing a file")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        parser.error("Workspace folder does not exist")
    executable = ROOT / "foundry-bootstrap.exe"
    if sys.platform == "win32" and executable.is_file() and not args.python:
        command = str(executable)
        argv = []
    else:
        command = sys.executable
        argv = [str(ROOT / "server.py")]
    argv += ["--workspace", str(workspace)]
    if args.data_dir:
        argv += ["--data-dir", str(args.data_dir.resolve())]
    label = args.label or LABELS[args.client]
    if label:
        argv += ["--session-label", label]

    entry = {"command": command, "args": argv}
    if args.client == "codex":
        # JSON strings and arrays of strings are also valid TOML here.
        rendered = ("[mcp_servers.Foundry_MCP]\n"
                    + "\n".join(f"{key} = {json.dumps(value, ensure_ascii=False)}"
                                for key, value in entry.items()))
    else:
        if args.client == "cursor":
            entry["type"] = "stdio"
        rendered = json.dumps({"mcpServers": {"Foundry_MCP": entry}}, indent=2)
    if args.stdout or args.client != "claude-desktop":
        print(rendered)
    else:
        output = ROOT / "claude-config.json"
        output.write_text(rendered + "\n", encoding="utf-8")
        print(output)


if __name__ == "__main__":
    main()
