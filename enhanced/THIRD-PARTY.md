# Portable Windows toolchain

Unmodified executables are distributed for the user's Windows environment. The extension itself uses Python's standard library. On other platforms install the equivalent tools on PATH; the bundled Windows executables do not run on macOS/Linux.

| Component | Version | License | Corresponding source |
|---|---|---|---|
| StyLua | 2.5.2 | MPL-2.0 | https://github.com/JohnnyMorganz/StyLua/tree/v2.5.2 |
| Selene | 0.31.0 | MPL-2.0 | https://github.com/Kampfkarren/selene/tree/0.31.0 |
| Luau | 0.745 | MIT | https://github.com/luau-lang/luau/tree/0.745 |

License texts are in `licenses`. SHA256 values are in `toolchain-manifest.json`. Luau was downloaded from its official GitHub release, with the release asset digest checked before extraction. StyLua and Selene were copied from the existing local Rokit installation. Their hashes record the distributed files; this manifest is not a digital signature or an authenticated update feed.

`roblox.yml` was generated using Selene's official generate-roblox-std command on 2026-11-05 and is bundled to avoid network access during lint checks.


## Roblox analysis (0.4)

Luau LSP 1.70.1 (MIT), unmodified binary: https://github.com/JohnnyMorganz/luau-lsp/tree/1.70.1. License in licenses/Luau-LSP-MIT.txt; executable hash in toolchain-manifest.json. API reference data comes from Roblox's public Studio CDN. Generated Roblox definitions come from the Luau LSP project. Snapshot sources, version, collection time and file hashes are in api/manifest.json.
