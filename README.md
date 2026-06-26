# NetOracle — Objective 1: Simulated SD-WAN/MPLS Environment

Air-gapped, multi-site SD-WAN-over-MPLS lab for predictive fault model training.
Containerlab-based, fully reproducible, zero internet dependency at runtime.

## Topology

```
                    ┌─────────────────────────────────────────┐
                    │           MPLS CORE (OSPF + LDP)        │
                    │    ┌──────────┐    ┌──────────┐        │
                    │    │  dc1-p1  │────│  hub-p1  │        │
                    │    └────┬─────┘    └────┬─────┘        │
                    │         │               │              │
                    │    ┌────┴─────┐    ┌────┴─────┐        │
                    │    │ dc2-p1   │    │ (core)   │        │
                    │    └──────────┘    └──────────┘        │
                    └─────────────────────────────────────────┘
                                │               │
                    ┌───────────┴─────┐   ┌─────┴────────────┐
                    │   dc1-pe1       │   │   hub-pe1        │
                    │   dc2-pe1       │   │   (hub PE)       │
                    └────────┬────────┘   └────────┬─────────┘
                             │                     │
              ┌──────────────┼─────────┬───────────┼──────────┐
              │              │         │           │          │
         dc1-ce1        dc2-ce1   hub-ce1    branch[1-6]-ce1
              │              │         │           │
         dc1-host       dc2-host  hub-host   branch[1-6]-host
```

**Sites:**
- 2x Datacenter (DC1, DC2) — dual PE-facing, redundant uplinks
- 1x Regional Hub — aggregation, overlay route-reflector
- 6x Branch sites — single-homed to hub or DC PEs

**Device roles:**
| Role | Count | Function |
|------|-------|----------|
| P    | 3     | MPLS label-switching (LDP), OSPF core |
| PE   | 3     | MP-BGP VPNv4, VRF segmentation, eBGP to CEs |
| CE   | 9     | SD-WAN edge, IPsec VTI overlay tunnels, overlay eBGP |
| Host | 9     | Traffic generators (iperf3, mgen, scapy) |

**Addressing:**
| Range | Purpose |
|-------|---------|
| 10.0.0.0/8 | Loopbacks and core links (IGP) |
| 10.1.0.0/16 | PE-CE and P-P links |
| 172.16.0.0/24 | DC1-LAN |
| 172.16.1.0/24 | DC2-LAN |
| 172.16.2.0/24 | Hub-LAN |
| 172.16.3-8.0/24 | Branch LANs |
 | 10.99.0.0/16 | IPsec VTI overlay tunnel endpoints |
 | 10.99.1.0/24 | Direct peering VTI (dc2↔branch5) |

## Architecture

### Underlay (MPLS VPN)

1. **IGP:** OSPF area 0 on all P/PE devices — carries loopbacks and core link subnets
2. **MPLS:** LDP label distribution across all core-facing interfaces (P-to-P, P-to-PE)
3. **VPN:** MP-BGP VPNv4 between PEs with three VRFs:
   - `VRF-CORP` (RD 65000:200, RT 200:200)
   - `VRF-FINANCE` (RD 65000:100, RT 100:100)
   - `VRF-GUEST` (RD 65000:300, RT 300:300)
4. **PE-CE:** eBGP per-VRF between PE and CE devices

### Overlay (SD-WAN)

1. **IPsec VTI tunnels** between CE devices using strongSwan (IKEv2, AES256-SHA256)
2. Hub-ce1 establishes IPsec SAs to all 8 spoke CEs; dc2-ce1 ↔ branch5-ce1 have an additional direct VTI peering
3. **eBGP over VTI interfaces** — overlay route exchange independent of underlay MPLS (hub-ce1 is route-reflector for all spokes)
4. Dual-plane routing enables detection of underlay-vs-overlay divergence
5. See [docs/ipsec.md](docs/ipsec.md) for full IPsec architecture details

### QoS

- DSCP marking at traffic generators: EF (46), AF41 (34), AF21 (18), DF (0)
- Per-hop HTB queuing on PE interfaces with strict priority for EF
- Applied uniformly via `scripts/apply_qos.sh`

## File Structure

```
netoracle/
├── topology.clab.yml              # Containerlab topology definition
├── configs/                        # Per-device FRR configs
│   ├── dc1-p1/  dc2-p1/  hub-p1/  # P routers (OSPF + LDP only)
│   ├── dc1-pe1/ dc2-pe1/ hub-pe1/ # PE routers (OSPF + LDP + MP-BGP + VRFs)
│   └── *-ce1/                     # CE routers (eBGP + GRE overlay + overlay BGP)
├── traffic/
│   ├── scheduler.py               # Main traffic orchestrator
│   ├── generator.py               # Per-protocol generator classes
│   ├── diurnal.py                 # Business-hour / ramp pattern engine
│   ├── ground_truth.py            # Structured CSV logger
│   ├── app_servers.py             # HTTP/HTTPS/FTP/SMB/DNS server lifecycle
│   ├── app_generators.py          # Application-layer protocol generators
│   ├── profiles.yaml              # Traffic profile & site-pair config
│   └── __init__.py
├── fault_injection/
│   ├── scenario_runner.py         # Scenario orchestrator
│   ├── faults.py                  # Fault primitives (tc netem, BGP, LDP, QoS)
│   ├── scenarios.yaml             # Scenario definitions
│   └── __init__.py
├── telemetry/
│   ├── collector.py               # Main telemetry orchestrator
│   ├── pollers.py                 # Interface/BGP/OSPF/MPLS/GRE/CPU pollers
│   ├── syslog_server.py           # UDP syslog receiver
│   └── config.yaml                # Telemetry configuration
├── scripts/
│   ├── setup.sh                   # Prerequisites check + image pull
│   ├── start_lab.sh               # Deploy topology + apply QoS + start telemetry
│   ├── stop_lab.sh                # Destroy topology
│   ├── apply_qos.sh               # Per-hop QoS via tc (HTB + priority)
│   ├── start_telemetry.sh         # Launch telemetry collector + syslog
│   ├── enable_syslog.sh           # Configure FRR syslog forwarding
│   └── verify_lab.sh              # Full connectivity test suite
├── docs/
│   └── schema.md                  # Ground truth log schema
├── requirements.txt               # Python deps
└── README.md
```

## Quick Start

### Prerequisites

- Docker (20.10+)
- Containerlab (0.50+): `sudo bash -c "$(curl -sL https://get.containerlab.dev)"`
- Python 3.8+

### Setup

```bash
cd netoracle
bash scripts/setup.sh
```

### Start Lab

```bash
# Deploy + converge + apply QoS (expect 60-90s)
bash scripts/start_lab.sh

# Verify everything is up
bash scripts/verify_lab.sh
```

### Run Traffic

```bash
# Baseline: all profiles, all site pairs, 1 hour
python3 -m traffic.scheduler baseline 3600

# Ramp-to-precursor: linear ramp bulk_transfer on branch1→hub
python3 -m traffic.scheduler precursor bulk_transfer branch1_to_hub 180

# Diurnal cycle: 24-hour business pattern
python3 -m traffic.scheduler diurnal 24
```

### Run Fault Scenarios

```bash
# Lists available scenarios
python3 -m fault_injection.scenario_runner

# Progressive congestion (ramp traffic to saturation)
python3 -m fault_injection.scenario_runner progressive_congestion

# BGP flap cascade (sequential neighbor resets)
python3 -m fault_injection.scenario_runner bgp_flap_cascade

# Intermittent MPLS failure (LDP disable + interface flap)
python3 -m fault_injection.scenario_runner intermittent_mpls_failure

# Controller policy drift (QoS reclassification)
python3 -m fault_injection.scenario_runner controller_policy_drift
```

### Stop Lab

```bash
bash scripts/stop_lab.sh
```

## Traffic Profiles

| Profile | Tool | DSCP | Rate | Behavior |
|---------|------|------|------|----------|
| voice | mgen | EF (46) | 80 kbps | Periodic RTP, 20ms interval |
| video | iperf3 UDP | AF41 (34) | 2.5 Mbps | Bursty, parallel streams |
| business_app | iperf3 TCP | AF21 (18) | 50 Mbps | Multi-session TCP |
| bulk_transfer | iperf3 TCP | DF (0) | 200 Mbps | Long-lived, large window |
| db_erp | scapy | AF21 (18) | 500 pps | Burst query-response |
| background_noise | iperf3 TCP | DF (0) | 5 Mbps | Constant low rate |
| **http_traffic** | Python urllib | AF21 (18) | 10 rps | Real HTTP/1.1 GETs on port 8080 |
| **https_traffic** | Python urllib+ssl | AF21 (18) | 8 rps | Real HTTPS/TLS GETs on port 8443 |
| **ftp_traffic** | Python ftplib | DF (0) | 1 xfer/5s | Real FTP downloads, multi-MB files |
| **smb_traffic** | smbprotocol | DF (0) | 1 xfer/10s | Real SMB/CIFS file reads on port 445 |
| **dns_traffic** | Raw UDP sockets | CS6 (48) | 50 qps | Real DNS A-record queries on port 5353 |
| **voip_rtp** | Raw UDP, RTP headers | EF (46) | 80 kbps | Structured RTP, 20ms packetization |
| **database_erp** | Raw UDP, SQL payloads | AF21 (18) | 500 pps | SQL-like query/reply, burst pattern |

## Fault Scenarios

### 1. Progressive Congestion
Ramp bulk traffic on 3 branches toward the hub link until congestion develops.
Captures precursor window telemetry (queue depth growth, latency increase,
packet loss progression) that the predictive model learns to recognize.

### 2. BGP Flap Cascade
Sequentially reset BGP sessions on hub PE across multiple VRF neighbors
with increasing hold-down timers. Tests BGP convergence detection and
path divergence between underlay and overlay.

### 3. Intermittent MPLS Underlay Failure
Disable MPLS LDP on a core link, then flap a P-P interface cyclically.
Traffic shifts to overlay while underlay recovers — classic SD-WAN
divergence signal for model training.

### 4. Controller Policy Drift
Change DSCP reclassification on PE ingress (EF→BE, AF41→BE) to
simulate SD-WAN controller misconfiguration. Per-class QoS degradation
is observable downstream.

## Monitoring Stack

The lab includes a full monitoring stack with Prometheus, Grafana, cAdvisor, and Telegraf.

### Components

| Container | Image | Mgmt IP | Port | Purpose |
|-----------|-------|---------|------|---------|
| `prometheus` | prom/prometheus:v2.51.0 | .110 | 9090 | Time-series DB, scrapes telemetry-collector + telegraf + cadvisor |
| `grafana` | grafana/grafana:10.4.2 | .111 | 3000 | Pre-provisioned dashboards, Prometheus datasource |
| `cadvisor` | gcr.io/cadvisor:v0.49.1 | .112 | 8080 | Container resource metrics (CPU/mem/network per container) |
| `telegraf` | telegraf:1.30 | .113 | 9273 | Docker + host-level metrics (disk, CPU, mem, net) |
| `telemetry-collector` | ubuntu:22.04 (custom) | .100 | 8000 | Device-level polling → Prometheus metrics endpoint |

### Data Flow

```
[FRR Devices] --docker exec vtysh--> [telemetry-collector] --/metrics--> [Prometheus] --scrape--> [Grafana]
[Docker Host] --------v--------> [cAdvisor:8080/metrics] -----> [Prometheus]
[Docker Host] --docker.sock--> [Telegraf:9273/metrics] --------> [Prometheus]
[FRR Devices] -----syslog UDP---> [telemetry-collector:5514] --> CSV
```

### Dashboards

The Grafana instance ships with a pre-configured **NetOracle Network Overview** dashboard
showing:
- Top interfaces by throughput (bar gauge + time series)
- Interface error rates
- BGP peer status (established/down counts + prefixes table)
- OSPF neighbor state (full/down/transitioning)
- Device memory utilization
- GRE tunnel throughput
- System load averages
- MPLS label counts per device

### Access

| Service | URL | Credentials |
|---------|-----|-------------|
| Grafana | http://192.168.100.111:3000 | admin / admin |
| Prometheus | http://192.168.100.110:9090 | none |
| cAdvisor | http://192.168.100.112:8080 | none |
| Telemetry Metrics | http://192.168.100.100:8000/metrics | none |

### Verification

```bash
bash scripts/start_monitoring.sh
```

## Telemetry Collection

The telemetry collector runs continuously alongside the lab, polling every device
on configurable intervals and writing structured CSV to `telemetry_data/`.

### Data Sources

| CSV File | Source | Interval | Content |
|----------|--------|----------|---------|
| `interface_counters.csv` | `vtysh show interface json` / `ip -s link` | 10s | rx/tx bytes, packets, errors, drops, bps rate |
| `bgp_events.csv` | `vtysh show bgp summary json` (VPNv4 + VRF + overlay) | 15s | Neighbor state, prefixes received, ASN, uptime |
| `ospf_events.csv` | `vtysh show ip ospf neighbor json` | 30s | Neighbor state, DR/BDR, interface, priority |
| `mpls_stats.csv` | `vtysh show mpls ldp binding` | 60s | Prefix-to-label bindings, interfaces |
 | `tunnel_stats.csv` | `ip -s link show type gre/vti` | 10s | Tunnel interface rx/tx counters |
 | `ipsec_sa.csv` | `ipsec status` / `ip -s xfrm state` | 15s | IKE SA state, XFRM stats (bytes/packets encrypted) |
| `cpu_memory.csv` | `/proc/meminfo`, `/proc/loadavg` | 60s | Memory used/total, load averages |
| `syslog.csv` | UDP syslog (port 5514) from FRR containers | real-time | BGP/OSPF/interface/route events with category tags |

### Architecture

```
[FRR devices] --docker exec vtysh--> [telemetry-collector] --> telemetry_data/*.csv
[FRR devices] -----syslog UDP------> [port 5514 listener] --> telemetry_data/syslog.csv
[CE devices]  --docker exec ipsec--> [telemetry-collector] --> telemetry_data/ipsec_sa.csv
```

### Manual control

```bash
# Start telemetry separately (auto-started by start_lab.sh)
bash scripts/start_telemetry.sh

# Stop telemetry
kill $(cat /tmp/telemetry_collector.pid)

# Watch live data
tail -f telemetry_data/interface_counters.csv
```

## Ground Truth Logs

Located in `ground_truth/` after any run:

- `flow_log.csv` — Every flow start/stop with full parameters
- `fault_log.csv` — Every injected fault with type, target, phase, parameters

Schema documentation in `docs/schema.md`. These logs are the labeled dataset
for Objectives 2 (predictive modeling) and 3 (RAG ingestion).

## Verification

```bash
# Full automated check
bash scripts/verify_lab.sh

# Manual checks
docker exec -it hub-pe1 vtysh -c "show bgp vpnv4 unicast summary"
docker exec -it dc1-p1 vtysh -c "show mpls ldp binding"
docker exec -it branch1-host ping -c 3 172.16.0.2
docker exec -it branch1-host iperf3 -c 172.16.0.2 -t 10 -b 10M
```

## IP/VRF Scheme

### MPLS Core (AS 65000)

| Device | Loopback | Role | eBGP Peer |
|--------|----------|------|-----------|
| dc1-p1 | 10.0.0.1/32 | P (LDP only) | — |
| dc2-p1 | 10.0.1.1/32 | P (LDP only) | — |
| hub-p1 | 10.0.2.1/32 | P (LDP only) | — |
| dc1-pe1 | 10.0.0.2/32 | PE (VPNv4 RR) | VPNv4 to 10.0.1.2, 10.0.2.2 |
| dc2-pe1 | 10.0.1.2/32 | PE (VPNv4 RR) | VPNv4 to 10.0.0.2, 10.0.2.2 |
| hub-pe1 | 10.0.2.2/32 | PE (VPNv4 RR) | VPNv4 to 10.0.0.2, 10.0.1.2 |

### Customer Edge (per-ASN)

| CE | ASN | VRF | LAN Subnet | Overlay Peers |
|----|-----|-----|------------|---------------|
| dc1-ce1 | 65501 | CORP | 172.16.0.0/24 | hub-ce1 (RR) |
| dc2-ce1 | 65502 | FINANCE | 172.16.1.0/24 | branch5-ce1 |
| hub-ce1 | 65503 | CORP | 172.16.2.0/24 | dc1-ce1, branch1-4 (RR) |
| branch1-ce1 | 65506 | CORP | 172.16.3.0/24 | hub-ce1 |
| branch2-ce1 | 65507 | CORP | 172.16.4.0/24 | hub-ce1 |
| branch3-ce1 | 65508 | CORP | 172.16.5.0/24 | hub-ce1 |
| branch4-ce1 | 65504 | CORP | 172.16.6.0/24 | hub-ce1 |
| branch5-ce1 | 65505 | FINANCE | 172.16.7.0/24 | dc2-ce1 |
| branch6-ce1 | 65509 | GUEST | 172.16.8.0/24 | (none) |
