local Http = game:GetService("HttpService")
local state = shared.__StudioMCPSubscriptions and shared.__StudioMCPSubscriptions[payload.id]
if not state or not state.active then
	return Http:JSONEncode({ active = false })
end
if payload.stop then
	state.close()
	return Http:JSONEncode({ active = false, stopped = true })
end
state.expires = os.clock() + 90
local events = {}
for _, value in state.ring do
	if value.seq > (payload.after or 0) then
		table.insert(events, value)
	end
	if #events >= 100 then
		break
	end
end
return Http:JSONEncode({
	active = true,
	mode = state.mode,
	events = events,
	dropped = state.dropped,
	source_coverage_limited = state.coverage_limited or false,
})
