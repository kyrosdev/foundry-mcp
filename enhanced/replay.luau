local input = game:GetService("UserInputService")
local virtual = assert(input:CreateVirtualInput(), "VirtualInput unavailable in this client")
local started = os.clock()
for _, event in payload.events do
	local delay = event.t - (os.clock() - started)
	if delay > 0 then task.wait(delay) end
	if event.key then
		virtual:SendKey(event.down, Enum.KeyCode[event.key], false)
	else
		local position = Vector2.new(event.x, event.y)
		if event.button then
			virtual:SendMouseButton(position, Enum.UserInputType[event.button], event.down, 0)
		else
			virtual:SendMousePosition(position)
		end
	end
end
return true
