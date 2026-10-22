# UniFi mirror reference

Mechanics of port mirroring on UniFi switches, plus the targeted-tap procedure. The decision guide
(where to point the mirror) is in [planning.md](planning.md). Statements below are observed on the
author's hardware unless marked otherwise; they are not vendor-documented.

## How mirroring works

- A switch copies frames from one **source port** to a **destination port**. Kokabiel's capture
  interface is cabled to the destination.
- A USW Flex 2.5G reports `switch_caps.max_mirror_sessions = 1`: **one session per switch, with one
  source port per session.**
- A session mirrors **both directions** of the source. A busy source (for example a 2.5G AP uplink
  at full load in both directions) can exceed the destination's line rate. Watch the `rx` and
  `pcap_dropped` lines in `status.sh`.
- Mirror an AP uplink, or the uplink of the segment you care about. Do not overlap sources
  (double counting).
- Client isolation is enforced inside the AP. Packets it blocks never reach any wire, so the mirror
  never sees them (Wi-Fi to Wi-Fi, and, as observed in the author's experience and not vendor-documented,
  Wi-Fi to wired on the same subnet).

## Reading `switch_caps` through the API

With the legacy API, authenticated as you normally do for your controller:

```sh
curl -sk -H "X-API-KEY: $KEY" \
  "https://<CONTROLLER>/proxy/network/api/s/default/stat/device" |
  jq '.data[] | select(.type=="usw") | {name, model, switch_caps}'
```

Fields to read:

| Field | Meaning |
|---|---|
| `switch_caps.max_mirror_sessions` | How many mirror sessions the switch supports. `0` or absent: no mirroring. Flex 2.5G reports `1` |
| `switch_caps.mirror_groups` | Port ranges that can mirror to each other |
| `port_table`, `port_overrides` | Current port state and the mirror configuration applied |
| `uplink` | This device's uplink. `uplink_remote_port` is the port on the **parent** device |

`/stat/sta` lists clients with `sw_mac` and `sw_port`, which locates a wired client.

## Mirror groups (observed)

On the USW Flex 2.5G 8, `mirror_groups` shows ports 1-4 and 5-10. **Source and destination appear
to need to be in the same group.** This is observed or likely, not vendor-documented. If a session
does not pass traffic, check the groups first. Check your own model; other switches differ.

## Targeted tap (one wired device)

There is only one session, so tapping a device **pauses the survey baseline** on that switch.
Time-box it.

1. Note the current configuration: source port and destination port of the mirror.
2. Find the device's switch port (`stat/sta` `sw_mac`/`sw_port`, or the UI client page).
3. Confirm that port is in the destination's mirror group.
4. In Port Manager, re-point the mirror's source to that port. The probe needs no changes; it exports
   whatever it receives.
5. Investigate, filtering by the device's IP at query time. For packets, either run on the probe
   `kokabiel-capture <ip-or-mac> [seconds]` (writes a pcap to the SD card if mounted, else to RAM, and
   prints a JSON summary), or from the operator machine
   `ansible-playbook ansible/capture.yml -l <probe> -e target=<ip-or-mac> -e seconds=60`
   (fetches the pcap locally).
6. **Restore the original source port.** Verify with `status.sh` that the capture counters climb
   again and flows return to the baseline pattern.

For a Wi-Fi device there is no per-client port. Stay in survey mode, filter by the device's IP in the
collector, and use the capture tools above if you need full packets.
