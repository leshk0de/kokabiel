# Collector notes

Kokabiel does not deploy or configure your collector, Vector, an index or dashboards. These notes
cover what the collector side needs to know. Example queries are pseudo-SQL for a
Quickwit/Elasticsearch-style index: translate them to your engine.

## Pointing the probe at a collector

Set the destination in `site.env`:

```sh
COLLECTOR_HOST=<COLLECTOR_IP>   # e.g. 192.0.2.10
COLLECTOR_PORT=2055             # UDP
EXPORT_VERSION=10               # 10 = IPFIX (default), 9 = NetFlow v9
```

[goflow2](https://github.com/netsampler/goflow2) decodes both IPFIX and NetFlow v9 into one schema, so
either setting works with it, and so with any v9/IPFIX collector listening on that UDP port. Flows are
unsampled; nothing needs multiplying by a sampling rate.

## Warning: no per-flow live lookups (feedback loop)

A collector pipeline that performs an **uncached, live lookup for every flow** (reverse DNS is the
usual one) generates DNS traffic. That traffic becomes flows, which trigger more lookups. Raising a
gateway's sampling from 1:10 to 1:2 tipped exactly such a loop into a roughly 50x flood. Unsampled
export from a probe can do the same, faster.

Fix it before turning the probe on:

- Resolve names **at query time**, or enrich lazily from a cache.
- If you must enrich during ingest, cache aggressively and bound the lookup rate.
- Never do per-flow live lookups.

Related: keep the probe's management path off the mirrored link, so its own export is not captured.
See [planning.md](planning.md).

## Pipeline gotchas for probe flows

Found while wiring the probe into a goflow2 → Vector → Quickwit pipeline that already ingested a
gateway's flows:

- **Empty next hop.** softflowd sends no next hop, and goflow2 emits `"next_hop": ""`. An index
  that types `next_hop` as an IP address rejects the **whole document**. Drop empty address
  fields before indexing.
- **Sampling defaults.** If the pipeline assumes a sampling rate for exporters that report 0 (a
  common workaround for gateways that never send it), scope that to the gateway. The probe also
  reports 0, and for it that means 1:1. Otherwise its bytes and packets get multiplied.
- **MAC addresses.** With `track_level=ether` (the default) the IPFIX templates carry
  `sourceMacAddress` (IE 56), `postDestinationMacAddress` (IE 57), `vlanId` (58) and
  `postVlanId` (59). goflow2 decodes them into `src_mac` and `dst_mac`. An index with a fixed or
  lenient schema silently drops fields it does not declare, so add them to the mapping if you want
  to keep them. Gateways that do not export MACs produce an all-zero MAC address, which is
  worth dropping at ingest.
- **Reverse DNS at ingest.** If you keep it, skip flows on port 53 and private addresses, and send
  the lookups through a local caching resolver. Every lookup that leaves the collector host is
  another flow.

## Telling sources apart

If the probe shares an index with the gateway's flows, distinguish them by **exporter address**: in
goflow2 output this is `sampler_address`. Use it as a filter or facet. Do not rely on the gateway's
exported `sampling_rate`: it was observed to stay stale after a sampling change.

Also note the gateway's flows are sampled (minimum 1:2) while the probe's are not, so do not compare
counts between them directly.

## Example queries

These are pseudo-SQL sketches. Translate them to your query engine and schema.

Field names follow goflow2 (`src_addr`, `dst_addr`, `dst_port`, `proto`, `tcp_flags`, `packets`,
`bytes`, `time_flow_start_ns`). Adjust to your schema. Always restrict to the probe with
`sampler_address:<PROBE_IP>` (for example `192.0.2.20`).

### Scan detection: fan-out

One source reaching many distinct destinations or ports within a few minutes.

```sql
SELECT src_addr,
       COUNT(DISTINCT dst_addr) AS dsts,
       COUNT(DISTINCT dst_port) AS ports
FROM flows
WHERE sampler_address = '192.0.2.20'
  AND time > now() - interval '5 minutes'
GROUP BY src_addr
HAVING COUNT(DISTINCT dst_addr) > 50 OR COUNT(DISTINCT dst_port) > 100
```

As an Elasticsearch/Quickwit-style aggregation: filter on `sampler_address` and a 5-minute time range;
terms aggregation on `src_addr` with sub-aggregations `cardinality(dst_addr)` and
`cardinality(dst_port)`; keep buckets above your thresholds.

### Scan detection: SYN-only one-packet flows

```sql
SELECT src_addr, COUNT(*) AS probes
FROM flows
WHERE sampler_address = '192.0.2.20'
  AND tcp_flags = 2 AND packets = 1
  AND time > now() - interval '5 minutes'
GROUP BY src_addr
ORDER BY probes DESC
```

(`tcp_flags == 2` is SYN alone.) The same shape applies in an aggregation: filter `tcp_flags:2 AND
packets:1`, terms on `src_addr`.

### Beaconing

The same `src -> dst:port` at regular intervals with small byte counts.

```sql
SELECT src_addr, dst_addr, dst_port,
       COUNT(*) AS flows,
       AVG(bytes) AS avg_bytes,
       STDDEV(interval_seconds) AS jitter
FROM flows_with_intervals   -- flows with the gap since the previous flow of the same tuple
WHERE sampler_address = '192.0.2.20'
  AND time > now() - interval '6 hours'
GROUP BY src_addr, dst_addr, dst_port
HAVING COUNT(*) > 20 AND AVG(bytes) < 2000 AND STDDEV(interval_seconds) < 5
```

Most search engines cannot compute the gap natively. Approximate it by bucketing with a
`date_histogram` per `(src, dst, port)` and looking for buckets that are filled at an even cadence,
or compute the interval in your query tool. Small, steady, long-lived patterns to a single external
host are the signal; expect to whitelist legitimate pollers (NTP, update checks, cloud sync).

### Caveats

- Scans blocked by client isolation inside an AP never reach the wire, so they will not appear.
- Names in results should be resolved at query time (see the warning above).
