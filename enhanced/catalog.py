"""Short native tool descriptions with exact upstream documentation on demand."""

NATIVE_DESCRIPTIONS = {
    "upload_image": "Upload images from URLs and return the Roblox asset ID for each URL.",
    "execute_luau": "Run Luau in the selected Studio DataModel. Yield between batches to keep Studio responsive.",
    "open_cloud_invoke": "Call an Open Cloud API with Studio's granted scopes. Use an apis.roblox.com path and optional JSON body or approved multipart files. Never send keys or auth headers.",
    "search_game_tree": "Explore a flat DataModel tree by path, class or keyword. Use inspect_instance for properties. Default depth 3, maximum 10.",
    "save_place": "Save the open place like Ctrl+S. Publishing is a separate action.",
    "skill": "Load a Roblox or custom skill by name. Use list_skills to discover names and guidance.",
    "subagent": "Launch a specialized Studio subagent for a multi-step task.",
    "multi_edit": "Apply ordered text replacements to one script. Use exact old text. If the path is absent, use className to specify the script type.",
    "submit_feedback": "Send Studio tool feedback to Roblox when the user authorizes it. Exclude code, personal data and credentials.",
    "http_get": "Read an approved documentation URL. Use query and context_lines to return matching excerpts. See extra_help for supported URLs.",
    "list_roblox_studios": "List connected Studio windows, place and universe IDs, and Edit/Play state. Use the target window ID as studio_id.",
    "script_read": "Read numbered script source, optionally within an inclusive line range.",
    "generate_procedural_model": "Generate a configurable model from primitive parts. Supports reference images, parts, segmentation and background jobs.",
    "generate_avatar": "Generate, upload and insert a rigged R15 avatar from a prompt. Use auto_setup_avatar to rig an existing model. Prefer async jobs when available.",
    "generate_animation": "Generate an R15+ animation from a prompt or convert an existing R6 animation to R15.",
    "generate_texture": "Replace an existing MeshPart or Model's texture using an AI prompt.",
    "inspect_instance": "Read properties, attributes and child counts for a path. Returns every instance matching an ambiguous path.",
    "search_asset": "Find assets in Creator Store or Creator Inventory. Filter by scope, type, creator, price, tags or audio duration. Use insert_asset to add a result.",
}

SHARED_NATIVE_FIELDS = {
    "studio_id": "",
    "datamodel_type": "",
    "async": "Return a job ID immediately. Check with wait_job_finished.",
    "segmentation": "auto chooses parts, none creates one piece, explicit requires partNames.",
    "partNames": "Up to 8 named parts for explicit segmentation. Pass a comma-separated string or JSON string array.",
}

SHORT_NATIVE_FIELDS = {
    ("skill", "skill_name"): "Name from list_skills.",
    ("search_game_tree", "instance_type"): "Case-sensitive IsA class filter.",
    ("search_game_tree", "keywords"): "Name contains any keyword. Separate by spaces or commas.",
    ("search_game_tree", "max_depth"): "Absolute traversal depth from path. Defaults to 3, max 10.",
    ("search_game_tree", "path"): "Case-sensitive dot path from game.",
    ("save_place", "notes"): "Set a version title only when requested. May replace current notes if unchanged. Ignored for local files.",
    ("subagent", "task"): "Task, required context and expected result.",
    ("subagent", "description"): "Short activity label for display.",
    ("inspect_instance", "path"): "Case-insensitive dot path. Up to 20 matches.",
    ("generate_procedural_model", "prompt"): "Use the user's wording and requested editable attributes. Empty if they provided only an image.",
    ("generate_animation", "duration"): "1 to 30 seconds. Omit unless requested.",
    ("generate_animation", "instance_path"): "Rig path. Defaults to the selected rig or a new default rig.",
    ("segment_mesh", "instance_path"): "MeshPart or Model path. Defaults to the single selected object.",
    ("generate_texture", "instance_path"): "MeshPart or Model path. Defaults to the single selected object.",
    ("generate_texture", "model"): "Fast by default. Quality generates a reference image first.",
    ("search_asset", "assetType"): "Use Image for uploaded images and Package for packages.",
    ("search_asset", "facets"): "Related keywords, Creator Store only.",
    ("search_asset", "groupId"): "Group ID for group scope. Use either groupId or groupName.",
    ("search_asset", "groupName"): "Case-insensitive group name for group scope. Use either groupName or groupId.",
    ("search_asset", "minPriceCents"): "Creator Store minimum price in cents.",
    ("search_asset", "priceFilter"): "Creator Store free, paid or all (default).",
    ("search_asset", "query"): "Use + between terms and quotes for phrases. Can be empty when filtering by asset type.",
    ("search_asset", "scope"): "Source to search. auto tries available sources in order.",
    ("search_asset", "tags"): "Creator Store category tags.",
    ("insert_asset", "assetName"): "Instance name. Pass the asset name when known.",
    ("insert_asset", "assetType"): "Known type skips metadata lookup. Image and Decal both insert as Decal.",
}


def advertise(tool, *, native=False, full=False):
    schema = tool.get("inputSchema", {"type":"object"})
    fields = dict(schema.get("properties", {}))
    fields["as_agent"] = ({"type":"string","description":"Logical agent ID from extra_agent."}
                          if full else {"type":"string"})
    result = {**tool, "inputSchema":{**schema, "properties":fields}}
    if native and not full:
        name = tool["name"]
        if name in NATIVE_DESCRIPTIONS:
            result["description"] = NATIVE_DESCRIPTIONS[name]
        for key, field in fields.items():
            short = SHORT_NATIVE_FIELDS.get((name, key), SHARED_NATIVE_FIELDS.get(key))
            if short is not None and isinstance(field, dict) and "description" in field:
                fields[key] = {key:value for key,value in field.items() if key != "description"}
                if short:
                    fields[key]["description"] = short
    return result
