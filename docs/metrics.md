# Metrics, dashboard and alerts

Each probe runs OpenWrt's `prometheus-node-exporter-lua` on TCP 9100 of its management
interface. It serves the usual `node_*` metrics (CPU, memory, load, network counters, time,
uname) plus the `kokabiel_*` metrics from one extra collector, evaluated on every scrape. Kokabiel
never edits your monitoring stack: this page gives you the pieces to wire in yourself.

## Reachability

The probe's firewall admits TCP 9100 only from `METRICS_ALLOWED_SRC` (default: the management
subnet). A Prometheus running inside Kubernetes usually reaches the probe through the node's
address after SNAT, not the pod IP. So the **node address** must fall inside `METRICS_ALLOWED_SRC`.
If scrapes time out, set `METRICS_ALLOWED_SRC` to a CIDR covering your nodes and re-run the play.

## Scrape configuration

The examples use RFC 5737 documentation addresses. Replace them with your probes' management
addresses. Every form attaches a `probe` label, which the dashboard and alerts rely on.

### Plain Prometheus

```yaml
scrape_configs:
  - job_name: kokabiel
    scrape_interval: 30s
    static_configs:
      - targets: ["192.0.2.10:9100"]
        labels: { probe: probe-a }
      - targets: ["192.0.2.11:9100"]
        labels: { probe: probe-b }
```

With many probes, you can instead derive the label from the target:

```yaml
    relabel_configs:
      - source_labels: [__address__]
        regex: "([^:]+):.*"
        target_label: probe
```

### Prometheus Operator ScrapeConfig

```yaml
apiVersion: monitoring.coreos.com/v1alpha1
kind: ScrapeConfig
metadata:
  name: kokabiel
  namespace: monitoring
  labels:
    release: kube-prometheus-stack   # match your Prometheus' scrapeConfigSelector
spec:
  scrapeInterval: 30s
  staticConfigs:
    - targets: ["192.0.2.10:9100"]
      labels: { probe: probe-a }
    - targets: ["192.0.2.11:9100"]
      labels: { probe: probe-b }
```

### additionalScrapeConfigs

For kube-prometheus-stack, put the plain job from above in the chart values:

```yaml
prometheus:
  prometheusSpec:
    additionalScrapeConfigs:
      - job_name: kokabiel
        scrape_interval: 30s
        static_configs:
          - targets: ["192.0.2.10:9100"]
            labels: { probe: probe-a }
```

## Grafana dashboard

`grafana/kokabiel-probe.json` has rows for health, traffic, loss, system and capture jobs. It has a
`Prometheus` datasource variable and a multi-value `probe` variable
(`label_values(kokabiel_capture_link_up, probe)`).

- **UI:** Dashboards, New, Import, upload the JSON, then pick the Prometheus datasource.
- **Provisioning:** copy the file into a folder served by a dashboard provider, for example
  `/etc/grafana/provisioning/dashboards/kokabiel/`:

  ```yaml
  apiVersion: 1
  providers:
    - name: kokabiel
      type: file
      options:
        path: /etc/grafana/provisioning/dashboards/kokabiel
  ```

  With the Grafana Helm chart sidecar, put the JSON in a ConfigMap labelled
  `grafana_dashboard: "1"`.

## Example alert rules

These are examples and are not deployed by this repo. As a plain Prometheus rules file, use
the `groups:` block; as a PrometheusRule, wrap it in `spec:`.

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: kokabiel
  namespace: monitoring
spec:
  groups:
    - name: kokabiel
      rules:
        - alert: KokabielExporterDown
          expr: up{job="kokabiel"} == 0
          for: 5m
          labels: { severity: critical }
          annotations:
            summary: "Probe {{ $labels.probe }} exporter is not scraping"
        - alert: KokabielNoFlowsExported
          expr: |
            rate(kokabiel_softflowd_flows_exported_total[10m]) == 0
            and on (probe) kokabiel_capture_link_up == 1
          for: 10m
          labels: { severity: warning }
          annotations:
            summary: "Probe {{ $labels.probe }} exports no flows although the link is up"
        - alert: KokabielDropsRising
          expr: |
            rate(kokabiel_softflowd_pcap_dropped_total[10m]) > 0
            or rate(kokabiel_capture_rx_dropped_total[10m]) > 0
            or rate(kokabiel_capture_rx_missed_errors_total[10m]) > 0
          for: 10m
          labels: { severity: warning }
          annotations:
            summary: "Probe {{ $labels.probe }} is dropping packets (libpcap or NIC)"
        - alert: KokabielCaptureLinkDown
          expr: kokabiel_capture_link_up == 0
          for: 2m
          labels: { severity: critical }
          annotations:
            summary: "Probe {{ $labels.probe }} capture link is down"
        - alert: KokabielInterfaceGuardFailed
          expr: kokabiel_interface_guard_ok == 0
          for: 1m
          labels: { severity: critical }
          annotations:
            summary: "Probe {{ $labels.probe }}: capture/management MAC differs from the recorded one"
        - alert: KokabielSdCardNearlyFull
          expr: |
            kokabiel_storage_free_bytes{mount="sd"}
              / kokabiel_storage_size_bytes{mount="sd"} < 0.1
          for: 15m
          labels: { severity: warning }
          annotations:
            summary: "Probe {{ $labels.probe }} SD card is over 90% full"
        - alert: KokabielImmediateModeShimMissing
          expr: |
            rate(kokabiel_softflowd_packets_received_total[10m]) > 0
            and rate(kokabiel_softflowd_packets_processed_total[10m]) == 0
          for: 10m
          labels: { severity: warning }
          annotations:
            summary: "Probe {{ $labels.probe }}: libpcap receives packets but softflowd processes none (immediate-mode shim missing?)"
```

## Metric reference

All `kokabiel_*` metrics carry the scrape's `probe` label in addition to the labels listed.
Counters reset when the exporter or interface restarts, so use `rate()` or `increase()`.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `kokabiel_capture_link_up` | gauge | | 1 if the capture interface has carrier. |
| `kokabiel_capture_promisc` | gauge | | 1 if the capture interface is in promiscuous mode. |
| `kokabiel_capture_carrier_changes_total` | counter | | Carrier up/down transitions (link flaps). |
| `kokabiel_capture_rx_packets_total` | counter | | Packets received on the capture interface. |
| `kokabiel_capture_rx_bytes_total` | counter | | Bytes received on the capture interface. |
| `kokabiel_capture_rx_dropped_total` | counter | | Packets dropped by the NIC driver or kernel. |
| `kokabiel_capture_rx_missed_errors_total` | counter | | Packets missed by the NIC (ring buffer overrun). |
| `kokabiel_interface_guard_ok` | gauge | | 1 if both capture and management MACs match the recorded values; 0 on mismatch or when unrecorded (fails closed). |
| `kokabiel_softflowd_up` | gauge | | 1 if `softflowctl statistics` answered. The other softflowd metrics are absent when 0. |
| `kokabiel_softflowd_active_flows` | gauge | | Flows currently tracked. |
| `kokabiel_softflowd_flows_exported_total` | counter | | Flows exported to the collector. |
| `kokabiel_softflowd_packets_processed_total` | counter | | Packets softflowd accounted into flows. |
| `kokabiel_softflowd_packets_received_total` | counter | | Packets received by libpcap. |
| `kokabiel_softflowd_pcap_dropped_total` | counter | | Packets dropped by libpcap. |
| `kokabiel_storage_size_bytes` | gauge | `mount` (`overlay`, `sd`) | Filesystem size. The `sd` series exists only while the card is mounted. |
| `kokabiel_storage_free_bytes` | gauge | `mount` (`overlay`, `sd`) | Free bytes on the filesystem. |
| `kokabiel_sd_present` | gauge | | 1 if the SD capture directory is a mount. |
| `kokabiel_temperature_celsius` | gauge | `zone` | Thermal zone temperature from `/sys/class/thermal`. |
| `kokabiel_capture_running` | gauge | | 1 while a `capture.sh` run is in progress. |
| `kokabiel_capture_bytes` | gauge | | Bytes written by the current capture run; 0 when none is running. |
| `kokabiel_capture_info` | gauge | `target` | Always 1 while a capture runs; the label names its target. |
| `kokabiel_unexpected_listeners` | gauge | | TCP listeners on non-loopback addresses other than 22 and 9100. Should be 0. |

Standard `node_*` metrics from the exporter (`node_cpu_seconds_total`, `node_memory_*_bytes`,
`node_load1/5/15`, `node_network_*`, `node_time_seconds`, `node_uname_info`) are also available.
