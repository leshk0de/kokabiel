-- Kokabiel collector for prometheus-node-exporter-lua.
-- Installed as /usr/lib/lua/prometheus-collectors/kokabiel.lua. The exporter
-- provides the globals metric(name, type, labels, value), get_contents() and
-- space_split(); metric() without a value returns a function(labels, value).
--
-- Test hooks (environment): KOKABIEL_SYSFS (/sys), KOKABIEL_PROC (/proc),
-- KOKABIEL_RUN (/var/run), KOKABIEL_SOFTFLOWCTL (/usr/sbin/softflowctl),
-- KOKABIEL_CTL (softflowd control socket), KOKABIEL_OVERLAY (/overlay).

local function env(name, default)
  local v = os.getenv(name)
  if v == nil or v == "" then return default end
  return v
end

local SYSFS = env("KOKABIEL_SYSFS", "/sys")
local PROC = env("KOKABIEL_PROC", "/proc")
local RUN = env("KOKABIEL_RUN", "/var/run")
local SOFTFLOWCTL = env("KOKABIEL_SOFTFLOWCTL", "/usr/sbin/softflowctl")
local CTL = env("KOKABIEL_CTL", "/var/run/kokabiel-softflowd.ctl")
local OVERLAY = env("KOKABIEL_OVERLAY", "/overlay")

local function read_trim(path)
  local f = io.open(path, "r")
  if not f then return nil end
  local s = f:read("*a")
  f:close()
  return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end

local function read_num(path)
  return tonumber(read_trim(path) or "")
end

-- Shell-quote a single word.
local function q(s)
  return "'" .. tostring(s):gsub("'", "'\\''") .. "'"
end

-- Run a command; returns its stdout and exit code (portable across Lua 5.1-5.4).
local function run(cmd)
  local p = io.popen("(" .. cmd .. ") 2>/dev/null; echo \"__rc=$?\"")
  if not p then return "", 1 end
  local out = p:read("*a") or ""
  p:close()
  local rc = tonumber(out:match("__rc=(%d+)%s*$")) or 1
  out = out:gsub("__rc=%d+%s*$", "")
  return out, rc
end

local function uci(opt)
  local out, rc = run("uci -q get kokabiel.main." .. opt)
  if rc ~= 0 then return nil end
  out = out:gsub("%s+$", "")
  if out == "" then return nil end
  return out
end

local function lower(s) return s and s:lower() or nil end

-- Total and free bytes of the filesystem holding PATH (df -Pk), or nil.
local function df(path)
  local out, rc = run("df -Pk " .. q(path))
  if rc ~= 0 then return nil end
  local line = out:match("\n([^\n]+)\n?$")
  if not line then return nil end
  local f = space_split(line)
  local size, avail = tonumber(f[2]), tonumber(f[4])
  if not size or not avail then return nil end
  return size * 1024, avail * 1024
end

local function is_mounted(dir)
  for line in get_contents(PROC .. "/mounts"):gmatch("[^\n]+") do
    if space_split(line)[2] == dir then return true end
  end
  return false
end

local function capture_interface()
  local cif = uci("capture_if")
  if not cif then return end
  local base = SYSFS .. "/class/net/" .. cif
  local carrier = read_num(base .. "/carrier")
  local flags = read_num(base .. "/flags")
  metric("kokabiel_capture_link_up", "gauge", nil, (carrier == 1) and 1 or 0)
  -- IFF_PROMISC is 0x100; Lua 5.1 has no bit operators.
  local promisc = 0
  if flags and math.floor(flags / 256) % 2 == 1 then promisc = 1 end
  metric("kokabiel_capture_promisc", "gauge", nil, promisc)
  metric("kokabiel_capture_carrier_changes_total", "counter", nil,
    read_num(base .. "/carrier_changes"))
  metric("kokabiel_capture_rx_packets_total", "counter", nil,
    read_num(base .. "/statistics/rx_packets"))
  metric("kokabiel_capture_rx_bytes_total", "counter", nil,
    read_num(base .. "/statistics/rx_bytes"))
  metric("kokabiel_capture_rx_dropped_total", "counter", nil,
    read_num(base .. "/statistics/rx_dropped"))
  metric("kokabiel_capture_rx_missed_errors_total", "counter", nil,
    read_num(base .. "/statistics/rx_missed_errors"))

  local ok = 1
  for _, pair in ipairs({ { "capture_if", "capture_mac" }, { "mgmt_if", "mgmt_mac" } }) do
    local ifn, want = uci(pair[1]), lower(uci(pair[2]))
    if ifn and want then
      if lower(read_trim(SYSFS .. "/class/net/" .. ifn .. "/address")) ~= want then ok = 0 end
    end
  end
  metric("kokabiel_interface_guard_ok", "gauge", nil, ok)
end

local function softflowd()
  local out, rc = run(q(SOFTFLOWCTL) .. " -c " .. q(CTL) .. " statistics")
  local active = tonumber(out:match("Number of active flows:%s*(%d+)"))
  local up = (rc == 0 and active ~= nil) and 1 or 0
  metric("kokabiel_softflowd_up", "gauge", nil, up)
  if up == 0 then return end
  metric("kokabiel_softflowd_active_flows", "gauge", nil, active)
  metric("kokabiel_softflowd_flows_exported_total", "counter", nil,
    tonumber(out:match("Flows exported:%s*(%d+)")))
  metric("kokabiel_softflowd_packets_processed_total", "counter", nil,
    tonumber(out:match("Packets processed:%s*(%d+)")))
  metric("kokabiel_softflowd_packets_received_total", "counter", nil,
    tonumber(out:match("Packets received by libpcap:%s*(%d+)")))
  metric("kokabiel_softflowd_pcap_dropped_total", "counter", nil,
    tonumber(out:match("Packets dropped by libpcap:%s*(%d+)")))
end

local function storage()
  local size_m = metric("kokabiel_storage_size_bytes", "gauge")
  local free_m = metric("kokabiel_storage_free_bytes", "gauge")
  local size, free = df(OVERLAY)
  if size then
    size_m({ mount = "overlay" }, size)
    free_m({ mount = "overlay" }, free)
  end
  local dir = uci("capture_dir") or "/mnt/kokabiel-cap"
  local present = 0
  if is_mounted(dir) then
    size, free = df(dir)
    if size then
      present = 1
      size_m({ mount = "sd" }, size)
      free_m({ mount = "sd" }, free)
    end
  end
  metric("kokabiel_sd_present", "gauge", nil, present)
end

local function temperature()
  local m = metric("kokabiel_temperature_celsius", "gauge")
  local out = run("ls -1 " .. q(SYSFS .. "/class/thermal"))
  for zone in out:gmatch("thermal_zone%d+") do
    local base = SYSFS .. "/class/thermal/" .. zone
    local t, ztype = read_num(base .. "/temp"), read_trim(base .. "/type")
    if t and ztype and ztype ~= "" then
      m({ zone = (ztype:gsub('[^%w%._%-]', "_")) }, t / 1000)
    end
  end
end

local function capture_job()
  local state = {}
  for line in get_contents(RUN .. "/kokabiel-capture.state"):gmatch("[^\n]+") do
    local k, v = line:match("^(%w+)=(.*)$")
    if k then state[k] = v end
  end
  local running = 0
  local pid = state.pid
  if pid and pid:match("^%d+$") and state.target and state.dir and state.started then
    -- fopen() on a directory succeeds on Linux, which is enough to test /proc/PID.
    local d = io.open(PROC .. "/" .. pid, "r")
    if d then
      d:close()
      running = 1
    end
  end
  metric("kokabiel_capture_running", "gauge", nil, running)
  if running == 0 then
    metric("kokabiel_capture_bytes", "gauge", nil, 0)
    return
  end
  -- Sum this job's files: <probe>-<target with : and . as ->-<UTC stamp>.pcap[N]
  local safe = state.target:gsub("[:%.]", "-")
  local started = os.date("!%Y%m%dT%H%M%SZ", tonumber(state.started) or 0)
  local total = 0
  local out = run("ls -1 " .. q(state.dir))
  for name in out:gmatch("[^\n]+") do
    local stamp = name:match("%-" .. safe:gsub("%-", "%%-") .. "%-(%d%d%d%d%d%d%d%dT%d%d%d%d%d%dZ)%.pcap%d*$")
    if stamp and stamp >= started then
      local f = io.open(state.dir .. "/" .. name, "rb")
      if f then
        total = total + (f:seek("end") or 0)
        f:close()
      end
    end
  end
  metric("kokabiel_capture_bytes", "gauge", nil, total)
  metric("kokabiel_capture_info", "gauge", { target = (state.target:gsub('[^%w:%.]', "_")) }, 1)
end

-- TCP listeners on non-loopback addresses other than 22 and 9100.
local function listeners()
  local out = run("netstat -tln")
  local n = 0
  for line in out:gmatch("[^\n]+") do
    local f = space_split(line)
    if f[1] and f[1]:match("^tcp") and f[4] then
      local host, port = f[4]:match("^(.*):(%d+)$")
      if host and not host:match("^127%.") and host ~= "::1" and host ~= "[::1]"
          and port ~= "22" and port ~= "9100" then
        n = n + 1
      end
    end
  end
  metric("kokabiel_unexpected_listeners", "gauge", nil, n)
end

local function scrape()
  capture_interface()
  softflowd()
  storage()
  temperature()
  capture_job()
  listeners()
end

return { scrape = scrape }
