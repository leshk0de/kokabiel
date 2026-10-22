# Where should my probe go?

Kokabiel watches **one mirror port** on one switch. Where you point it decides what you can see.
This guide is for readers who do not yet know their own network well. It assumes UniFi; the last
section covers other gear. For the mechanics of the mirror itself, see
[unifi-mirror.md](unifi-mirror.md).

## Visibility model

A generic reference topology (yours will differ):

```
                 Internet
                    |
               +----+----+   sampled IPFIX (min 1:2) ------------+
               | Gateway |                                       |
               +----+----+                                       |
                    | (A) gateway uplink                         |
          +---------+----------+        mirror session           |
          |    Core switch     |--(dest)--> probe capture port   |
          +--+----------+------+            probe mgmt --IPFIX---+
   (B) AP    |          | (C) inter-switch                       v
   uplink(s) |          |     uplink                      Flow collector
        +----+--+   +---+----------+
        |  APs  |   | Server switch|-- servers
        +---+---+   +--------------+
        Wi-Fi clients (some SSIDs isolated)
```

| Vantage point | Sees | Blind to |
|---|---|---|
| Gateway IPFIX (usually already on) | Anything crossing the gateway: internet, inter-VLAN | Everything on the same subnet; much of the short flows (sampling) |
| (B) AP uplink mirror (Kokabiel default) | Every frame between that AP's clients and anything else, unsampled, including broadcast/DHCP/ARP | Other APs' clients; client-to-client on the same AP; anything blocked inside the AP by client isolation |
| (A) Gateway uplink mirror | All internet and inter-VLAN traffic, unsampled | Same-subnet traffic |
| (C) Inter-switch uplink mirror | Clients to servers, servers to internet | Server-to-server on the server switch |
| Host-level eBPF on servers (separate project) | Each server's own connections and DNS | Anything not touching that server |
| Second probe on the server switch | Server-to-server | n/a |

**Why an AP uplink is the default.** Wi-Fi clients are usually the largest and least controlled
population, and on a flat subnet their traffic to servers never reaches the gateway, so it is
recorded nowhere; their internet traffic is only sampled. The USW Flex 2.5G was observed to allow one
mirror session with one source port (check `switch_caps` on other models), so mirror the uplink that carries the most clients (mesh APs backhauling through a
wired AP are covered by it). (A) was rejected because the gateway already approximates it, (C) because
it does not see Wi-Fi-to-internet, and multiple sources because the observed switch does not support them
(and overlapping sources would double-count anyway).

## 1. Discovery checklist (UniFi)

Collect these before choosing a placement. Most are in the UniFi UI; the API paths below are the
legacy controller API (`/proxy/network/api/s/default/...`) and the Integration API
(`/proxy/network/integration/v1/sites`, authenticated with an `X-API-KEY` header).

| What | Where to look | Why it matters |
|---|---|---|
| Topology: which switch and port each AP, switch and the gateway hangs off | UI: Devices. API: `stat/device` (`port_table`, `uplink`) and `stat/sta` (`sw_mac`, `sw_port`) | Tells you which uplink carries which clients |
| Networks/VLANs and which SSIDs map to them | UI: Settings, Networks and WiFi | Decides whether clients and servers share a subnet (and so skip the gateway) |
| Client isolation | UI: per-SSID and per-network settings | Isolated traffic is dropped inside the AP and never reaches a wire |
| Gateway NetFlow settings and sampling | UI: gateway integrations/NetFlow | Your existing baseline. Observed minimum sampling is 1:2 (1:1 is not offered) |
| Per-switch mirror capabilities | API: `stat/device`, `switch_caps.max_mirror_sessions`, `switch_caps.mirror_groups` | Some small switches and gateway-integrated ports cannot mirror at all |
| Link speeds | UI: port table, or `port_table[].speed` | A 10G source cannot be fully captured on a 2.5G probe |
| Where the flow collector lives | Your own notes | The probe must reach it over the management port |

**API gotcha:** a device's `uplink.uplink_remote_port` is the port on the **parent** device, not on
the device you queried. Read it together with the parent's `port_table`.

## 2. Placement options

| Option | Sees | Misses | Benefits | Drawbacks |
|---|---|---|---|---|
| (a) Gateway uplink port | Everything north-south and routed inter-VLAN, unsampled | Same-subnet traffic | Complete internet view | Duplicates the gateway's own sampled export |
| (b) One AP uplink | That AP's Wi-Fi clients (and mesh children) to everything: internet, servers, other APs | Other APs, client-to-client on the same AP, anything dropped by client isolation | Covers the least controlled population; sees broadcast, DHCP and ARP | One AP only |
| (c) Inter-switch uplink | Traffic between two segments, e.g. clients to servers | Traffic that stays on one switch | Good for client-to-server visibility | Often 10G, so it can oversubscribe a 2.5G probe |
| (d) A single device's port | Everything that one device sends and receives | All else | Targeted investigation | Pauses the switch's only mirror session |
| (e) Consolidate APs behind one port (small PoE switch) | All Wi-Fi via option (b) | As (b) | One mirror covers every AP | Costs hardware and a cabling change |
| (f) One probe per switch | Same-switch east-west traffic | Other switches | The only way to see it | A probe per switch |

### Which one first?

| My main worry is... | Start with |
|---|---|
| Unknown or untrusted Wi-Fi clients (laptops, phones, IoT) | (b) the busiest AP uplink |
| What is leaving for the internet, unsampled | (a) |
| Clients reaching servers | (c), if the link speed is within the probe's means; otherwise (b) |
| One suspicious wired device | (d), time-boxed |
| Server-to-server traffic | (f) |
| All Wi-Fi, not just one AP | (e), then (b) |

## 3. Rules of thumb

- One mirror session and one source port per switch was observed on the USW Flex 2.5G; check
  `switch_caps` on other models. Do not plan on multi-source.
- The destination port must be in the same mirror group as the source (observed, see
  [unifi-mirror.md](unifi-mirror.md)).
- A mirror carries both directions, so a busy source can exceed the destination's line rate.
- Avoid overlapping sources; the same packet would be counted twice.
- Keep the probe's own management and export path off the mirrored link, or it observes itself.
- Client isolation and same-AP traffic are invisible to every wired vantage point (isolation is
  enforced in the AP; Wi-Fi to wired blocking on the same subnet is as observed by the author, not
  vendor-documented).
- Flat networks hide lateral movement from the gateway, because same-subnet traffic never reaches it.
- The gateway's NetFlow is worth enabling as a sampled baseline, but first check your collector for
  per-flow live lookups (see the feedback-loop warning in [collector.md](collector.md)).

### Worked example (anonymised)

A home with two 2.5G switches, three APs and servers on the second switch. One AP is wired to
Switch A and two mesh off it; Switch B hosts the servers. Wi-Fi to server traffic is the gap: it
stays on the flat subnet and the gateway never sees it. The busiest wired AP uplink on Switch A
(option b) covers its mesh children too. The mirror destination is another port in the same mirror
group on Switch A, cabled to the probe's capture port. The other APs stay covered only by the
gateway's sampled export.

## 4. Configuring the mirror in UniFi

The exact labels vary between Network application versions.

1. Open **Port Manager** for the switch and pick the port the probe's capture interface is cabled to
   (the destination). Choose a port in the same mirror group as the source.
2. Set that port's operation to **Mirroring** and select the **source port** (for example the AP
   uplink). Apply.
3. Cable the destination port to the probe's capture NIC. Do not cable the probe's management
   interface to the mirrored link.
4. Confirm: run `status.sh` on the probe and check that the `rx` line's packet counter climbs on
   the capture port, with low `dropped`/`missed` values. Then check that flows arrive at your
   collector (see [collector.md](collector.md)).

## 5. Non-UniFi, high level

- **Other managed switches with SPAN:** the same ideas apply. Check the session and source limits,
  and whether VLAN tags survive on the mirror output.
- **Passive network TAPs:** fail-safe and lossless, but a full-duplex TAP has two outputs (one per
  direction), which needs two capture ports or an aggregating TAP. Multi-gig TAPs cost more.
- **OpenWrt/Linux router:** run softflowd on the router itself; no probe needed.
- **pfSense/OPNsense:** built-in softflowd/NetFlow export.
- **Hypervisor virtual switches** (Proxmox, ESXi port mirroring) cover VM traffic.
- **When a probe does not help:** traffic encrypted inside tunnels, or confined to a single host.
