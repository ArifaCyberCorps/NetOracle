# IPsec SD-WAN Overlay Architecture

## Overview

The NetOracle lab replaces traditional GRE overlay tunnels with IPsec-based
Virtual Tunnel Interfaces (VTI). This provides an encrypted SD-WAN overlay
that more faithfully represents production multi-site deployments.

## Architecture

### Components

| Component | Role | Technology |
|-----------|------|-----------|
| **strongSwan** | IKEv2 daemon | IPsec tunnel mode with ESP/AES256-SHA256 |
| **VTI** | Kernel virtual interfaces | Linux VTI driver (`ip_vti`) |
| **FRR** | Routing daemon | BGP route-reflector over VTI interfaces |
| **XFRM** | Kernel IPsec framework | SA/SP database, encryption engine |

### Data Flow

```
[CE: FRR] --BGP over VTI--> [vti100: 10.99.0.0/31]
                                      |
                            IPsec tunnel (ESP/AES256)
                            outer: lo(10.0.2.3) <-> lo(10.0.0.3)
                                      |
                            MPLS underlay (OSPF+LDP)
                            eth1: CE <-> PE
```

1. Application traffic arrives at CE router (eth2, LAN side)
2. FRR routes traffic into VTI interface based on BGP-learned routes
3. Kernel matches VTI mark to IPsec SA (via XFRM policy database)
4. IPsec encrypts the inner IP packet in tunnel mode (ESP)
5. Outer IP header uses CE loopbacks as tunnel endpoints
6. Encrypted packet transits the MPLS underlay to the remote CE
7. Remote CE's kernel decrypts via matching IPsec SA
8. Decrypted inner packet appears on the remote VTI interface
9. FRR routes traffic to the destination LAN

### Hub-and-Spoke Route-Reflector

hub-ce1 (AS 65503) establishes IPsec VTI tunnels to all 8 spoke CEs:

| Spoke | ASN | VTI Name | Mark | VTI IP | Remote CE Loopback |
|-------|-----|----------|------|--------|-------------------|
| dc1-ce1 | 65501 | vti100 | 100 | 10.99.0.0/31 | 10.0.0.3 |
| dc2-ce1 | 65502 | vti101 | 101 | 10.99.0.10/31 | 10.0.1.3 |
| branch1-ce1 | 65506 | vti102 | 102 | 10.99.0.2/31 | 10.0.3.1 |
| branch2-ce1 | 65507 | vti103 | 103 | 10.99.0.4/31 | 10.0.4.1 |
| branch3-ce1 | 65508 | vti104 | 104 | 10.99.0.6/31 | 10.0.5.1 |
| branch4-ce1 | 65504 | vti105 | 105 | 10.99.0.8/31 | 10.0.6.1 |
| branch5-ce1 | 65505 | vti106 | 106 | 10.99.0.12/31 | 10.0.7.1 |
| branch6-ce1 | 65509 | vti107 | 107 | 10.99.0.14/31 | 10.0.8.1 |

### Direct Peering (dc2-ce1 ↔ branch5-ce1)

These two sites have an additional direct IPsec VTI tunnel independent of the hub:

| Node | VTI Name | Mark | VTI IP | Remote End |
|------|----------|------|--------|-----------|
| dc2-ce1 | vti200 | 200 | 10.99.1.0/31 | branch5-ce1 |
| branch5-ce1 | vti200 | 200 | 10.99.1.1/31 | dc2-ce1 |

## Deployment

### Image Build

A custom Docker image (`netoracle/ce-ipsec:9.1`) extends `frrouting/frr:9.1` with
strongSwan pre-installed. Built via:

```bash
docker build -t netoracle/ce-ipsec:9.1 -f docker/Dockerfile.ce .
```

The `setup.sh` script automates this during initial configuration.

### Per-Node Initialization

At container startup, each CE runs `setup_ipsec.sh` which:

1. Creates VTI interfaces with unique marks
2. Writes `/etc/ipsec.conf` with per-node connection definitions
3. Writes `/etc/ipsec.secrets` with PSK authentication
4. Starts strongSwan via `ipsec start`

### CE Image (Dockerfile)

```dockerfile
FROM frrouting/frr:9.1
RUN apk add --no-cache strongswan
COPY scripts/setup_ipsec.sh /usr/local/bin/setup_ipsec.sh
```

## Telemetry

### IPsec SA Status

The `IkeSaPoller` polls two sources:

1. **`ipsec status`** — IKE SA state per connection (ESTABLISHED/CONNECTING/DOWN)
2. **`ip -s xfrm state`** — XFRM state statistics (bytes/packets encrypted)

Prometheus metrics exposed as `netoracle_ipsec_sa_state{device,connection,remote,state}`.

### VTI Interface Counters

VTI interfaces are collected by the standard `InterfaceCountersPoller` (they are
Linux netdevices visible via `show interface json` in FRR). No additional
poller needed.

## IPsec Configuration

### strongSwan Defaults (all connections)

| Parameter | Value | SD-WAN Purpose |
|-----------|-------|----------------|
| `ikelifetime` | 24h | IKE SA lifetime before rekey |
| `lifetime` | 4h | ESP CHILD SA lifetime before rekey |
| `rekeymargin` | 3m | Rekey begins this long before expiry |
| `rekeyfuzz` | 100% | Randomize rekey timing to avoid collision |
| `keyingtries` | %forever | Never stop retrying on failure |
| `keyexchange` | IKEv2 | Modern IKE protocol |
| `mobike` | no | Disable MOBIKE (static endpoints in lab) |
| `type` | tunnel | ESP tunnel mode (encrypts inner IP) |
| `esp` | aes256-sha256-modp2048! | ESP: AES-256-CBC + HMAC-SHA256 + DH-2048 |
| `ike` | aes256-sha256-modp2048! | IKE: same suite for key exchange |
| `dpdaction` | clear | Delete SA on DPD timeout (triggers reconnect) |
| `dpddelay` | 30s | DPD keepalive interval |
| `dpdtimeout` | 150s | Declare peer dead after no response |
| `close_action` | restart | Auto-restart connection when closed |

### DPD Timing vs BGP Hold Timer

The DPD timer hierarchy ensures IPsec detects overlay failures before BGP:

```
DPD keepalive:        30s
DPD timeout:         150s  ← IPsec declares peer dead
BGP keepalive:        60s
BGP hold time:       180s  ← BGP declares session down
```

If a remote CE becomes unreachable, DPD detects it at 150s and tears down
the IPsec SA. BGP follows 30s later when its hold timer expires. This
ordering is intentional: the telemetry model learns the characteristic
gap between underlay failure detection and routing convergence.

### Rekeying Behavior

- IKE SA rekeys every 24h IKE → ESP SAs re-key in that 24h window
- ESP CHILD SA rekeys every 4h (or after 4GB of traffic, whichever first)
- `rekeyfuzz=100%` randomizes the rekey start time within the margin window
  to prevent simultaneous rekeys from all SAs on rekey collision
- Each rekey increments the SA instance counter (`vti100[1]` → `vti100[2]`)
- SPI values change on each rekey; the telemetry collector detects SPI
  changes and logs a rekey event

## Tunnel Health Monitoring

Each CE container runs `tunnel_health.sh` as a background daemon. It polls
every 30 seconds and logs events to syslog (facility `daemon`, tag
`ipsec-health`):

| Event | Severity | Trigger |
|-------|----------|---------|
| SA established | info | SA count increases |
| SA lost (DPD) | warning | SA count decreases |
| VTI unreachable | warning | ICMP probe over VTI fails |
| ESP SPI change | info | XFRM state SPI changes |

The telemetry collector's syslog server parses these events into the
`ipsec` category for structured CSV output.

## Telemetry

### Prometheus Metrics

| Metric | Labels | Description |
|--------|--------|-------------|
| `netoracle_ipsec_sa_state` | device, connection, remote, state | 1=ESTABLISHED, 0=DOWN |
| `netoracle_ipsec_sa_uptime_seconds` | device, connection, remote | IKE SA age in seconds |
| `netoracle_ipsec_esp_bytes_in` | device, connection | ESP bytes received (this SA) |
| `netoracle_ipsec_esp_bytes_out` | device, connection | ESP bytes sent (this SA) |
| `netoracle_ipsec_rekey_total` | device, connection | Rekey events detected (SPI change) |
| `netoracle_ipsec_sa_id` | device, connection | SA instance counter (increments on rekey) |
| `netoracle_ipsec_esp_spi_in` | device, connection | Inbound ESP SPI (hex, decoded) |
| `netoracle_ipsec_esp_spi_out` | device, connection | Outbound ESP SPI (hex, decoded) |
| `netoracle_xfrm_bytes` | device, dst, spi | Kernel XFRM state byte counter |
| `netoracle_xfrm_packets` | device, dst, spi | Kernel XFRM state packet counter |

### CSV Schema (`telemetry_data/ipsec_sa.csv`)

The IKE SA poller writes 28 fields per row, covering IKE SA state, CHILD SA
ESP parameters, and XFRM kernel statistics. See `docs/schema.md` for the
full schema.

## Fault Injection

### IPsec-Specific Scenarios

| Scenario | Description | Primitives Used |
|----------|------------|-----------------|
| `ipsec_sa_flap` | Flap individual SAs on hub-ce1 | ipsec_sa_down, ipsec_sa_up |
| `strongswan_restart` | Kill/restart strongSwan (all SAs drop) | ipsec_stop, ipsec_start |
| `vti_interface_flap` | Bring VTI down/up | vti_down, vti_up |

### Fault Primitives

| Primitive | Effect |
|-----------|--------|
| `ipsec_sa_down(container, conn)` | Terminates a single IKE SA |
| `ipsec_sa_up(container, conn)` | Initiates a single IKE SA |
| `ipsec_rekey(container, conn)` | Forces SA rekey |
| `ipsec_stop(container)` | Stops strongSwan (all SAs drop) |
| `ipsec_start(container)` | Starts strongSwan |
| `vti_interface_down(container, vti)` | VTI interface admin-down |
| `vti_interface_up(container, vti)` | VTI interface admin-up |

## Verification

```bash
# Check IPsec SAs established
docker exec hub-ce1 ipsec status

# Check VTI interfaces
docker exec hub-ce1 ip link show type vti

# Check BGP over VTI
docker exec hub-ce1 vtysh -c 'show bgp summary'

# Verify overlay reachability
docker exec branch1-ce1 ping -c 3 10.99.0.0
docker exec branch1-ce1 ping -c 3 172.16.2.1

# Check telemetry
curl http://192.168.100.100:8000/metrics | grep ipsec
```

## Key Design Decisions

1. **VTI over XFRM interfaces**: VTI (`ip tunnel add ... mode vti`) is more
   widely supported in Linux kernels than XFRM interfaces and provides a
   simpler configuration model where the VTI key corresponds to the IPsec SA mark.

2. **Loopback-based tunnel endpoints**: CE loopback IPs (10.0.x.x) are used as
   IPsec outer endpoints rather than PE-facing link IPs. This makes IPsec
   independent of any single underlay link, allowing multi-path and failover.

3. **PSK authentication**: Pre-shared keys are used for simplicity in this lab
   environment. Production deployments should use certificate-based
   authentication (EAP-TLS or similar).

4. **IKEv2 with AES256-SHA256**: Modern cryptographic profile suitable for
   classified-grade security. The lab uses modp2048 DH group.

5. **strongSwan in CE container**: Rather than sidecar containers, strongSwan
   runs inside the FRR container. This simplifies routing and avoids multi-container
   orchestration complexity while maintaining process isolation.
