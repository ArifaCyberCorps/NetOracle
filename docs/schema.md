# Ground Truth Schema Documentation

## Overview

Ground truth logs are the labeled dataset produced by Objective 1 for consumption by
Objective 2 (predictive modeling) and Objective 3 (RAG ingestion). Two CSV files are
produced: `flow_log.csv` and `fault_log.csv`. Both are append-only, timestamp-aligned
(NTP-synced within the lab), and designed for easy loading into pandas/Spark.

## Flow Log Schema (`flow_log.csv`)

| Column | Type | Description |
|---|---|---|
| `timestamp_unix` | float (epoch sec, 3dp) | When the flow event occurred |
| `timestamp_iso` | string (ISO 8601) | Human-readable UTC timestamp |
| `flow_id` | string | Unique flow identifier (e.g., `baseline-voice-branch1-to-hub-1`) |
| `profile` | string | Traffic profile name: `voice`, `video`, `business_app`, `bulk_transfer`, `db_erp`, `background_noise` |
| `source` | string | Source host name (e.g., `branch1-host`) |
| `dest` | string | Destination host name (e.g., `hub-host`) |
| `source_ip` | string (IPv4) | Source IP address |
| `dest_ip` | string (IPv4) | Destination IP address |
| `protocol` | string | Generator tool: `iperf3`, `mgen`, `scapy` |
| `dscp` | string | DSCP marking: `EF`, `AF41`, `AF21`, `DF` |
| `rate_bps` | int | Configured data rate in bits/sec |
| `packet_size` | int | Packet/payload size in bytes |
| `duration_sec` | int | Intended flow duration in seconds |
| `expected_volume_bytes` | int | `rate_bps * duration_sec / 8` |
| `diurnal_multiplier` | float | Diurnal scaling factor applied (0.0–1.0) |
| `session_id` | string | Generator session UUID (truncated 8 chars) |
| `status` | string | `started` or `completed` |

### Usage notes for ML pipeline

- Join on `flow_id` and `session_id` to pair start/complete events.
- Filter `status == "started"` to get the intended traffic at time T.
- Join to fault log by timestamp range to label pre-fault / during-fault / post-fault windows.
- `diurnal_multiplier` tells the model whether this flow was running at business hours (1.0) or overnight (~0.2).

## Fault Log Schema (`fault_log.csv`)

| Column | Type | Description |
|---|---|---|
| `timestamp_unix` | float (epoch sec, 3dp) | When the fault event was injected |
| `timestamp_iso` | string (ISO 8601) | Human-readable UTC timestamp |
| `scenario_id` | string | Scenario name (e.g., `bgp_flap_cascade`) |
| `fault_type` | string | Type: `bgp_reset`, `interface_flap`, `mpls_ldp_disable`, `qos_change`, `traffic_ramp`, `ipsec_sa_down`, `ipsec_sa_up`, `ipsec_stop`, `ipsec_start`, `vti_down`, `vti_up`, `scenario_start`, `scenario_end` |
| `target` | string | Target container/device (e.g., `hub-pe1`, `dc1-p1:eth2`) |
| `parameters_json` | string (JSON) | Fault parameters as JSON object |
| `phase` | string | Scenario phase: `init`, `warmup`, `ramp_to_congestion`, `sustained_congestion`, `fault_active`, `recovery`, `complete` |
| `description` | string | Human-readable description |

### Pre-defined scenario phases

Each scenario in `scenarios.yaml` maps faults to phases. The ML pipeline uses `phase`
to segment time-series telemetry into labeled regions:

| Phase | Meaning |
|---|---|
| `init` | Scenario starting, no faults yet |
| `warmup` | Establishing baseline telemetry |
| `fault_active` | Fault is being injected |
| `sustained_congestion` | Congestion is held steady |
| `recovery` | Fault is cleared, link/route recovering |
| `complete` | Scenario finished |

## Directory Structure

```
ground_truth/
├── flow_log.csv       # Append-only flow events
├── fault_log.csv      # Append-only fault events
└── runs/              # Optional per-run directories
    └── YYYYMMDD_HHMMSS_scenario/
        ├── flow_log.csv
        └── fault_log.csv
```

## Integration with Telemetry Pipeline

The telemetry collector (Objective 2) should:
1. Read both CSVs at startup or poll for changes
2. Align network telemetry data with flow/fault events by timestamp
3. Label each telemetry sample with:
   - `flow_id` of any active flows at that time
   - `phase` from the most recent fault event (or "normal" if none active)
