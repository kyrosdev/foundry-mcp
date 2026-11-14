# Foundry MCP

Foundry is a local MCP server for working on Roblox projects in Roblox Studio with Codex, Claude, and other compatible MCP clients. It combines the server built into Roblox Studio with tools for inspecting, editing, testing, and coordinating changes in the open place.

On Windows, the Rust launcher discovers tools while the Python server starts in parallel. Native tool availability follows your installed Studio version. Work directly when solo. The Foundry coordination mode switches automatically when registered teammates share the same project and Studio window.

## Bring your own agent

Use Foundry from Codex, Claude Code, Claude Desktop, Cursor, or another MCP client that can launch a local server over standard input and output. Each client receives the same Foundry tool definitions for the same installation and Roblox Studio version. Connect to the same open Roblox or Blender session when switching clients. Use the same workspace and Foundry data directory to share saved plans, history, and team reservations. Each connected agent retains its own identity and its client's tool approval settings.

## Features

| Area | Capabilities |
| --- | --- |
| Scene | Inspect the hierarchy, selection, typed properties, tags, and attributes. Edit instances in batches and export models. |
| Luau | Read editor source, find references, map the impact of changes, and review diffs. Format with StyLua, lint with Selene, and check Roblox types with Luau LSP. |
| Change plans | Prepare, review, apply, and revert changes that combine scripts and instances. Check for conflicts and validate plans in a local copy. |
| Sync | Export scripts and a Rojo sourcemap, prepare two-way synchronization, and watch for changes. Sync pauses when a conflict occurs. |
| Playtests | Save versioned scenarios with assertions, logs, and screenshots. Simulate players and devices, and record and replay bugs. |
| Collaboration | Register agents, assign tasks, lock paths, exchange messages, share context, and attach evidence for review. |
| Monitor | Follow tool activity in a Qt desktop app. See each agent's cursor in Studio and open targets or playtest results. |
| Open Cloud | Work with analytics, published versions, assets, passes, products, and DataStores, subject to the permissions and scopes available. |
| Live information | Watch Studio state and signals and consult public web sources. |

Clients discover available tools through `tools/list`. Call `extra_help` with a tool's exact name to see its description and arguments. The [tool guide](enhanced/HELP.md) includes workflow examples.

## Installation on Windows x64

1. Install the latest Roblox Studio and Python 3.10 or later. Run `python --version` in PowerShell to check.
2. Download **Foundry-MCP-1.1.0-windows-x64.zip** from the [v1.1.0 release](../../releases/tag/v1.1.0). Extract the entire folder and keep the executable with the other files in `enhanced`.
3. Open a place in Roblox Studio. In **Assistant → … → Manage MCP Servers**, turn on **Enable Studio as MCP server**. [Roblox setup guide](https://create.roblox.com/docs/studio/mcp)
4. Choose an existing local folder for your project. Substitute your own paths for `C:\Tools\Foundry-MCP` and `C:\Projects\MyGame` in the examples below.
5. Configure your client using one of the methods below and restart it.
6. Ask the assistant to call `list_roblox_studios`, then call `extra_help` with `{"tool":"extra_impact"}`. If several Studio windows are open, specify the place you want to work on.

### Codex

~~~powershell
codex mcp add Foundry_MCP -- "C:\Tools\Foundry-MCP\enhanced\foundry-bootstrap.exe" --workspace "C:\Projects\MyGame" --session-label Codex
codex mcp list
~~~

To configure Codex directly, add this section to its `config.toml`:

~~~toml
[mcp_servers.Foundry_MCP]
command = 'C:\Tools\Foundry-MCP\enhanced\foundry-bootstrap.exe'
args = ['--workspace', 'C:\Projects\MyGame', '--session-label', 'Codex']
~~~

### Claude Code

~~~powershell
claude mcp add --transport stdio --scope user Foundry_MCP -- "C:\Tools\Foundry-MCP\enhanced\foundry-bootstrap.exe" --workspace "C:\Projects\MyGame" --session-label "Claude Code"
claude mcp list
~~~

### Claude Desktop

Under **Settings → Developer → Edit Config**, add the `Foundry_MCP` entry to the existing `mcpServers` object. Preserve any other entries.

~~~json
{
  "mcpServers": {
    "Foundry_MCP": {
      "command": "C:\\Tools\\Foundry-MCP\\enhanced\\foundry-bootstrap.exe",
      "args": ["--workspace", "C:\\Projects\\MyGame", "--session-label", "Claude Desktop"]
    }
  }
}
~~~

### Cursor and other local MCP clients

Run this command from the extracted Foundry folder:

~~~powershell
python enhanced/configure.py --client cursor --workspace "C:\Projects\MyGame"
~~~

Copy the generated `Foundry_MCP` entry into the `mcpServers` object in `~/.cursor/mcp.json` for all projects or `.cursor/mcp.json` for the current project. Preserve existing entries. Restart Cursor and check for Foundry under **Tools & MCP**. [Cursor MCP documentation](https://cursor.com/docs/mcp)

For other local clients, use `--client generic` to get the command and arguments in JSON. Use `--client codex` to generate TOML. Use `--data-dir` if the clients share a custom Foundry data directory. Keep generated configuration with machine-specific paths in your local client settings.

### Run through Python directly

If your MCP client launches local servers, you can set the command to `python.exe`. Pass the absolute path to `enhanced/server.py` as the first argument, followed by `--workspace` and `--session-label`. If the Rust launcher needs a Python installation outside your PATH, set `FOUNDRY_PYTHON` to the absolute path of that interpreter.

### Open the monitor

Double-click **Open Monitor.cmd**. The shortcut installs Qt through pip if needed. To start it from a terminal in the extracted folder:

~~~powershell
python -m pip install -r enhanced/monitor-requirements.txt
python enhanced/monitor.py
~~~

The monitor and clients share a data directory. By default, this is `%LOCALAPPDATA%\StudioMCPExtra`. If you set `--data-dir` for the server, pass the same path to the monitor. Use **Hide cursors** to hide agent cursors in Studio. The server also accepts `--no-cursors` and `--no-monitor`.

## Connect Blender

1. Install the **Foundry-Blender-Bridge-1.1.0.zip** add-on from the same release. In Blender, open **Edit → Preferences → Add-ons**, choose **Install from Disk**, and enable **Foundry Blender Bridge**.
2. Keep the same Foundry MCP connection in your client. Call `extra_blender_sessions` to discover the open Blender session, then use its `session_id` with `extra_blender_scene` or `extra_blender_object`.
3. Use `extra_blender_create` to create a mesh primitive and `extra_blender_edit` to change its transform or material. Save the Blender project through Blender when ready.

For shared work, join using the `studio_id` returned by `extra_blender_sessions`, and reserve paths such as `["Objects", "Crate"]`. Auto mode uses the same rules in both apps. If you configure a custom Foundry data directory, start Blender with `FOUNDRY_DATA_DIR` set to that directory.

## Stable updates

The Windows launcher checks the official GitHub stable release after catalog discovery, at most once per day. It verifies the package size and GitHub SHA-256, stages the update in the installation directory, and activates it when the MCP client reconnects. Keep the installation directory writable and configure your client from the original extracted folder. Use `--no-updates` to pin that installation, or run `python enhanced/stable_update.py --check-now` to check immediately.

The Blender add-on is versioned separately and can be updated through Blender's Add-ons panel.

## First workflow

1. Select the Studio window with `list_roblox_studios`.
2. Run `extra_impact` with a path such as `["ServerScriptService","Config"]`. Set `include_source: true` to get the source and its hash from the same capture.
3. Use `extra_mixed_changes` to prepare a plan and review the diff.
4. Apply the plan and run a scenario with `extra_scenario`. Use the plan's rollback action if you need to restore the previous state.
5. When delegating work, have each participant call `extra_team_join` with the same `project_id` and `studio_id` and use the same Foundry data directory.

### Automatic coordination

New project registrations use `mode: "auto"`. A solo agent can edit directly. Once another registered agent is online in the same project and Studio window, writes require an active task with the appropriate reserved paths. Live tasks and pending reviews keep their reservations even when an agent disconnects. Once those reservations are released and only one agent remains, auto mode returns to individual editing.

Set `mode: "team"` in `extra_team_join` to require reservations even when you are the only connected agent. `extra_team_board` reports the effective mode. Existing team registrations retain their previous behavior until switched to auto. Foundry recognizes collaboration through registered participants sharing its local data directory.

### Verify a change in Studio

Use `extra_playtest` to start Play, move the simulated player with `character_navigation`, assert the expected server or client state, and capture the viewport with `screen_capture` where supported by the current Studio mode. Save the steps with `extra_scenario` to repeat the check. Team tasks can require a verification receipt from `extra_evidence` before review.

Use `extra_script_put` or an edit plan to apply the bundled Luau checks. Raw Luau execution remains available and can bypass those checks. Two-way project sync uses a three-way comparison and pauses on conflicts requiring a decision.

## Permissions and data

- Foundry stores history, plans, locks, and monitor activity locally. Information received by your MCP client is subject to your AI provider's data policies.
- Most editor operations use the Studio session. Some Open Cloud operations use permissions granted in Studio. Certain REST workflows accept the `STUDIO_MCP_EXTRA_ROBLOX_API_KEY` environment variable. [Roblox Studio MCP permissions](https://create.roblox.com/docs/studio/mcp)
- The monitor displays observable tool calls and registered agents. Some ribbon controls and publishing workflows require an interactive Studio window. Ribbon label recognition can use Windows OCR and FFmpeg.
- Impact analysis is static. Dynamic references and runtime behavior require inspection or playtests.

## Development

The release includes Windows binaries. To rebuild the launcher with Rust installed, run these commands from the repository root:

~~~powershell
python enhanced/generate_workbench_catalog.py
cargo build --locked --release --manifest-path enhanced/bootstrap/Cargo.toml
Copy-Item enhanced/bootstrap/target/release/foundry-bootstrap.exe enhanced/foundry-bootstrap.exe
python -m unittest discover -s tests
~~~

Foundry is an independent project based on the [open-source Roblox Studio MCP server](https://github.com/Roblox/studio-rust-mcp-server). See the [MIT license](LICENSE) and [third-party notices](enhanced/THIRD-PARTY.md).
