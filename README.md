# Kokabiel

A small, passive NetFlow/IPFIX probe for a UniFi-style home or lab network, built on a NanoPi R5C
running FriendlyWrt (OpenWrt). A switch mirrors one port to the probe's capture NIC, `softflowd`
turns every packet (unsampled) into IPFIX records, and the probe exports them to a collector you
already run. It never sits inline, never serves anything to the network, and if it dies nothing
else breaks.

## Why

A gateway's flow export leaves two holes:

1. **It is sampled.** UniFi gateways export IPFIX with at least 1:2 random packet sampling (1:1 is
   not offered). Short flows such as a single DNS query, a SYN probe or a one-packet beacon have a
   50% or worse chance of never being recorded, and those are exactly the flows a SOC cares about.
2. **It only sees traffic that crosses the gateway.** On a flat network, Wi-Fi clients talking to
   wired servers never touch the gateway, so lateral movement is invisible.

Kokabiel closes both for the traffic on the mirrored port (typically an access point's uplink,
which covers its Wi-Fi clients). See [docs/planning.md](docs/planning.md) for where to put the
probe on your network.

## Architecture

```
                 Internet
                    |
               +----+----+   sampled IPFIX (>=1:2) -------------+
               | Gateway |                                       |
               +----+----+                                       |
                    | (A) gateway uplink                         |
          +---------+----------+        mirror session           |
          |    Core switch     |--(dest)--> Kokabiel eth1        |
          +--+----------+------+            Kokabiel eth0 --IPFIX-----+
   (B) AP    |          | (C) inter-switch                       v
   uplink(s) |          |     uplink                     Collector -> index -> SOC dashboard
        +----+--+   +---+----------+
        |  APs  |   | Server switch|-- servers
        +---+---+   +--------------+
         Wi-Fi clients
```

| Vantage point | Sees | Blind to |
|---|---|---|
| Gateway IPFIX (existing) | anything crossing the gateway: internet, inter-VLAN | same-subnet traffic; at least 50% of short flows (sampling) |
| **(B) AP uplink mirror: Kokabiel** | every frame between that AP's clients and anything else, unsampled, including broadcast/DHCP/ARP | other APs' clients; client-to-client on the same AP; anything blocked inside the AP by client isolation |
| (A) gateway uplink mirror | all internet and inter-VLAN traffic, unsampled | same-subnet traffic |
| (C) inter-switch uplink mirror | clients to servers, servers to internet | server-to-server on one switch |

The switch allows one mirror session with one source port, so pick the uplink that carries the most
clients. Details: [docs/planning.md](docs/planning.md), [docs/unifi-mirror.md](docs/unifi-mirror.md).

## Hardware

NanoPi R5C (RK3568B2, 4 GB RAM, eMMC, passive metal case) rather than a Raspberry Pi 5:

- **Two native 2.5GbE ports** versus the Pi 5's single 1GbE. The probe needs a capture port and a
  separate management/export port, and the mirrored links are 2.5G.
- Fanless case and eMMC instead of an SD card, which suits an always-on appliance.
- Runs FriendlyWrt (OpenWrt): small footprint, and `uci` gives declarative, persistent config.
- Trade-off: weaker cores (Cortex-A55 versus A76) and a smaller ecosystem. Fine for `softflowd` at
  home traffic rates; heavy DPI (Zeek/Suricata) on this box is unproven.

Port roles: `eth0` is management (DHCP client, SSH, metrics, flow export); `eth1` is the passive
capture port (no address, promiscuous). An optional microSD card holds on-demand packet captures.

## Quick start

You need on your workstation: Docker (for the shim build), `uv`, Ansible, and an SSH key at
`~/.ssh/id_ed25519.pub` (a single public-key line); set `ssh_pubkey` only if your key
lives elsewhere.

> **Required before deploying: `make shim`.** softflowd 1.1.0 on OpenWrt 25.12 processes **zero
> packets**: it never enables libpcap's immediate mode, so the TPACKET_V3 block timeout is infinite
> and no packet is ever delivered. Kokabiel ships a tiny `LD_PRELOAD` shim
> ([scripts/files/pcap_immediate.c](scripts/files/pcap_immediate.c)) that is built with `make shim`
> (Docker, aarch64 OpenWrt image, needs arm64 emulation via qemu/binfmt on x86 hosts) and loaded
> only into softflowd via the procd environment. The Ansible play **fails early** if
> `build/pcap_immediate.so` is missing. Upstream fixed this in irino/softflowd commit `f5c0f99103`
> (PR #71), not yet released; once a release carries it the shim can go.

1. **Bootstrap the probe from its console** ([docs/bootstrap.md](docs/bootstrap.md)): set a
   temporary root password and open SSH on the management port. **Do not cable eth1 yet.** The
   stock image runs a DHCP server on it that would hijack your LAN.
2. **Create your inventory.** Copy the whole directory (`group_vars/kokabiel.yml` carries the
   Python interpreter setting) and edit it:
   ```sh
   cp -r ansible/inventory.example ansible/inventory
   $EDITOR ansible/inventory/hosts.yml ansible/inventory/host_vars/probe1.yml
   ```
   Probes live in the group `kokabiel`; `collector_host` is the only required variable.
3. **Install tooling and build the shim:**
   ```sh
   make tools   # shellcheck, sshpass, ansible-core, ansible-lint (uses Homebrew: macOS-oriented; elsewhere install them yourself)
   make shim    # build/pcap_immediate.so
   ```
4. **First run, before cabling eth1.** Host key checking is on, so SSH to the probe once
   (`ssh root@<probe>`, accept the key) or run `ssh-keyscan <probe> >> ~/.ssh/known_hosts` first. Use the temporary password; skip the status check, which
   needs the capture link up. `--ask-pass` needs `sshpass` on your workstation (`make tools`
   installs it via `brew install hudochenkov/sshpass/sshpass`; on Linux use your package manager):
   ```sh
   cd ansible
   ansible-playbook site.yml --ask-pass --skip-tags status
   ```
   The play installs Python and packages, configures the probe, installs your SSH key, sets a
   generated root passphrase, offers to prepare the SD card, and ends by printing a
   ready-to-paste Prometheus scrape snippet.
5. **Cable eth1** to the mirror destination port and configure the mirror
   ([docs/unifi-mirror.md](docs/unifi-mirror.md)).
6. **Run the full play** so the status check runs and gates the result:
   ```sh
   ansible-playbook site.yml
   ```

Package manager notes: FriendlyWrt 25.12 uses `apk` (24.10 uses `opkg`); the scripts detect it.
**Never install `kmod-*` packages**: FriendlyWrt ships kernel 6.1 while the upstream feeds carry
kmods for 6.12, so they will not load (the scripts refuse to install them).

### What the play does to the appliance

- eth1 is detached from `br-lan`, gets no address (IPv6 off), no DHCP/RA, a DROP-only firewall zone (`kk_capture`),
  promiscuous mode and offloads off (GRO/GSO/TSO/LRO, rx-VLAN) re-applied on every link-up.
- An interface-role guard (by PCI device path, recorded at first setup) refuses to treat the wrong
  NIC as the capture port and keeps softflowd stopped until it passes. MACs cannot serve here:
  FriendlyWrt pins them per interface name. After a deliberate hardware change, rerun
  `setup.sh --rerecord-nics`.
- `softflowd` runs under procd with respawn, exporting IPFIX (v10, or v9 fallback) with full
  5-tuple tracking and a 65536-flow table.
- `dnsmasq` is restricted to loopback; the web UI (LuCI) is off. With `enable_luci: true` it binds
  to `127.0.0.1` only; reach it with `ssh -L 8080:127.0.0.1:80 root@<probe>`.
- A long list of FriendlyWrt router services is stopped and disabled (ttyd, vsftpd, samba4,
  minidlna, miniupnpd, adblock, smartdns, sqm, odhcpd, lldpd, umdns and more; the default list is
  `KOKABIEL_DISABLE_SERVICES` in [scripts/lib/common.sh](scripts/lib/common.sh)). Only SSH (22)
  and metrics (9100) may listen; `kokabiel-status` checks this.

### Ansible variables

Per-probe variables go in `ansible/inventory/host_vars/<probe>.yml` or `group_vars/`. Defaults come
from the role `defaults/`.

| Variable | Default | Meaning |
|---|---|---|
| `collector_host` | (required) | IPFIX/NetFlow collector address |
| `collector_port` | `2055` | collector UDP port |
| `export_version` | `10` | `10` = IPFIX, `9` = NetFlow v9 |
| `probe_name` | inventory hostname | hostname; distinguishes probes in dashboards |
| `capture_if` / `mgmt_if` | `eth1` / `eth0` | passive capture and management NICs |
| `track_level` | `ether` | softflowd tracking: `ether` (full + source/destination MAC and VLAN), `full`, `port`, `proto`, `ip`, `vlan`; anything else is rejected |
| `timezone` | `UTC0` | POSIX TZ string such as `PST8PDT,M3.2.0,M11.1.0`; Olson names like `America/Los_Angeles` are rejected (FriendlyWrt has no zoneinfo) |
| `capture_dir` | `/mnt/kokabiel-cap` | SD card mount point |
| `capture_sd_max_mb` | `4096` | cap on SD capture storage |
| `capture_ram_max_mb` | `100` | cap on RAM capture (allowed 100-500) |
| `enable_luci` | `0` | `1` = LuCI on 127.0.0.1 only |
| `syslog_host` | unset | optional remote syslog target |
| `metrics_allowed_src` | management subnet | CIDR allowed to scrape TCP 9100 |
| `ssh_pubkey` | `~/.ssh/id_ed25519.pub` | public key file or text to install (a single public-key line); empty installs no key |
| `disable_password_login` | `false` | turn off password SSH, only after key login is verified |
| `show_secrets` | `true` | print the root passphrase at the end (`-e show_secrets=false` for unattended runs) |
| `rotate_root_passphrase` | prompted | `-e rotate_root_passphrase=true` rotates without a prompt |
| `erase_new_sd_card` | prompted | `-e erase_new_sd_card=true` allows unattended SD formatting |

### Secrets and prompts

- A root passphrase of the form `word-word-word-NNNN` (EFF large wordlist, CSPRNG) is generated per
  probe and saved to `ansible/logs/<probe>-root-passphrase.txt` (mode 0600, `logs/` mode 0700,
  gitignored). It is shown on the terminal only, never written to `ansible.log`. After the first
  run the play asks per probe whether to rotate it (default no).
- The configure play runs `serial: 1`, so prompts appear once per probe, with that probe's details.
- **SD card:** a card that is not Kokabiel's (ext4, label `KOKABIEL-CAP`, marker file) is shown to
  you and the play asks `Erase SD card ... Type ERASE to confirm:`. Only the exact word `ERASE`
  formats it; anything else leaves it alone and captures fall back to RAM. Unattended runs never
  erase unless you pass `-e erase_new_sd_card=true`. The R5C boots from a bootable microSD before
  eMMC, so never insert a leftover OS card; erasing also removes that risk.

## Using it without Ansible

Everything on the probe is POSIX `sh`. Copy `scripts/` and your `build/pcap_immediate.so` to the
probe, write a `site.env` from [site.env.example](site.env.example), and run
`scripts/setup.sh --env site.env` as root (add `--rerecord-nics` only after a deliberate NIC or
`CAPTURE_IF`/`MGMT_IF` change). Format an SD card with
`kokabiel-format-sd --device /dev/mmcblkN` (same safety checks and `ERASE` confirmation). You then
handle SSH keys, passphrases and Python yourself.

## On-probe commands

| Command | What it does |
|---|---|
| `kokabiel-status` | read-only health check printing OK/FAIL/INFO lines, exit 1 on any FAIL. Checks the capture link, promiscuous mode, no address, NIC guard (`nic_guard`: PCI paths match the recorded ones), softflowd running, the shim loaded (`flows_shim`), and that non-loopback listeners are a subset of TCP 22 and 9100 (UDP: only the DHCP clients, 68 and 546) |
| `kokabiel-capture <ip-or-mac> [seconds] [--max-mb N]` | time-boxed, size-capped `tcpdump` for one device to the SD card (RAM fallback). Last stdout line is JSON; exit codes 0 ok, 2 usage, 3 config, 4 space/memory, 5 tcpdump failed, 75 busy |
| `kokabiel-sd describe\|mount` | show or mount the SD card |

### Captures from your workstation

```sh
cd ansible
ansible-playbook capture.yml -l probe1 -e target=192.0.2.50 -e seconds=60
```

The pcap is fetched to `ansible/captures/<probe>/` (gitignored) for Wireshark, and RAM copies on the
probe are removed. Use it for "what exactly is this device sending", which flows cannot answer. To
investigate one wired device instead, re-point the switch's mirror (see
[docs/unifi-mirror.md](docs/unifi-mirror.md)).

## Prometheus and Grafana

Each probe runs `prometheus-node-exporter-lua` on TCP 9100 of the management interface: standard
`node_*` metrics plus `kokabiel_*` (capture link, promisc, NIC guard, softflowd flows and drops,
storage, temperature, capture jobs). The firewall admits scrapes only from `metrics_allowed_src`
(default the management subnet; for in-cluster Prometheus use the node address after SNAT). The
play prints a scrape snippet for your probes at the end. Import
[grafana/kokabiel-probe.json](grafana/kokabiel-probe.json) into Grafana. Kokabiel never edits your
monitoring stack. Scrape examples (plain, Prometheus Operator), alert rules and the metric
reference are in [docs/metrics.md](docs/metrics.md). Collector notes are in
[docs/collector.md](docs/collector.md): your collector must not do uncached per-flow lookups such
as reverse DNS, or exported flows can amplify themselves into a flood.

## Recovery ladder

Kokabiel never touches the bootloader, partition table or OS image; it only changes OpenWrt config,
installs packages and drops scripts. The worst case is a probe unreachable over the network. From
least to most drastic:

1. **Console fix:** keyboard and monitor, log in as root (passphrase in `ansible/logs/`), fix or
   revert the `uci` change.
2. **Soft factory reset:** `firstboot && reboot` at the console. Back to FriendlyWrt defaults; then
   [console bootstrap](docs/bootstrap.md) and the playbook again.
3. **Reflash:** write a FriendlyWrt or FriendlyELEC eFlasher image to a microSD card and boot from
   it (SD wins over eMMC), then reflash eMMC per FriendlyELEC's wiki.
4. **Maskrom:** hold MASK while powering on and flash over USB with Rockchip tools. Last resort.

The user button reboots the device (FriendlyWrt trigger); there is no long-press factory reset.

## Validation

1. Reboot the probe; `kokabiel-status` must pass every check.
2. From a Wi-Fi client on the mirrored AP, generate traffic to a wired host; its flows appear at the
   collector under the probe's exporter address within the active timeout (about 5 minutes).
3. Port-scan a wired host from a Wi-Fi client on a non-isolated SSID; the fan-out (one source, many
   ports, one-packet SYN-only flows) is visible in flows.
4. ARP test: sweep from a client on an isolated SSID and watch `tcpdump -ni eth1 arp` on the
   probe, to learn whether those broadcasts ever reach the mirror.

## Known platform risks

| Risk | Mitigation |
|---|---|
| **softflowd 1.1.0 processes zero packets** (no libpcap immediate mode; upstream fix irino/softflowd `f5c0f99103`, PR #71, unreleased) | `LD_PRELOAD` shim, `make shim` required before deploy; `kokabiel-status` checks `flows_shim` |
| FriendlyWrt 25.12 base: `apk` instead of `opkg`, fw4/nftables, kernel 6.1; major upgrades are reflash-only | detect the package manager; rebuild = reflash + play |
| Kernel 6.1 vs feed kmods built for 6.12 | never install `kmod-*`; the scripts refuse |
| `eth0`/`eth1` names reported to swap on some boots | PCI-path interface-role guard that gates softflowd, metric and alert |
| "PHY link never came up" on eth1 at boot (seen on upstream 24.10-rc) | link-up metric; hotplug re-applies settings when the link comes up |
| r8125 2.5G link flapping (reported on the sibling R6S) | carrier-change metric; fallback: limit advertised speed to 1G |
| NIC offloads silently distort captures | offloads off on the capture NIC, re-applied on link-up |
| softflowd default flow table (8192) too small for scans | `max_flows` raised to 65536; drop metrics |
| Boot from a bootable microSD beats eMMC | SD erase-with-confirmation flow |
| Passive case thermals under sustained load undocumented | temperature metric |
| Self-observation loops (probe export or collector lookups crossing the mirror) | keep the management path off the mirrored link; see [docs/collector.md](docs/collector.md) |

## Performance

Not yet measured. Idle, 24-hour household and sustained-transfer numbers (CPU, memory,
temperature, NIC and pcap drops, flows per second) will be added here with the firmware version
and date.

## Scope and non-goals

In scope: unsampled flow export for one mirrored port, health metrics, and on-demand packet capture.
Out of scope for v1: a web UI on the probe, monitoring 10G links, serving DNS or any network
service, DPI/IDS (Zeek, Suricata), server-side capture, automating the UniFi mirror through its
API, changing your network design, and deploying the collector, index or dashboards. The capture
interface never transmits, and the probe is never in the forwarding path.

## Development

```sh
make test   # pytest, including a privacy check that no private data is committed
make lint   # shellcheck and ansible-lint
```

This repository is public: nothing identifying a real network may be committed. Site-specific
values live in gitignored `site.env` and `ansible/inventory/`.

## License

MIT, see [LICENSE](LICENSE). The root passphrase generator vendors the EFF large wordlist (CC BY
3.0 US); attribution is in [NOTICE](NOTICE).

Further reading: [planning](docs/planning.md), [UniFi mirror](docs/unifi-mirror.md),
[collector](docs/collector.md), [metrics](docs/metrics.md), [bootstrap](docs/bootstrap.md).
