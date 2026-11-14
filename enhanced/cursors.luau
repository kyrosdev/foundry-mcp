local run = game:GetService("RunService")
if not run:IsEdit() then return false end
local core = game:GetService("CoreGui")
local http = game:GetService("HttpService")
local key = "__FoundryViewportCursors"
local state = shared[key]
local RENDERER_VERSION = 31
local WIDTH, HEIGHT = 222, 92
local SCALE = 1.14
local SLOT_HEIGHT = math.ceil(HEIGHT*SCALE)+6
local PAPER = Color3.fromRGB(255,249,246)
local INK = Color3.fromRGB(24,27,36)
local MUTED = Color3.fromRGB(92,83,89)
local SHARED_WIDTH = 218
local SHARED_ROW_HEIGHT = 37
local SHARED_LIMIT = 4
local EFFECT_COLORS = {
	create = Color3.fromRGB(97,215,176),
	move = Color3.fromRGB(245,183,104),
	edit = Color3.fromRGB(166,151,246),
}

local function loadArt()
	local encoded = (function()
		-- CURSOR_ART
	end)()
	local art = {}
	for name, hex in encoded do
		local pixels = buffer.create(#hex/2)
		for i = 1,#hex,2 do
			buffer.writeu8(pixels,(i-1)/2,tonumber(string.sub(hex,i,i+1),16))
		end
		local ok, editable = pcall(function()
			return game:GetService("AssetService"):CreateEditableImage({Size=Vector2.new(16,16)})
		end)
		if ok and editable then
			editable:WritePixelsBuffer(Vector2.zero,Vector2.new(16,16),pixels)
			art[name] = editable
		end
	end
	return art
end

local function label(parent, name, size, color, font)
	local text = Instance.new("TextLabel")
	text.Name = name
	text.BackgroundTransparency = 1
	text.TextColor3 = color
	text.TextSize = size
	text.Font = font
	text.TextXAlignment = Enum.TextXAlignment.Left
	text.TextTruncate = Enum.TextTruncate.AtEnd
	text.Parent = parent
	return text
end

local function panel(parent, name, position, size, radius)
	local frame = Instance.new("Frame")
	frame.Name = name
	frame.Position = position
	frame.Size = size
	frame.BackgroundColor3 = PAPER
	frame.BorderSizePixel = 0
	local corner = Instance.new("UICorner")
	corner.CornerRadius = UDim.new(0,radius)
	corner.Parent = frame
	frame.Parent = parent
	return frame
end

local function providerStyle(row)
	if string.find(string.lower(row.provider),"claude",1,true) then
		return "Claude", Color3.fromRGB(236,105,77)
	end
	return "Codex", Color3.fromRGB(126,104,244)
end

local function activity(row)
	return (row.state == "done" and "✓ " or row.state == "failed" and "! " or "") .. row.stage
end

local function openTarget(view)
	local target = view.target
	local camera = workspace.CurrentCamera
	if not target or not target:IsDescendantOf(game) then return end
	if target:IsA("LuaSourceContainer") then
		local editor = game:GetService("ScriptEditorService")
		local line = view.row.line
		local opened = editor:OpenScriptDocumentAsync(target)
		if opened and line then
			local document = editor:FindScriptDocument(target)
			if document then document:ForceSetSelectionAsync(math.clamp(line,1,document:GetLineCount()),1) end
		end
		return
	end
	game:GetService("Selection"):Set({target})
	if camera and target:IsA("BasePart") then
		camera:ZoomToExtents(target.CFrame,target.Size)
	elseif camera and target:IsA("Model") then
		camera:ZoomToExtents(target:GetBoundingBox())
	end
end

local function openReport(state, report)
	if state.report then state.report:Destroy() end
	local frame = panel(state.gui,"PlaytestReport",UDim2.fromScale(0.5,0.5),UDim2.new(0.88,0,0.8,0),10)
	frame.AnchorPoint = Vector2.new(0.5,0.5)
	frame.ZIndex = 20
	local limits = Instance.new("UISizeConstraint")
	limits.MaxSize = Vector2.new(480,400)
	limits.Parent = frame
	local heading = label(frame,"Title",17,INK,Enum.Font.GothamMedium)
	heading.Text = if report.passed then "Playtest passed" else "Playtest failed"
	heading.Position = UDim2.fromOffset(16,0)
	heading.Size = UDim2.new(1,-104,0,44)
	local close = Instance.new("TextButton")
	close.Name = "Close"
	close.Text = "Close"
	close.Font = Enum.Font.GothamMedium
	close.TextSize = 13
	close.TextColor3 = INK
	close.BackgroundTransparency = 1
	close.Position = UDim2.new(1,-74,0,4)
	close.Size = UDim2.fromOffset(66,36)
	close.Parent = frame
	local list = Instance.new("ScrollingFrame")
	list.BackgroundTransparency = 1
	list.BorderSizePixel = 0
	list.Position = UDim2.fromOffset(16,48)
	list.Size = UDim2.new(1,-32,1,-60)
	list.CanvasSize = UDim2.new()
	list.AutomaticCanvasSize = Enum.AutomaticSize.Y
	list.ScrollBarThickness = 5
	list.Parent = frame
	local layout = Instance.new("UIListLayout")
	layout.SortOrder = Enum.SortOrder.LayoutOrder
	layout.Padding = UDim.new(0,8)
	layout.Parent = list
	for index, step in report.steps do
		local line = label(list,"Step",13,INK,Enum.Font.Code)
		line.LayoutOrder = index
		line.TextWrapped = true
		line.TextTruncate = Enum.TextTruncate.None
		line.Text = (if step.passed then "✓ " else "! ") .. step.title
			.. (if step.detail ~= "" then "\n" .. step.detail else "")
		line.Size = UDim2.new(1,-10,0,0)
		line.AutomaticSize = Enum.AutomaticSize.Y
	end
	state.report = frame
	return close
end

local function closeReport(state)
	if state.report then
		state.report:Destroy()
		state.report, state.reportClose = nil, nil
	end
end

local function activate(view, follow)
	local current = shared[key]
	if not current then return end
	if follow then
		if current.follow == view then
			current.follow = nil
		elseif view.target and view.target:IsDescendantOf(workspace) then
			current.follow = view
		end
	elseif view.row.report then
		current.reportClose = openReport(current,view.row.report)
	else
		local ok, err = pcall(openTarget,view)
		if not ok then warn("Foundry cursor could not open the target:",err) end
	end
end

local function addTargetButton(parent)
	local button = Instance.new("TextButton")
	button.Name = "OpenTarget"
	button.BackgroundTransparency = 1
	button.Size = UDim2.fromScale(1,1)
	button.Text = ""
	button.ZIndex = 3
	button.Parent = parent
	return button
end

local function addFollowButton(parent)
	local button = addTargetButton(parent)
	button.Name = "FollowAgent"
	button.Position = UDim2.new(1,-22,0,0)
	button.Size = UDim2.fromOffset(22,21)
	button.Text = "◎"
	button.TextColor3 = MUTED
	button.TextSize = 15
	button.Font = Enum.Font.GothamMedium
	button.ZIndex = 4
	return button
end

local function showShared(view, members, art)
	local count = #members
	local limit = math.min(count, SHARED_LIMIT)
	local height = 28 + limit * SHARED_ROW_HEIGHT + (count > limit and 18 or 0)
	if not view.shared then
		local frame = panel(view.card,"SharedLabel",UDim2.fromOffset(11,19),UDim2.fromOffset(SHARED_WIDTH,height),7)
		frame.ZIndex = 2
		local heading = label(frame,"Target",12,INK,Enum.Font.GothamMedium)
		heading.Position = UDim2.fromOffset(10,0)
		heading.Size = UDim2.new(1,-20,0,25)
		local divider = Instance.new("Frame")
		divider.BackgroundColor3 = Color3.fromRGB(227,215,211)
		divider.BorderSizePixel = 0
		divider.Position = UDim2.fromOffset(10,25)
		divider.Size = UDim2.new(1,-20,0,1)
		divider.Parent = frame
		local more = label(frame,"More",10,MUTED,Enum.Font.GothamMedium)
		more.Size = UDim2.new(1,-20,0,15)
		view.shared = {frame=frame,heading=heading,more=more,rows={}}
		view.shared.button = addTargetButton(frame)
	end
	local sharedLabel = view.shared
	sharedLabel.frame.Visible = true
	local changed = sharedLabel.count ~= count
	for index = 1,limit do
		local item = sharedLabel.rows[index]
		if not item or item.row ~= members[index] then changed = true end
	end
	if not changed then return height + 19 end
	sharedLabel.count = count
	sharedLabel.frame.Size = UDim2.fromOffset(SHARED_WIDTH,height)
	sharedLabel.frame:SetAttribute("MemberCount",count)
	sharedLabel.heading.Text = members[1].path[#members[1].path]
	sharedLabel.more.Visible = count > limit
	sharedLabel.more.Position = UDim2.fromOffset(10,height-17)
	sharedLabel.more.Text = "+" .. (count-limit) .. " agents"
	for index = 1,limit do
		local member = members[index]
		local item = sharedLabel.rows[index]
		if not item then
			local line = Instance.new("Frame")
			line.Name = "Member"
			line.BackgroundTransparency = 1
			line.Position = UDim2.fromOffset(10,28+(index-1)*SHARED_ROW_HEIGHT)
			line.Size = UDim2.new(1,-20,0,SHARED_ROW_HEIGHT)
			line.Parent = sharedLabel.frame
			local mark = Instance.new("ImageLabel")
			mark.Name = "Provider"
			mark.BackgroundTransparency = 1
			mark.Position = UDim2.fromOffset(0,6)
			mark.Size = UDim2.fromOffset(13,13)
			mark.Parent = line
			local name = label(line,"Agent",11,INK,Enum.Font.GothamMedium)
			name.Position = UDim2.fromOffset(20,2)
			name.Size = UDim2.new(1,-20,0,17)
			local stage = label(line,"Stage",11,MUTED,Enum.Font.Code)
			stage.Position = UDim2.fromOffset(20,17)
			stage.Size = UDim2.new(1,-20,0,16)
			item = {line=line,mark=mark,name=name,stage=stage}
			item.follow = addFollowButton(line)
			sharedLabel.rows[index] = item
		end
		item.line.Visible = true
		if item.row ~= member then
			local provider = providerStyle(member)
			if art[provider] then item.mark.ImageContent = Content.fromObject(art[provider]) end
			item.name.Text = member.name
			item.stage.Text = activity(member)
			item.row = member
		end
	end
	for index = limit+1,#sharedLabel.rows do sharedLabel.rows[index].line.Visible = false end
	return height + 19
end

local function create(parent)
	local card = Instance.new("Frame")
	card.Name = "AgentCursor"
	card.BackgroundTransparency = 1
	card.Size = UDim2.fromOffset(WIDTH,HEIGHT)
	card.Active = false
	local scale = Instance.new("UIScale")
	scale.Scale = SCALE
	scale.Parent = card
	local pointer = Instance.new("ImageLabel")
	pointer.Name = "Pointer"
	pointer.Size = UDim2.fromOffset(16,16)
	pointer.BackgroundTransparency = 1
	pointer.ZIndex = 4
	pointer.Parent = card
	local tether = Instance.new("Frame")
	tether.Name = "LabelTether"
	tether.AnchorPoint = Vector2.new(0.5,0.5)
	tether.BackgroundColor3 = PAPER
	tether.BackgroundTransparency = 0.25
	tether.BorderSizePixel = 0
	tether.Visible = false
	tether.Parent = card
	local badge = panel(card,"Identity",UDim2.fromOffset(11,18),UDim2.fromOffset(72,21),5)
	local border = Instance.new("UIStroke")
	border.ApplyStrokeMode = Enum.ApplyStrokeMode.Border
	border.Transparency = 0.36
	border.Parent = badge
	local mark = Instance.new("ImageLabel")
	mark.Name = "Provider"
	mark.BackgroundTransparency = 1
	mark.Position = UDim2.fromOffset(8,5)
	mark.Size = UDim2.fromOffset(11,11)
	mark.Parent = badge
	local name = label(badge,"Agent",11,INK,Enum.Font.GothamMedium)
	name.Position = UDim2.fromOffset(24,0)
	name.Size = UDim2.new(1,-31,1,0)
	local bubble = panel(card,"Bubble",UDim2.fromOffset(11,45),UDim2.fromOffset(208,28),6)
	local stage = label(bubble,"Stage",12,INK,Enum.Font.Code)
	stage.Position = UDim2.fromOffset(9,5)
	stage.Size = UDim2.new(1,-18,1,-10)
	stage.TextWrapped = true
	stage.TextTruncate = Enum.TextTruncate.None
	stage.TextYAlignment = Enum.TextYAlignment.Top
	card.Parent = parent
	local view = {card=card,pointer=pointer,tether=tether,border=border,badge=badge,mark=mark,
		name=name,stage=stage,bubble=bubble}
	local compact = panel(card,"CompactLabel",UDim2.fromOffset(11,18),UDim2.fromOffset(146,22),5)
	compact.Visible = false
	local compactMark = Instance.new("ImageLabel")
	compactMark.Name = "Provider"
	compactMark.BackgroundTransparency = 1
	compactMark.Position = UDim2.fromOffset(7,5)
	compactMark.Size = UDim2.fromOffset(12,12)
	compactMark.Parent = compact
	local compactName = label(compact,"Agent",11,INK,Enum.Font.GothamMedium)
	compactName.Position = UDim2.fromOffset(24,0)
	compactName.Size = UDim2.new(1,-31,1,0)
	view.compact,view.compactMark,view.compactName = compact,compactMark,compactName
	view.compactButton = addTargetButton(compact)
	view.button = addTargetButton(badge)
	view.followButton = addFollowButton(badge)
	view.bubbleButton = addTargetButton(bubble)
	return view
end

local function resolve(path)
	local instance = game
	for _, name in path do
		local found
		for _, child in instance:GetChildren() do
			if child.Name == name then
				if found then return nil end
				found = child
			end
		end
		if not found then return nil end
		instance = found
	end
	return instance
end

local function anchor(instance)
	while instance and instance ~= workspace do
		if instance:IsA("Attachment") then return instance.WorldPosition end
		if instance:IsA("BasePart") then return instance.Position + Vector3.new(0,instance.Size.Y/2,0) end
		if instance:IsA("Model") and instance:IsDescendantOf(workspace) then
			return instance:GetPivot().Position
		end
		instance = instance.Parent
	end
end

local function updateOutlines(view,row,parent)
	for _, outline in view.outlines or {} do outline:Destroy() end
	view.outlines = {}
	if row.state ~= "done" then return end
	for _, target in row.outlines or {} do
		local instance = resolve(target.path)
		if instance and instance:IsDescendantOf(workspace) and instance:IsA("PVInstance") then
			local outline = Instance.new("Highlight")
			outline.Name = "FoundryChange"
			outline.Adornee = instance
			outline.DepthMode = Enum.HighlightDepthMode.Occluded
			outline.FillTransparency = 0.88
			outline.OutlineTransparency = 0.25
			outline.OutlineColor = EFFECT_COLORS[target.effect] or EFFECT_COLORS.edit
			outline.FillColor = outline.OutlineColor
			outline.Parent = parent
			table.insert(view.outlines,outline)
		end
	end
end

local function placeLabel(tip,width,height,viewport,occupied,maxDistance)
	local gap = 8
	local best, bestScore
	local function consider(x,y)
		x = math.clamp(x,gap,math.max(gap,viewport.X-width-gap))
		y = math.clamp(y,gap,math.max(gap,viewport.Y-height-gap))
		local nearest = Vector2.new(math.clamp(tip.X,x,x+width),math.clamp(tip.Y,y,y+height))
		local distance = (nearest-tip).Magnitude
		if distance > maxDistance then return end
		local score = distance*3 + (Vector2.new(x,y)-tip).Magnitude
		if bestScore and score >= bestScore then return end
		for _, box in occupied do
			if x < box.right+gap and x+width+gap > box.left
				and y < box.bottom+gap and y+height+gap > box.top then return end
		end
		best,bestScore = Vector2.new(x,y),score
	end
	consider(tip.X,tip.Y)
	if bestScore == 0 then
		table.insert(occupied,{left=best.X,right=best.X+width,top=best.Y,bottom=best.Y+height})
		return best
	end
	consider(tip.X-width,tip.Y)
	consider(tip.X,tip.Y-height)
	consider(tip.X-width,tip.Y-height)
	for _, box in occupied do
		consider(box.right+gap,tip.Y)
		consider(box.left-width-gap,tip.Y)
		consider(tip.X,box.bottom+gap)
		consider(tip.X,box.top-height-gap)
		consider(box.right+gap,box.bottom+gap)
		consider(box.left-width-gap,box.top-height-gap)
	end
	if not best and maxDistance == math.huge then
		for y = gap,viewport.Y-height-gap,height+gap do
			for x = gap,viewport.X-width-gap,width+gap do consider(x,y) end
		end
	end
	if best then table.insert(occupied,{left=best.X,right=best.X+width,top=best.Y,bottom=best.Y+height}) end
	return best
end

local function hit(button, point)
	if not button or not button.Visible or not button.Parent.Visible then return false end
	local offset = point-button.AbsolutePosition
	local size = button.AbsoluteSize
	return offset.X >= 0 and offset.Y >= 0 and offset.X < size.X and offset.Y < size.Y
end

if state and (state.version or 0) < RENDERER_VERSION and #payload.rows > 0 then
	local connections = state.connections
	if state.shutdown then
		state.shutdown()
	else
		run:UnbindFromRenderStep(key)
		state.gui.Parent:Destroy()
		for _, editable in state.art do editable:Destroy() end
		shared[key] = nil
	end
	state = nil
	payload.previousConnections = connections
end

if not state and #payload.rows > 0 then
	local gui = Instance.new("ScreenGui")
	gui.Name = "FoundryAgentCursors"
	gui.Archivable = false
	gui.IgnoreGuiInset = true
	gui.ZIndexBehavior = Enum.ZIndexBehavior.Sibling
	gui.DisplayOrder = 800
	local root = Instance.new("Folder")
	root.Name = "FoundryCursorLayer"
	root.Archivable = false
	root.Parent = core
	gui.Parent = root
	state = {gui=gui,version=RENDERER_VERSION,connections=payload.previousConnections or {},views={},slots={},art=loadArt()}
	shared[key] = state
	local input = game:GetService("UserInputService")
	local guiService = game:GetService("GuiService")
	local pressed
	local function pick(point)
		if hit(state.reportClose,point) then return state.reportClose end
		if state.report then return nil end
		for _, view in state.hitOrder or {} do
			if view.shared and hit(view.shared.button,point) then
				local index = 1+math.floor(((point.Y-view.shared.frame.AbsolutePosition.Y)/SCALE-28)/SHARED_ROW_HEIGHT)
				local item = view.shared.rows[index]
				if item and item.line.Visible then return state.views[item.row.id], hit(item.follow,point) end
				return view
			end
			if hit(view.compactButton,point) then return view end
			if hit(view.button,point) or hit(view.bubbleButton,point) then return view, hit(view.followButton,point) end
		end
	end
	local function pointerPosition(event)
		return Vector2.new(event.Position.X,event.Position.Y) - gui.AbsolutePosition
	end
	local beginInput = input.InputBegan:Connect(function(event)
		local kind = event.UserInputType
		local keyCode = event.KeyCode
		if kind == Enum.UserInputType.MouseButton2 or kind == Enum.UserInputType.MouseButton3
			or keyCode == Enum.KeyCode.W or keyCode == Enum.KeyCode.A or keyCode == Enum.KeyCode.S
			or keyCode == Enum.KeyCode.D or keyCode == Enum.KeyCode.Q or keyCode == Enum.KeyCode.E
			or keyCode == Enum.KeyCode.Up or keyCode == Enum.KeyCode.Down
			or keyCode == Enum.KeyCode.Left or keyCode == Enum.KeyCode.Right then
			state.follow = nil
		end
		if kind == Enum.UserInputType.MouseButton1 or kind == Enum.UserInputType.MouseButton2 then
			local point = pointerPosition(event)
			local target, follow = pick(point)
			pressed = {kind=kind,point=point,target=target,follow=follow}
		end
	end)
	local changeInput = input.InputChanged:Connect(function(event)
		if event.UserInputType == Enum.UserInputType.MouseWheel then state.follow = nil end
	end)
	local endInput = input.InputEnded:Connect(function(event)
		if not pressed or event.UserInputType ~= pressed.kind then return end
		local click = pressed
		pressed = nil
		local point = pointerPosition(event)
		local target, follow = pick(point)
		if click.kind ~= Enum.UserInputType.MouseButton1 or not click.target or (click.point-point).Magnitude > 6
			or target ~= click.target or follow ~= click.follow then return end
		if click.target == state.reportClose then
			closeReport(state)
		else
			task.defer(activate,click.target,click.follow)
		end
	end)
	local function shutdown()
		run:UnbindFromRenderStep(key)
		beginInput:Disconnect()
		changeInput:Disconnect()
		endInput:Disconnect()
		root:Destroy()
		for _, editable in state.art do editable:Destroy() end
		if shared[key] == state then shared[key] = nil end
	end
	state.shutdown = shutdown
	local function render(dt)
		local decay = if guiService.ReducedMotionEnabled then 0 else math.exp(-math.min(dt,1/30)*16)
		local now = os.clock()
		local camera = workspace.CurrentCamera
		if not run:IsEdit() then return end
		for id, connection in state.connections do
			if now - connection.time > 7 then state.connections[id] = nil end
		end
		local follow = state.follow
		if follow and camera then
			local frame = camera.CFrame
			if follow.target and follow.target:IsDescendantOf(workspace) then
				local focus = anchor(follow.target)
				if focus and (focus-frame.Position).Magnitude > 0.1 then
					camera.CFrame = frame:Lerp(CFrame.lookAt(frame.Position,focus),1-decay)
				end
			end
		end
		local keep = {}
		for _, connection in state.connections do
			if now - connection.time <= 7 then
				for _, row in connection.rows do table.insert(keep,row) end
			end
		end
		table.sort(keep,function(a,b) return a.id < b.id end)
		while #keep > 16 do table.remove(keep) end
		local groups = {}
		for _, row in keep do
			local keyPath = row.group
			local group = groups[keyPath] or {members={},ids={}}
			groups[keyPath] = group
			table.insert(group.members,row)
			group.ids[row.id] = true
		end
		for path, slots in state.slots do
			for id in slots do
				if not groups[path] or not groups[path].ids[id] then slots[id] = nil end
			end
			if not next(slots) then state.slots[path] = nil end
		end
		for _, row in keep do
			local slots = state.slots[row.group] or {}
			state.slots[row.group] = slots
			if slots[row.id] == nil then
				local used = {}
				for _, slot in slots do used[slot] = true end
				local slot = 0
				while used[slot] do slot += 1 end
				slots[row.id] = slot
			end
		end
		local visible = {}
		local occupied = {}
		local hitOrder = {}
		for _, row in keep do
			if not camera or not run:IsEdit() then break end
			visible[row.id] = true
			local view = state.views[row.id] or create(gui)
			state.views[row.id] = view
			table.insert(hitOrder,view)
			if view.row ~= row then
				local provider, color = providerStyle(row)
				view.border.Color = color
				local icon = state.art[provider]
				local pointer = state.art[provider.."Pointer"]
				if icon then
					view.mark.ImageContent = Content.fromObject(icon)
					view.compactMark.ImageContent = view.mark.ImageContent
				end
				if pointer then view.pointer.ImageContent = Content.fromObject(pointer) end
				view.name.Text = row.name
				view.stage.Text = activity(row)
					.. (#row.path > 0 and " · " .. row.path[#row.path] or "")
				local textService = game:GetService("TextService")
				local nameBounds = textService:GetTextSize(row.name,11,Enum.Font.GothamMedium,Vector2.new(160,21))
				view.badge.Size = UDim2.fromOffset(math.clamp(nameBounds.X+54,80,188),21)
				local bounds = textService:GetTextSize(view.stage.Text,12,Enum.Font.Code,Vector2.new(190,100))
				local bubbleHeight = math.clamp(bounds.Y+12,29,46)
				local bubbleWidth = if row.report then 182 else math.clamp(bounds.X+26,98,218)
				view.bubble.Size = UDim2.fromOffset(bubbleWidth,bubbleHeight)
				view.height = 45+bubbleHeight
				view.card:SetAttribute("TargetPath",table.concat(row.path,"."))
				view.card:SetAttribute("AgentId",row.id)
				updateOutlines(view,row,root)
				view.row = row
			end
			if not view.target or not view.target:IsDescendantOf(game) or view.path ~= row.group then
				view.target = resolve(row.path)
				view.path = row.group
			end
			view.followButton.Visible = view.target ~= nil and view.target:IsDescendantOf(workspace)
			view.followButton.Text = if state.follow == view then "◉" else "◎"
			local members = groups[row.group].members
			local grouped = #row.path > 0 and view.target ~= nil and #members > 1
			local host = grouped and members[1] == row
			view.badge.Visible = not grouped
			view.bubble.Visible = not grouped
			local sharedHeight
			if host then
				sharedHeight = showShared(view,members,state.art)
				for _, item in view.shared.rows do
					item.follow.Visible = view.followButton.Visible
					item.follow.Text = if state.follow and state.follow.row.id == item.row.id then "◉" else "◎"
				end
			elseif view.shared then
				view.shared.frame.Visible = false
			end
			local position
			if view.target and view.target:IsDescendantOf(workspace) then position=anchor(view.target) end
			local point, onScreen
			if position then point,onScreen = camera:WorldToViewportPoint(position) end
			if view.onScreen ~= onScreen then
				view.card:SetAttribute("Anchored",onScreen == true)
				view.onScreen = onScreen
			end
			local slots = state.slots[row.group]
			local slot = slots[row.id]
			local lastSlot = slot
			for _, memberSlot in slots do lastSlot = math.max(lastSlot,memberSlot) end
			local viewport = camera.ViewportSize
			local projected = point and point.Z > 0.05
			local edge = Vector2.new(8,12)
			local tip
			if point then
				local center = viewport/2
				local half = Vector2.new(math.max(1,center.X-19),math.max(1,center.Y-19))
				local direction = Vector2.new(point.X,point.Y)-center
				if not projected then
					direction = -direction
					if direction.Magnitude < 1 then direction = Vector2.new(0,1) end
				end
				local extent = math.max(math.abs(direction.X)/half.X,math.abs(direction.Y)/half.Y,0.001)
				tip = center + direction/(if projected then math.max(1,extent) else extent) - Vector2.new(8,8)
			else
				tip = edge
			end
			local rows = math.max(1,math.floor((viewport.Y-16)/SLOT_HEIGHT))
			local column = math.floor(slot/rows)
			local width = (if host then SHARED_WIDTH+11 else 11+math.max(view.badge.Size.X.Offset,view.bubble.Size.X.Offset))*SCALE
			local height = (sharedHeight or view.height)*SCALE
			if position then
				local step = if grouped then 15 else 8
				local span = lastSlot*step
				local start = math.clamp(tip.X-span/2,8,math.max(8,viewport.X-28-span))
				tip = Vector2.new(start+slot*step,tip.Y)
				view.pointer.Rotation = if onScreen then 0 else
					math.deg(math.atan2(tip.Y-viewport.Y/2,tip.X-viewport.X/2))+135
			else
				tip = if grouped then edge + Vector2.new(slot*15,0) else edge + Vector2.new(column*WIDTH*SCALE,(slot%rows)*SLOT_HEIGHT)
				view.pointer.Rotation = 0
			end
			-- Reserve space at the animated position so labels also avoid
			-- one another while moving between objects.
			if view.group ~= row.group or view.projected ~= projected then
				view.tipOffset = view.tip and view.tip-tip or Vector2.zero
			end
			view.tipOffset *= decay
			view.group,view.projected = row.group,projected
			view.tip = tip+view.tipOffset
			local placement, compact
			if host or not grouped then
				placement = placeLabel(view.tip,width,height,viewport,occupied,72)
				if not placement then
					local caption = if host then row.name .. " +" .. (#members-1) else row.name
					if view.compactName.Text ~= caption then
						local bounds = game:GetService("TextService"):GetTextSize(caption,11,Enum.Font.GothamMedium,Vector2.new(125,22))
						view.compactName.Text = caption
						view.compact.Size = UDim2.fromOffset(math.clamp(bounds.X+32,65,150),22)
					end
					width,height = (view.compact.Size.X.Offset+11)*SCALE,40*SCALE
					placement = placeLabel(view.tip,width,height,viewport,occupied,math.huge)
					compact = placement ~= nil
				end
			end
			view.compact.Visible = compact == true
			view.badge.Visible = placement ~= nil and not compact and not grouped
			view.bubble.Visible = view.badge.Visible
			if view.shared then view.shared.frame.Visible = host and placement ~= nil and not compact end
			view.position = placement or view.tip
			view.card.Position = UDim2.fromOffset(view.position.X,view.position.Y)
			local offset = (view.tip-view.position)/SCALE
			view.pointer.Position = UDim2.fromOffset(offset.X,offset.Y)
			view.tether.Visible = false
			if placement then
				local topLeft = Vector2.new(11,if host and not compact then 19 else 18)
				local endPoint = Vector2.new(math.clamp(offset.X,topLeft.X,width/SCALE),
					math.clamp(offset.Y,topLeft.Y,height/SCALE))
				local link = endPoint-offset
				if link.Magnitude > 24 then
					local midpoint = (endPoint+offset)/2
					view.tether.Position = UDim2.fromOffset(midpoint.X,midpoint.Y)
					view.tether.Size = UDim2.fromOffset(link.Magnitude,1)
					view.tether.Rotation = math.deg(math.atan2(link.Y,link.X))
					view.tether.Visible = true
				end
			end
		end
		state.hitOrder = hitOrder
		for id, view in state.views do
			if not visible[id] then
				if state.follow == view then state.follow = nil end
				for _, outline in view.outlines or {} do outline:Destroy() end
				view.card:Destroy()
				state.views[id] = nil
			end
		end
		if not next(state.connections) and not state.report then
			shutdown()
		end
	end
	run:BindToRenderStep(key,Enum.RenderPriority.Camera.Value+1,render)
end
if state then
	if not state.update then
		state.update = function(update)
			if update.hidden then
				state.follow = nil
				closeReport(state)
			end
			for _, row in update.rows do row.group = http:JSONEncode(row.path) end
			state.connections[update.connection] = if #update.rows > 0 then {rows=update.rows,time=os.clock()} else nil
		end
	end
	state.update(payload)
end
return true
