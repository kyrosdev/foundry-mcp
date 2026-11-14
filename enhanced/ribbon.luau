local toolPlugin = PluginManager():CreatePlugin()
local ok, result = pcall(function()
	if requested then
		toolPlugin:SelectRibbonTool(Enum.RibbonTool[requested], UDim2.new())
	end
	return toolPlugin:GetSelectedRibbonTool().Name
end)
toolPlugin:Destroy()
if not ok then error(result) end
return result
