local Http = game:GetService("HttpService")
local snapshots = shared.__StudioMCPAnalysisSnapshots
local snapshot = snapshots and snapshots[payload.token]
assert(snapshot and os.clock() - snapshot.created < 120, "Project snapshot expired; retry collection")
if payload.discard then
	snapshots[payload.token] = nil
	return "discarded"
end
assert(type(payload.offset) == "number" and payload.offset >= 0 and payload.offset % 1 == 0, "Invalid offset")
local block = string.sub(snapshot.json, payload.offset + 1, payload.offset + 32000)
local hex = (string.gsub(block, ".", function(character)
	return string.format("%02x", string.byte(character))
end))
local nextOffset = payload.offset + #block
local done = nextOffset >= #snapshot.json
if done then
	snapshots[payload.token] = nil
end
return Http:JSONEncode({
	token = payload.token,
	hex = hex,
	next_offset = nextOffset,
	done = done,
	bytes = #snapshot.json,
})
