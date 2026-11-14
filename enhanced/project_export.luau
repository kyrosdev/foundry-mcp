-- MCP_PROJECT_EXPORT
local Http = game:GetService("HttpService")
local Editor = game:GetService("ScriptEditorService")
local root = { name = "game", className = "DataModel", children = {} }
local queue = { { instance = game, node = root } }
local nodes, scripts, bytes = 0, 0, 0
local aliases = 0
local excludedEditorServices = {}
local complete = true
local index = 1
while index <= #queue do
	if index > 200000 then
		complete = false
		break
	end
	local entry = queue[index]
	local instance, node = entry.instance, entry.node
	nodes += 1
	if instance:IsA("LuaSourceContainer") then
		scripts += 1
		if scripts > 1000 then
			complete = false
			break
		end
		local ok, source = pcall(function()
			return Editor:GetEditorSource(instance)
		end)
		assert(ok, "Cannot read editor source: " .. instance:GetFullName())
		bytes += #source
		if bytes > 8000000 then
			complete = false
			break
		end
		node.source = source
	end
	local names = {}
	local children = instance:GetChildren()
	local occupied = {}
	for _, child in children do
		occupied[child.Name] = true
	end
	for _, child in children do
		local virtualName = child.Name
		if names[child.Name] then
			aliases += 1
			virtualName = "__MCP_Duplicate_" .. tostring(aliases)
			while occupied[virtualName] do
				virtualName ..= "_"
			end
			occupied[virtualName] = true
		end
		names[child.Name] = true
		if #queue >= 200000 then
			complete = false
			break
		end
		local childNode = { name = virtualName, className = child.ClassName, children = {} }
		if virtualName ~= child.Name then
			childNode.actualName = child.Name
		end
		table.insert(node.children, childNode)
		local className = child.ClassName
		local editorOnly = string.find(className, "Plugin")
			or string.sub(className, 1, 4) == "Core"
			or string.sub(className, 1, 6) == "Studio"
		if instance == game and editorOnly and not (exportOptions and exportOptions.includeEditor) then
			table.insert(excludedEditorServices, child.Name)
			continue
		end
		table.insert(queue, { instance = child, node = childNode })
	end
	if not complete then
		break
	end
	if index % 500 == 0 then
		task.wait()
	end
	index += 1
end
return snapshotResponse(Http:JSONEncode({
	tree = root,
	nodes = nodes,
	scripts = scripts,
	complete = complete,
	duplicate_aliases = aliases,
	excluded_editor_services = excludedEditorServices,
	captured_at = DateTime.now():ToIsoDate(),
	place_id = game.PlaceId,
	universe_id = game.GameId,
}))
