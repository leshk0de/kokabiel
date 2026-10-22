-- Test harness for the Kokabiel collector: provides the globals that
-- /usr/bin/prometheus-node-exporter-lua defines (metric, get_contents,
-- space_split), runs the collector's scrape(), and prints "name{labels} value".
-- Usage: lua harness.lua /path/to/kokabiel.lua

function space_split(s)
  local elements = {}
  for element in s:gmatch("%S+") do
    table.insert(elements, element)
  end
  return elements
end

function get_contents(filename)
  local f = io.open(filename, "rb")
  local contents = ""
  if f then
    contents = f:read "*a"
    f:close()
  end
  return contents
end

local function emit(name, labels, value)
  if type(value) == "nil" then
    return
  end
  local parts = {}
  if labels then
    for k, v in pairs(labels) do
      parts[#parts + 1] = k .. '="' .. v .. '"'
    end
    table.sort(parts)
  end
  local l = (#parts > 0) and ("{" .. table.concat(parts, ",") .. "}") or ""
  io.write(name, l, " ", tostring(value), "\n")
end

function metric(name, mtype, labels, value)
  if value then
    emit(name, labels, value)
  end
  return function(l, v)
    emit(name, l, v)
  end
end

local collector = dofile(arg[1])
local ok, err = pcall(collector.scrape)
if not ok then
  io.stderr:write("scrape error: " .. tostring(err) .. "\n")
  os.exit(1)
end
