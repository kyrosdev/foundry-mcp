local Http = game:GetService("HttpService")
local History = game:GetService("ChangeHistoryService")
local Collection = game:GetService("CollectionService")
local function resolve(path)
	assert(type(path) == "table", "path must be an array of exact child names")
	local current = game
	for _, name in ipairs(path) do
		assert(type(name) == "string", "path segments must be strings")
		local found = nil
		for _, child in current:GetChildren() do
			if child.Name == name then
				assert(not found, "Ambiguous sibling name: " .. name)
				found = child
			end
		end
		assert(found, "Path segment not found: " .. name)
		current = found
	end
	return current
end
local function exactPath(instance)
	local path = {}
	while instance and instance ~= game do
		table.insert(path, 1, instance.Name)
		instance = instance.Parent
	end
	return path
end
local function decode(value)
	if type(value) ~= "table" then
		return value
	end
	local kind = value.type
	local v = value.value
	if kind == "Instance" then
		return resolve(value.path)
	end
	if kind == "Enum" then
		assert(type(v) == "table" and #v == 2, "Enum value: [enumType, item]")
		return Enum[v[1]][v[2]]
	end
	assert(type(v) == "table", "Typed value requires value array")
	local constructors = {
		Vector2 = { 2, Vector2.new },
		Vector3 = { 3, Vector3.new },
		Color3 = { 3, Color3.new },
		UDim = { 2, UDim.new },
		UDim2 = { 4, UDim2.new },
		CFrame = { 12, CFrame.new },
	}
	local constructor = constructors[kind]
	assert(
		constructor and (#v == constructor[1] or (kind == "CFrame" and #v == 3)),
		"Unsupported typed value or component count"
	)
	for _, n in ipairs(v) do
		assert(type(n) == "number", "Components must be numbers")
	end
	return constructor[2](table.unpack(v))
end
local function encode(value)
	local kind = typeof(value)
	if kind == "Instance" then
		return { type = kind, path = exactPath(value) }
	end
	if kind == "Vector3" then
		return { type = kind, value = { value.X, value.Y, value.Z } }
	end
	if kind == "Vector2" then
		return { type = kind, value = { value.X, value.Y } }
	end
	if kind == "Color3" then
		return { type = kind, value = { value.R, value.G, value.B } }
	end
	if kind == "CFrame" then
		return { type = kind, value = { value:GetComponents() } }
	end
	if kind == "UDim" then
		return { type = kind, value = { value.Scale, value.Offset } }
	end
	if kind == "UDim2" then
		return { type = kind, value = { value.X.Scale, value.X.Offset, value.Y.Scale, value.Y.Offset } }
	end
	if kind == "EnumItem" then
		return { type = "Enum", value = { tostring(value.EnumType), value.Name } }
	end
	if kind == "string" or kind == "number" or kind == "boolean" then
		return value
	end
	return tostring(value)
end
local function attributes(instance)
	local out = {}
	for k, v in instance:GetAttributes() do
		out[k] = encode(v)
	end
	return out
end
local function bounded(value, default, maximum)
	value = value or default
	assert(type(value) == "number" and value % 1 == 0 and value >= 1 and value <= maximum, "Invalid limit")
	return value
end
local function transaction(callback)
	local recording = History:TryBeginRecording("MCP Extra " .. operation)
	-- Reuse the surrounding Studio command's Undo recording when present.
	assert(recording or History:IsRecordingInProgress(), "Cannot begin undo recording")
	local rollback = {}
	local ok, result = pcall(callback, rollback)
	if not ok then
		local failures = {}
		for i = #rollback, 1, -1 do
			local restored, err = pcall(rollback[i])
			if not restored then
				table.insert(failures, tostring(err))
			end
		end
		if recording then
			History:FinishRecording(recording, Enum.FinishRecordingOperation.Cancel)
		end
		error(tostring(result) .. (#failures > 0 and ("; rollback errors: " .. table.concat(failures, "; ")) or ""))
	end
	if recording then
		History:FinishRecording(recording, Enum.FinishRecordingOperation.Commit)
	end
	return result
end
local result
if operation == "instance_actions" then
	assert(type(payload.items) == "table" and #payload.items > 0 and #payload.items <= 100, "Expected 1..100 items")
	local planned = {}
	for _, item in ipairs(payload.items) do
		assert(
			item.action == "clone" or item.action == "move" or item.action == "rename" or item.action == "trash",
			"Invalid action"
		)
		local target = resolve(item.path)
		assert(target ~= game and target.Parent ~= game, "Cannot modify root services")
		if item.parent then
			local parent = resolve(item.parent)
			assert(parent ~= target and not parent:IsDescendantOf(target), "Cannot parent into own subtree")
		end
		if item.action == "move" then
			assert(item.parent, "move requires parent")
		end
		if item.action == "rename" then
			assert(type(item.name) == "string" and #item.name > 0, "rename requires name")
		end
		table.insert(planned, { action = item.action, path = item.path, parent = item.parent, name = item.name })
	end
	if payload.preview ~= false then
		result = { preview = true, operations = planned }
	else
		result = transaction(function(rollback)
			local changed = {}
			for _, item in ipairs(payload.items) do
				local target = resolve(item.path)
				local oldParent, oldName = target.Parent, target.Name
				if item.action == "clone" then
					local copy = target:Clone()
					assert(copy, "Instance is not Archivable")
					table.insert(rollback, function()
						copy:Destroy()
					end)
					copy.Name = item.name or target.Name
					copy.Parent = if item.parent then resolve(item.parent) else oldParent
					target = copy
				else
					table.insert(rollback, function()
						target.Parent = oldParent
						target.Name = oldName
					end)
					if item.action == "rename" then
						target.Name = item.name
					end
					if item.action == "move" then
						target.Parent = resolve(item.parent)
					end
					if item.action == "trash" then
						local storage = game:GetService("ServerStorage")
						local trash = storage:FindFirstChild("MCP_Trash")
						if not trash then
							trash = Instance.new("Folder")
							trash.Name = "MCP_Trash"
							trash.Parent = storage
							local created = trash
							table.insert(rollback, 1, function()
								created:Destroy()
							end)
						end
						assert(trash:IsA("Folder"), "MCP_Trash must be a Folder")
						assert(target ~= trash and not trash:IsDescendantOf(target), "Cannot trash the trash container")
						target.Name = target.Name .. "_" .. Http:GenerateGUID(false)
						target.Parent = trash
					end
				end
				table.insert(changed, { path = exactPath(target), action = item.action })
			end
			return { preview = false, operations = changed }
		end)
	end
elseif operation == "selection" then
	local Selection = game:GetService("Selection")
	if payload.paths then
		assert(#payload.paths <= 100, "Maximum 100 paths")
		local selected = {}
		for _, path in ipairs(payload.paths) do
			table.insert(selected, resolve(path))
		end
		Selection:Set(selected)
	end
	result = {}
	for _, instance in Selection:Get() do
		table.insert(result, { path = exactPath(instance), class = instance.ClassName })
	end
elseif operation == "snapshot" or operation == "diagnostics" then
	local root = resolve(payload.path or {})
	local limit = bounded(payload.limit, 500, 5000)
	local depth = bounded(payload.depth, 4, 32)
	local properties = payload.properties or {}
	assert(#properties <= 30, "Maximum 30 properties")
	local queue = { { root, 0 } }
	local rows, counts = {}, {}
	local index = 1
	while index <= #queue and index <= limit do
		local instance, level = table.unpack(queue[index])
		counts[instance.ClassName] = (counts[instance.ClassName] or 0) + 1
		if operation == "snapshot" then
			local row = {
				path = exactPath(instance),
				class = instance.ClassName,
				attributes = attributes(instance),
				tags = Collection:GetTags(instance),
				properties = {},
			}
			for _, property in ipairs(properties) do
				assert(type(property) == "string", "Property names must be strings")
				local ok, v = pcall(function()
					return instance[property]
				end)
				local kind = typeof(v)
				local supported = kind == "string" or kind == "number" or kind == "boolean" or kind == "Instance"
					or kind == "Vector3" or kind == "Vector2" or kind == "Color3" or kind == "CFrame"
					or kind == "UDim" or kind == "UDim2" or kind == "EnumItem"
				if ok and (not payload.reversible_only or supported) then
					row.properties[property] = encode(v)
				elseif not payload.reversible_only then
					row.properties[property] = { error = tostring(v) }
				end
			end
			table.insert(rows, row)
		end
		if level < depth then
			for _, child in instance:GetChildren() do
				if #queue >= limit + 1 then
					break
				end
				table.insert(queue, { child, level + 1 })
			end
		end
		if index % 100 == 0 then
			task.wait()
		end
		index += 1
	end
	result = {
		instances = rows,
		counts = counts,
		visited = index - 1,
		truncated = #queue >= limit + 1,
		depth = depth,
		placeId = game.PlaceId,
		gameId = game.GameId,
		jobId = game.JobId,
	}
elseif operation == "batch_create" or operation == "batch_properties" then
	assert(
		type(payload.items) == "table" and #payload.items > 0 and #payload.items <= 100,
		"items must contain 1 to 100 entries"
	)
	result = transaction(function(rollback)
		local paths = {}
		for _, item in ipairs(payload.items) do
			local instance
			local parent
			if operation == "batch_create" then
				parent = resolve(item.parent)
				instance = Instance.new(item.class)
				table.insert(rollback, function()
					instance:Destroy()
				end)
				instance.Name = item.name or item.class
			else
				instance = resolve(item.path)
			end
			local count = 0
			for property, value in pairs(item.properties or {}) do
				count += 1
				assert(count <= 50, "Maximum 50 properties per item")
				assert(
					property ~= "Parent" and property ~= "Source",
					"Use parent for creation and native multi_edit for source"
				)
				local old = instance[property]
				table.insert(rollback, function()
					instance[property] = old
				end)
				instance[property] = decode(value)
			end
			if parent then
				instance.Parent = parent
			end
			table.insert(paths, exactPath(instance))
		end
		return { paths = paths, changed = #paths }
	end)
elseif operation == "attributes" then
	local instance = resolve(payload.path)
	result = transaction(function(rollback)
		local function set(name, value)
			local old = instance:GetAttribute(name)
			table.insert(rollback, function()
				instance:SetAttribute(name, old)
			end)
			instance:SetAttribute(name, value)
		end
		for name, value in pairs(payload.set or {}) do
			set(name, decode(value))
		end
		for _, name in ipairs(payload.remove or {}) do
			set(name, nil)
		end
		return attributes(instance)
	end)
elseif operation == "tags" then
	local instance = resolve(payload.path)
	result = transaction(function(rollback)
		local old = Collection:GetTags(instance)
		table.insert(rollback, function()
			for _, tag in Collection:GetTags(instance) do
				Collection:RemoveTag(instance, tag)
			end
			for _, tag in old do
				Collection:AddTag(instance, tag)
			end
		end)
		for _, tag in ipairs(payload.add or {}) do
			Collection:AddTag(instance, tag)
		end
		for _, tag in ipairs(payload.remove or {}) do
			Collection:RemoveTag(instance, tag)
		end
		return Collection:GetTags(instance)
	end)
else
	error("Unknown operation")
end
return Http:JSONEncode(result)
