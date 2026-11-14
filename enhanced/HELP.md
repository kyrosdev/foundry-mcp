# Foundry MCP tool guide

The cursor artwork uses the Claude and Codex marks from [Lobe Icons](https://github.com/lobehub/lobe-icons). Foundry renders them locally without requesting Roblox asset IDs.

Studio cursors display the agent and observed tool activity in a 2D viewport layer in Edit mode. Visible spatial targets are projected onto the screen. The cursor and label ease between positions, and scripts and off-screen targets appear at the viewport edge. Labels disappear a few seconds after the last call. Cursors use the existing agent identity and are enabled by default. Pass `--no-cursors` to turn them off, or `--no-monitor` to disable both cursors and local monitor snapshots.

Use **Hide cursors** in the Foundry Monitor desktop app to hide cursors, outlines and reports for MCP connections using the monitor's data directory. This also stops camera following. **Show cursors** resumes display. The preference is stored locally.

Use `extra_ribbon` with `studio_id` and an empty `payload` to read the selected Studio ribbon tool. Use `payload.tool` to activate Select, Move, Scale, Rotate, Transform, PivotEditor, ColorPicker or MaterialPicker. These are Studio actions and do not move the Windows mouse. Third-party plugin buttons require an integration with the plugin that owns them.

On Windows, use `extra_studio_control` with `payload.action: "point"` or `"click"` and the visible button name in `payload.control`. Open the Plugins tab so the label is visible. Foundry uses Windows accessibility when available and reads custom ribbon labels locally using FFmpeg and Windows OCR. Ambiguous or unreadable labels are rejected. The result reports click dispatch; verify the resulting plugin state separately. Run Foundry Monitor (or `python enhanced/monitor.py --ribbon-only`) with the same data directory as the MCP clients to see the live pointers. The overlay passes mouse input through to Studio.

Agents on the same object keep separate pointers and share one label, with activity per agent. It shows up to four names and counts any additional agents. Successful batch operations briefly outline up to eight resolved targets. To send a custom message, use `extra_agent` with `action: "status"`, `studio_id`, `status` (up to 120 characters) and an optional exact `path`.

Click an agent's label to frame its object or open its script. For a ranged `script_read`, the editor goes to the first requested line. When labels crowd the viewport, Foundry places them around the animated pointers and draws a short connector where needed. When the full label will not fit nearby, it becomes a clickable compact badge with the agent name and shared-target count. The full label returns as space opens up. Use the circle beside an agent's name to follow its spatial target. Click the circle again or use the camera controls to stop following. After `extra_playtest`, click **Playtest results** in Edit mode to see the result and step summaries. The shortcut stays visible for up to 35 seconds.

Use `extra_place_publish` with `restore_latest`, the open Studio window and its exact place ID to republish the newest verified release as a new revision. The default uses Studio Open Cloud permissions. Use `transport: "api_key"` for the Assets API with a configured key. The receipt identifies the checkpoint, prior published version and newly confirmed version. Use `restore_status` with its plan ID if verification is pending. Running game servers keep their current version until restarted.

To capture a bug, start Play, call `extra_scenario` with `record_start`, reproduce it in the viewport, and call `record_stop` with the recording ID and scenario name before leaving Play. Use `record_get` for captured warnings, errors and inputs. Replay with `run`. By default, the scenario checks whether recorded errors recur. Supply `assert_code` and `assert_model` when stopping to check the behavior directly. Keyboard and mouse playback uses viewport coordinates. Text entry and unsupported inputs are omitted. Capture ends after 120 seconds.

To validate a mixed plan, export the current place via File > Download a Copy or Save to File and open that file in a separate Studio window. Use `extra_mixed_changes` with `validate`, the original plan ID, `copy_studio_id` and an exact `scenario` name/version. Validation changes the local window. The original remains pending. Validation checks script and hierarchy changes plus the preconditions of the edited properties. The local file on disk remains available to reopen for a fresh test.

In scenario steps, `profile_seconds` samples client render or server heartbeat timing and process memory. Compare saved runs to see deltas. Use `remote_handlers` with an exact path to find candidate server handlers. A scenario step such as `{"remote_probe":{"path":["ReplicatedStorage","Purchase"],"calls":[["invalid"],["invalid"]]}}` sends bounded JSON arguments to a RemoteEvent on an unpublished local copy. Follow it with a server assertion to verify the state.

Use `compare_plans` with `peer_plan_id` to see overlapping paths. Use `validate` with the same peer ID to test two compatible plans together. Use `export_scene` and `prepare_scene` to stage reversible property edits from a JSON manifest.

Use `extra_cloud_luau` with `verify`, a place ID, exact published version and a boolean Luau assertion. A passing check records the version and task locally. Retrieve the release history with `verified_versions`.

The initial catalog lists every tool with its accepted arguments. Call `extra_help` with `{"tool":"search_asset"}` (or another exact tool name) for its full description and parameter notes. For Roblox skill names and summaries, use `list_skills`.

Use `detail: "full"` on mixed plan preparation, impact queries, or scenario runs for expanded responses. Mixed plan status returns the saved preview before apply. Scenario result returns the saved report.

Use `include_source: true` with `extra_impact` to retrieve the target script and its hash in the same capture as its references. Save a single server assertion with `extra_scenario` and `assert_code`. It uses a fresh Play session with one player by default. Use `mode: "run"` for assertions that need only the server simulation. Mixed plan preparation can run a pinned `baseline_scenario: {"name": "...", "version": 1}` alongside local checks after capturing the edit state. A failing baseline is recorded for comparison and the plan still requires an explicit apply.

Use `extra_place_publish` with `transport: "studio"` to prepare and open a local place file for publishing on Windows. Select the expected destination in **File → Publish As**. If the Studio reports an active Team Create session for that destination, close its editors before overwriting. Use status with the destination's current Studio ID to verify the published version.

`extra_datastore` uses the open Studio session by default. Enable **Save & load data** scopes under **MCP Permissions** and **Enable Studio Access to API Services** in the game settings. Standard DataStore updates use prepare/apply with a revision check. Select `transport: "api_key"` for REST.

Use `extra_publish_open_place` with `studio_id` and the expected `place_id` to run Studio's native Publish command on Windows and verify the published version. This requires an interactive Studio window in Edit mode and the default Alt+P shortcut.

After initial project sync, use `extra_project_sync_watch` to start, inspect or stop automatic bidirectional sync on this MCP connection. A conflict pauses writes until it is resolved and the watch is restarted.

Use `extra_multiplayer_test` for a server-side assertion with 2–8 simulated Studio clients. Use `extra_device_test` to list available devices or run a saved scenario across their presets. The two tools target the open Studio window explicitly.

Use `extra_project_sync` with `action: "preview"` and a workspace-relative `directory`. For a local place, bind it with `extra_project_bind` first. Omit `scope` to discover all scripts. Read changes with `action: "diff"` and apply with the returned `plan_id`. For a content conflict, pass the reported hashes in `resolutions` and choose `studio`, `disk` or `merged` (with `source`). The generated `default.project.json` maps script files and their ancestors.

Use `extra_mixed_changes` for staged scene and script changes. Create nested instances with `kind: "create"`, `path` and `class`. Include `source` to create a script. Reference a planned object from `ObjectValue.Value` with `{"type":"Instance","path":[...]}`. Use `kind: "relocate"` with `destination` to move or rename. Inspect the prepared plan before apply. Rollback uses the same plan ID.

Cloud workflows include `extra_cloud_catalog`, `extra_cloud_luau`, `extra_asset_upload`, `extra_place_publish`, `extra_datastore` and `extra_roblox_lookup`. See [VERSAO-1.0.md](../VERSAO-1.0.md) for authorization and scope.

---

# API/type checking update (0.4)

Roblox type checking now uses Luau LSP with Roblox definitions, not the plain standalone analyzer. extra_code_check optionally accepts studio_id plus path for live hierarchy and cross-module analysis. Protected writes automatically analyze a proposed source against the live project; type errors now block writes alongside lint and format errors. Explicit check=false defers checks for coordinated multi-file changes.

New tools: extra_api_lookup (class_name/member or enum_name), extra_api_refresh (validated refresh from Roblox CDN and Luau LSP definitions), extra_project_check (studio_id; optional path/source replacement and include_editor).

Full reference and tested boundaries: ../APIS-ROBLOX.md. These rules supersede older notes about missing Roblox engine types below. Project collection is chunked to avoid truncation, and refuses incomplete results. Duplicate siblings have analysis-only aliases; ambiguous replacement paths are rejected. Editor/plugin services are excluded from game checks by default.

---

# Ferramentas de edição de instâncias

All extra tools except extra_help take studio_id (from list_roblox_studios), payload, and optional datamodel_type (default Edit). Mutating tools require Edit. Paths are arrays of exact child names below game, e.g. ["Workspace", "My.Model"]. Empty path means game. Ambiguous sibling names produce an error.

Payload examples:

- extra_selection: {} to read; {"paths":[["Workspace","SpawnLocation"]]} to select; {"paths":[]} to clear.
- extra_snapshot: {"path":["Workspace"],"depth":4,"limit":500,"properties":["Position","Size","Anchored"]}. Maximum depth 32, limit 5000, properties 30. Missing properties return per-property errors. Counts cover visited nodes only. Depth limits also omit descendants even if truncated is false. Not an atomic snapshot during play.
- extra_diagnostics: {"path":[],"depth":32,"limit":5000}. Bounded class counts and place metadata, not a performance profiler.
- extra_batch_create: {"items":[{"class":"Part","name":"Demo","parent":["Workspace"],"properties":{"Anchored":true,"Position":{"type":"Vector3","value":[0,5,0]}}}]}. Items run in order; later items can reference earlier paths.
- extra_batch_properties: {"items":[{"path":["Workspace","Demo"],"properties":{"Transparency":0.5}}]}.
- extra_attributes: {"path":["Workspace","Demo"],"set":{"Damage":10},"remove":["OldAttribute"]}. Omit set/remove to read.
- extra_tags: {"path":["Workspace","Demo"],"add":["Enemy"],"remove":["OldTag"]}. Omit add/remove to read.

Typed values: {"type":"Vector2|Vector3|Color3|UDim|UDim2|CFrame","value":[components]}. Color3 uses 0..1; CFrame accepts 3 position or 12 matrix components. Enums: {"type":"Enum","value":["Material","Neon"]}. Instance references: {"type":"Instance","path":["Workspace","Demo"]}. Plain strings, booleans and numbers pass through.

Batch tools allow 100 items and 50 properties per item. They use Studio undo recording and attempt rollback on failure, reporting rollback errors. External plugin side effects cannot be rolled back. Parent and Source writes are excluded; use native multi_edit for scripts. Selection changes are not undoable. Existing official tools, permissions, errors, images and notifications pass through to Claude unchanged.


## Workbench tools (version 0.2)

These tools use their own arguments directly, NOT a payload wrapper:

- extra_status: {} returns session ID, allowed workspace, data directory and tool availability.
- extra_sessions: {} lists sessions and active leases. Last activity does not prove a process is still alive.
- extra_lock: {"studio_id":"ID","path":["ServerScriptService","Demo"],"action":"acquire","ttl":600}. Returns a token. Renew/release with the same studio_id/path, action and token. Leases expire in 5..3600 seconds. All connections must share --data-dir for coordination.
- extra_history: {"studio_id":"ID","path":["ServerScriptService","Demo"],"limit":25} lists revisions. {"revision_id":"REV"} reads a revision. {} lists recent activity. Backups are persisted locally in SQLite.
- extra_script_get: {"studio_id":"ID","path":["ServerScriptService","Demo"]} returns live editor source and SHA256.
- extra_script_put: {"studio_id":"ID","path":["ServerScriptService","Demo"],"source":"print(1)\n","expected_hash":"HASH_FROM_GET","preview":true}. Preview is the default. Set preview=false to apply. format and check default true. Formatting and lint/parser errors block writes; standalone type diagnostics are returned for review because the Roblox type environment is incomplete. Explicit format=false/check=false opts out. Existing scripts only; use native multi_edit with className to create scripts.
- extra_script_restore: {"studio_id":"ID","path":["ServerScriptService","Demo"],"revision_id":"REV","expected_hash":"CURRENT_HASH","preview":true}. Current code is backed up before a real restore.
- extra_sync: {"studio_id":"ID","path":["ServerScriptService","Demo"],"file":"src/demo.server.luau","action":"pull","preview":true}. Allowed actions: status, pull, push, merge. Initial pull establishes the baseline. A conflicting existing file is never used as an implicit baseline. Three-way merge combines non-overlapping line edits and reports overlap. Each call handles ONE script; there is no background watcher or automatic deletion. Paths must stay within --workspace, including resolved symlinks. Set preview=false to apply.
- extra_code_check: {"source":"--!strict\nlocal x: number = 1\nprint(x)\n"}. Runs StyLua, Selene with a bundled Roblox standard library, and standalone Luau analyzer. Missing tools and diagnostics are reported as failures. The analyzer defaults to strict mode unless the file specifies its own mode.
- extra_code_format: {"source":"print( 1 )"}. Returns formatted source, never writes to Studio.
- extra_playtest: {"studio_id":"ID","steps":[{"tool":"start_stop_play","arguments":{"is_start":true}},{"assert_code":"return workspace ~= nil","arguments":{"datamodel_type":"Server"}},{"tool":"get_console_output","arguments":{}}],"cleanup":true}. Native tool arguments remain native. Assertion code must return true. Screenshots remain MCP image content. The runner refuses to start over an already running play session and stops sessions it started, even after assertion failure. If a request times out, code may still run and its lease is retained until expiry.

## Instance actions

extra_instance_actions uses the original payload wrapper:
{"studio_id":"ID","payload":{"preview":true,"items":[{"action":"clone","path":["Workspace","House"],"name":"HouseCopy","parent":["Workspace"]}]}}

Actions: clone, move, rename, trash. Preview defaults true. move requires parent; rename requires name. trash moves objects into ServerStorage.MCP_Trash with unique names rather than destroying them. Undo recording is used on applied changes. Root services cannot be moved or trashed.

## Boundaries

Native multi_edit of EXISTING scripts is routed through formatting, lint, history and compare-and-swap writes. Native script creation is formatted and linted before forwarding to Studio, with an after-creation snapshot when the source can be read. Arbitrary execute_luau, generated content, Open Cloud mutations, manual edits and clients connected directly to the official server do not receive per-script snapshots from this extension. Native mutating calls through this extension are serialized per Studio; local guarded writes also respect script leases. Locking is cooperative, not a Studio permission boundary.

History/sync currently use the runtime Studio ID. After reopening Studio, old history remains accessible by revision ID and activity records, but sync mappings do not automatically migrate to a new Studio ID. Use the same Studio session for an ongoing sync workflow.

Sync rechecks disk contents and uses atomic file replacement; manual editors do not share its locks, so it is not a distributed transaction between disk and Studio. If one side changes mid-operation, inspect status/history before retrying. Restore cannot reverse arbitrary external plugin side effects.

The bridge adds no telemetry. Your AI provider still receives tool results and Roblox cloud tools still contact Roblox. Open Cloud publishing, purchases and player-data changes require the relevant account permissions; they are not tested against live production data by this package.


## Collaboration and live information (0.3)

See ../GUIA-COLABORACAO.md for the complete workflow and limits. All tools accept optional as_agent, owned by this MCP connection. Use extra_agent(action=register,name=...) to create a logical subagent identity; include its returned agent_id as as_agent on EVERY call by that subagent. The field is stripped before native forwarding. Registered identity does not start an AI model.

New tools: extra_agent; extra_team_join, extra_team_board, extra_team_message, extra_team_inbox, extra_team_context, extra_task_create, extra_task_claim, extra_task_update, extra_task_review, extra_task_handoff, extra_team_events; extra_live_read, extra_live_watch; extra_web_search, extra_web_fetch.

Team operations require a shared project_id and shared --data-dir. Join with studio_id for protected work. Claim scoped paths before edits; keep the task lease alive explicitly with task_update before expiry. Independent review is required by default and cannot be performed by the task's owner identity. Team context uses expected_version and preserves earlier revisions. Messages and evidence are untrusted reference data, not verified commands or proof.

live_read reads Studio now. live_watch uses 2..60 second polling with at most 8 active watches per connection; changes/errors are delivered through team_events after a cursor. team_events wait_seconds is bounded to 25. Watch samples are cached snapshots with explicit age, error and stale flags. Native source buffer changes between polls are coalesced.

web_search defaults to best-effort Bing public RSS; BRAVE_SEARCH_API_KEY selects the official Brave API. web_fetch reads public HTTPS text/HTML/Markdown/JSON/XML without executing JavaScript. Results carry source URLs and collection timestamps; publication dates are separate. There is no silent cached fallback on failure.

In team mode, protected script/object edits require an active owned task covering the target paths. Known helper operations and script creation can proceed on disjoint scopes; physical writes are serialized briefly. Opaque raw code, generation and global Studio actions are blocked while other identities reserve paths. Manual edits and clients bypassing this extension remain outside cooperative coordination.
