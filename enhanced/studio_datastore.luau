local Http = game:GetService("HttpService")
assert(tostring(game.GameId) == payload.universe, "Studio is open in another experience")
local service = game:GetService("DataStoreService")
local action = payload.action

local function encode(result)
	local json = Http:JSONEncode(result)
	assert(#json <= 50000, "DataStore response exceeds 50 KB; request a smaller page")
	return json
end

local function entry(value, info)
	local row = {found = value ~= nil, value = value}
	if info then
		row.revisionId = info.Version
		row.updatedTimeMs = info.UpdatedTime
		row.userIds = info:GetUserIds()
		row.attributes = info:GetMetadata()
	end
	return row
end

local function equal(a, b)
	if type(a) ~= type(b) then return false end
	if type(a) ~= "table" then return a == b end
	for key, value in a do
		if not equal(value, b[key]) then return false end
	end
	for key in b do
		if a[key] == nil then return false end
	end
	return true
end

local function page(pages, field, convert)
	for _ = 1, payload.page do
		if pages.IsFinished then return encode({[field] = {}}) end
		pages:AdvanceToNextPageAsync()
	end
	local items = {}
	for _, row in pages:GetCurrentPage() do
		table.insert(items, convert(row))
	end
	return encode({
		[field] = items,
		nextPageToken = if pages.IsFinished then nil else tostring(payload.page + 1),
	})
end

if action == "stores" then
	return page(service:ListDataStoresAsync("", payload.page_size), "dataStores", function(row)
		return {id = row.DataStoreName}
	end)
end
if payload.memory then
	local map = game:GetService("MemoryStoreService"):GetSortedMap(payload.store)
	if action == "get" then
		local value, sortKey = map:GetAsync(payload.key)
		return encode({found = value ~= nil, value = value, sortKey = sortKey})
	end
	local items = map:GetRangeAsync(Enum.SortDirection.Ascending, payload.page_size, payload.bound)
	local last = items[#items]
	return encode({
		items = items,
		nextPageToken = if #items == payload.page_size then Http:JSONEncode({key = last.key, sortKey = last.sortKey}) else nil,
	})
end
if payload.ordered then
	local store = service:GetOrderedDataStore(payload.store, payload.scope)
	if action == "get" then
		return encode(entry(store:GetAsync(payload.key)))
	end
	return page(store:GetSortedAsync(false, payload.page_size), "entries", function(row)
		return {id = row.key, value = row.value}
	end)
end

local store = service:GetDataStore(payload.store, payload.scope)
if action == "list" then
	return page(store:ListKeysAsync("", payload.page_size, "", true), "entries", function(row)
		return {id = row.KeyName}
	end)
end
if action == "revisions" then
	return page(store:ListVersionsAsync(payload.key, Enum.SortDirection.Descending, nil, nil, payload.page_size), "revisions", function(row)
		return {revisionId = row.Version, createdTimeMs = row.CreatedTime, deleted = row.IsDeleted}
	end)
end
if action == "apply" then
	local value, info = store:UpdateAsync(payload.key, function(current, currentInfo)
		if not currentInfo or not equal(entry(current, currentInfo), payload.expected) then
			return nil
		end
		return payload.value, currentInfo:GetUserIds(), currentInfo:GetMetadata()
	end)
	if not info then return encode({conflict = true}) end
	return encode({entry = entry(value, info)})
end
assert(action == "get" or action == "prepare", "Unsupported DataStore operation")
local options = Instance.new("DataStoreGetOptions")
options.UseCache = false
local value, info = store:GetAsync(payload.key, options)
return encode(entry(value, info))
