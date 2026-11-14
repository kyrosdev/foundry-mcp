-- The shared instance codecs and path resolver are prepended by the server.
local Editor = game:GetService("ScriptEditorService")
if payload.local_copy then
	assert(game.PlaceId == 0 and game.GameId == 0, "Validation must target an unpublished local file")
end
-- Keep Instance wrappers alive across separate command-bar invocations.
local identityState = shared.__MCPExtraPlanIdentities
if not identityState or identityState.count >= 4096 then
	identityState = { values = {}, count = 0 }
	shared.__MCPExtraPlanIdentities = identityState
end
local identities = identityState.values
local function identity(instance)
	if not identities[instance] then
		identities[instance] = Http:GenerateGUID(false)
		identityState.count += 1
	end
	return identities[instance]
end
local function locate(path)
	local parent = resolve(table.move(path, 1, #path - 1, 1, {}))
	local found
	for _, child in parent:GetChildren() do
		if child.Name == path[#path] then
			assert(not found, "Ambiguous target")
			found = child
		end
	end
	return parent, found
end

local function property(instance, name)
	local value = instance[name]
	local kind = typeof(value)
	assert(
		kind == "Instance"
			or kind == "string"
			or kind == "number"
			or kind == "boolean"
			or kind == "EnumItem"
			or kind == "Vector3"
			or kind == "Vector2"
			or kind == "Color3"
			or kind == "CFrame"
			or kind == "UDim"
			or kind == "UDim2",
		"Unsupported reversible property " .. name
	)
	return value
end

local function matches(instance, expected, references)
	for name, value in expected do
		if property(instance, name) ~= (if references and references[name] then references[name] else decode(value)) then
			return false
		end
	end
	return true
end

local function update(instance, expected, source)
	local applied = false
	Editor:UpdateSourceAsync(instance, function(current)
		applied = current == expected
		return if applied then source else nil
	end)
	assert(applied, "Concurrent source change")
end

local inspected, candidates, planned = {}, {}, {}
local function pathKey(path)
	return Http:JSONEncode(path)
end
local function parentPath(path)
	return table.move(path, 1, #path - 1, 1, {})
end
local function removable(instance, item)
	assert(#Collection:GetTags(instance) == 0, "Created object gained tags")
	for name in instance:GetAttributes() do
		assert(name == "_MCPExtraPlan", "Created object gained attributes")
	end
	if item.source then
		assert(Editor:GetEditorSource(instance) == item.source, "Created script source changed")
	end
end
local ok, problem = pcall(function()
	local removals = {}
	if payload.phase == "rollback" then
		for _, item in payload.items do
			if item.kind == "create" then
				removals[resolve(item.path)] = true
			end
		end
	end
	for i, item in payload.items do
		local plannedParent = planned[pathKey(parentPath(item.path))]
		local parent, instance
		if plannedParent then
			parent = plannedParent
		else
			parent, instance = locate(item.path)
			if item.parent_identity then
				assert(identity(parent) == item.parent_identity, "Parent was replaced")
			end
		end
		if item.kind == "create" and payload.phase ~= "rollback" then
			assert(not instance, "Creation path already exists")
			local candidate = Instance.new(item.class)
			candidates[i] = candidate
			candidate.Name = item.path[#item.path]
			if candidate:IsA("LuaSourceContainer") then
				assert(type(item.source) == "string", "A new script requires source")
				candidate.Source = item.source
			end
			local values = {}
			for name, value in item.properties do
				if type(value) == "table" and value.type == "Instance" then
					assert(candidate:IsA("ObjectValue") and name == "Value", "New references require ObjectValue.Value")
					values[name] = value
				else
					property(candidate, name)
					candidate[name] = decode(value)
					values[name] = encode(property(candidate, name))
				end
			end
			inspected[i] = { parent = parent, instance = candidate, proposed = values }
			planned[pathKey(item.path)] = candidate
			if not plannedParent then
				inspected[i].parent_identity = identity(parent)
			end
		else
			assert(instance, "Target missing")
			if item.identity then
				assert(identity(instance) == item.identity, "Target object was replaced")
			end
			if item.kind == "relocate" then
				local destination, occupied = locate(item.destination)
				assert(not occupied, "Relocation destination already exists")
				if item.destination_identity then
					assert(identity(destination) == item.destination_identity, "Destination parent was replaced")
				end
				inspected[i] = {
					instance = instance,
					parent = parent,
					destination = destination,
					parent_identity = identity(parent),
					destination_identity = identity(destination),
				}
			elseif item.kind == "script" then
				assert(instance:IsA("LuaSourceContainer"), "Target is not a script")
				local before = Editor:GetEditorSource(instance)
				local captureSource = payload.phase == "prepare" and item.expected == nil
				assert(captureSource or before == item.expected, "Source changed")
				inspected[i] = { instance = instance, before = if captureSource then before else nil }
			else
				local before, proposed, references = {}, {}, {}
				for name, value in item.properties do
					if item.kind == "create" and type(value) == "table" and value.type == "Instance" then
						-- A prior rollback item can detach this referenced object.
						local target = decode(value)
						assert(property(instance, name) == target, "Created reference changed")
						before[name], proposed[name], references[name] = value, value, target
					else
						before[name] = encode(property(instance, name))
						proposed[name] = encode(decode(value))
					end
				end
				if item.class then
					assert(instance.ClassName == item.class, "Target class changed")
				end
				if item.expected then
					assert(matches(instance, item.expected), "Property changed since preview")
				end
				if item.kind == "create" then
					assert(instance:GetAttribute("_MCPExtraPlan") == payload.plan, "Created object identity changed")
					removable(instance, item)
					for _, child in instance:GetChildren() do
						assert(removals[child], "Created object gained children")
					end
				end
				inspected[i] = {
					parent = parent,
					instance = instance,
					before = before,
					proposed = proposed,
					references = references,
					class = instance.ClassName,
				}
			end
		end
	end
	if payload.phase ~= "rollback" then
		for i, item in payload.items do
			if item.kind == "create" then
				local row = inspected[i]
				row.references = {}
				for name, value in row.proposed do
					if type(value) == "table" and value.type == "Instance" then
						local target = planned[pathKey(value.path)] or resolve(value.path)
						row.instance[name] = target
						row.references[name] = target
					end
				end
			end
		end
	end
end)

local undo = {}
if ok and payload.phase ~= "prepare" then
	local recording = History:TryBeginRecording("MCP mixed changes")
	if not recording and not History:IsRecordingInProgress() then
		ok, problem = false, "Cannot begin undo recording"
	else
		ok, problem = pcall(function()
			for i, item in payload.items do
				local row = inspected[i]
				local instance = row.instance
				if item.kind == "script" then
					update(instance, item.expected, item.source)
					table.insert(undo, function()
						update(instance, item.source, item.expected)
					end)
				elseif item.kind == "relocate" then
					local parent, present = locate(item.path)
					local destination, occupied = locate(item.destination)
					assert(parent == row.parent and present == instance and destination == row.destination and not occupied,
						"Relocation paths changed")
					table.insert(undo, function()
						assert(instance.Parent == destination or instance.Parent == parent, "Object moved during recovery")
						instance.Parent = parent
						instance.Name = item.path[#item.path]
					end)
					instance.Name = item.destination[#item.destination]
					instance.Parent = destination
				elseif item.kind == "create" then
					if payload.phase == "rollback" then
						assert(
							instance.Parent == row.parent and matches(instance, item.expected, row.references),
							"Created object changed"
						)
						assert(#instance:GetChildren() == 0, "Created object gained children")
						removable(instance, item)
						instance.Parent = nil
						table.insert(undo, function()
							local parent, present = locate(item.path)
							assert(not present and parent == row.parent, "Creation path occupied during recovery")
							instance.Parent = parent
						end)
					else
						local parent, present = locate(item.path)
						assert(not present and parent == row.parent, "Creation path changed")
						instance:SetAttribute("_MCPExtraPlan", payload.plan)
						instance.Parent = parent
						candidates[i] = nil
						table.insert(undo, function()
							assert(
								instance.Parent == parent and matches(instance, row.proposed, row.references),
								"Created object changed"
							)
							assert(#instance:GetChildren() == 0, "Created object gained children")
							removable(instance, item)
							instance.Parent = nil
						end)
					end
				else
					assert(
						resolve(item.path) == instance and matches(instance, item.expected),
						"Target changed during apply"
					)
					for name, value in item.properties do
						local before = property(instance, name)
						instance[name] = decode(value)
						local written = property(instance, name)
						table.insert(undo, function()
							assert(property(instance, name) == written, "Manual edit preserved")
							instance[name] = before
						end)
						assert(written == decode(value), "Studio normalized requested property " .. name)
					end
				end
			end
		end)
		if not ok then
			local failures = {}
			for i = #undo, 1, -1 do
				local restored, detail = pcall(undo[i])
				if not restored then
					table.insert(failures, tostring(detail))
				end
			end
			if #failures > 0 then
				problem = tostring(problem) .. " | recovery: " .. table.concat(failures, " | ")
			end
			payload.conflict = #failures > 0
		end
		if recording then
			History:FinishRecording(
				recording,
				if ok or payload.conflict
					then Enum.FinishRecordingOperation.Commit
					else Enum.FinishRecordingOperation.Cancel
			)
		end
	end
end
for _, instance in candidates do
	instance:Destroy()
end
local result = {}
if ok and payload.phase == "prepare" then
	for i, row in inspected do
		result[i] = {
			before = row.before,
			proposed = row.proposed,
			class = row.class,
			parent_identity = row.parent_identity,
			destination_identity = row.destination_identity,
			identity = if payload.items[i].kind ~= "create" then identity(row.instance) else nil,
		}
	end
end
return snapshotResponse(Http:JSONEncode({
	success = ok,
	error = if ok then nil else tostring(problem),
	conflict = payload.conflict,
	items = result,
}))
