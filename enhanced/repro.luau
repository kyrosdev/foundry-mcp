local input = game:GetService("UserInputService")
local logs = game:GetService("LogService")
local Http = game:GetService("HttpService")
local recordings = shared.__FoundryReproductions or {}
shared.__FoundryReproductions = recordings
local function disconnect(record)
	for _, connection in record.connections do connection:Disconnect() end
	table.clear(record.connections)
end

local record = recordings[payload.id]
if payload.action == "start" then
	for id, old in recordings do
		if old.stopped or os.clock() - old.started > 120 then
			disconnect(old)
			recordings[id] = nil
		else
			error("A reproduction is already recording in this DataModel")
		end
	end
	record = {started = os.clock(), events = {}, logs = {}, dropped = 0, connections = {}}
	recordings[payload.id] = record
	table.insert(record.connections, logs.MessageOut:Connect(function(message, kind)
		if kind == Enum.MessageType.MessageError or kind == Enum.MessageType.MessageWarning then
			if #record.logs >= 40 then record.dropped += 1 return end
			table.insert(record.logs, {t = os.clock()-record.started, kind = kind.Name, message = string.sub(message,1,300)})
		end
	end))
	if payload.client then
		local held = {}
		local lastMove = 0
		local function capture(event, down)
			local kind = event.UserInputType
			local key = event.KeyCode
			local data
			if kind == Enum.UserInputType.Keyboard and key ~= Enum.KeyCode.Unknown and key ~= Enum.KeyCode.Escape then
				if input:GetFocusedTextBox() then
					if down then record.dropped += 1 end
					if not held[key.Name] then return end
				end
				data = {key = key.Name, down = down}
			elseif kind == Enum.UserInputType.MouseButton1 or kind == Enum.UserInputType.MouseButton2 then
				data = {button = kind.Name, down = down, x = event.Position.X, y = event.Position.Y}
			elseif kind == Enum.UserInputType.MouseMovement then
				if os.clock()-lastMove < 0.1 then return end
				lastMove = os.clock()
				data = {x = event.Position.X, y = event.Position.Y}
			else
				if down then record.dropped += 1 end
				return
			end
			if #record.events >= 240 then record.dropped += 1 return end
			local id = data.key or data.button
			if id then
				if not down and not held[id] then return end
				held[id] = if down then table.clone(data) else nil
			end
			data.t = os.clock()-record.started
			table.insert(record.events, data)
		end
		record.release = function()
			for _, event in held do
				event.down = false
				event.t = math.min(os.clock()-record.started, 120)
				table.insert(record.events, event)
			end
			table.clear(held)
		end
		table.insert(record.connections, input.InputBegan:Connect(function(event) capture(event,true) end))
		table.insert(record.connections, input.InputEnded:Connect(function(event) capture(event,false) end))
		table.insert(record.connections, input.InputChanged:Connect(function(event)
			if event.UserInputType == Enum.UserInputType.MouseMovement then capture(event,nil) end
		end))
	end
	task.delay(120, function()
		disconnect(record)
		if record.release then record.release() end
	end)
	return Http:JSONEncode({recording = true})
end

assert(record, "Recording not found in this play session")
disconnect(record)
if record.release then record.release() end
record.stopped = true
return Http:JSONEncode({events = record.events, logs = record.logs, dropped = record.dropped})
