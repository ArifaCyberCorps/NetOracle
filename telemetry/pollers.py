import asyncio
import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("telemetry-pollers")


class DockerExecutor:
    async def execute(self, container: str, *cmd_parts) -> str:
        cmd = ["docker", "exec", container] + list(cmd_parts)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode != 0:
                log.debug(f"docker exec {container} failed: {stderr.decode()[:200]}")
                return ""
            return stdout.decode(errors="replace")
        except asyncio.TimeoutError:
            log.warning(f"Timeout executing on {container}: {' '.join(cmd_parts[:2])}")
            return ""
        except Exception as e:
            log.warning(f"Error executing on {container}: {e}")
            return ""

    async def vtysh(self, container: str, command: str) -> str:
        return await self.execute(container, "vtysh", "-c", command)


class InterfaceCountersPoller:
    def __init__(self, executor: DockerExecutor):
        self.executor = executor
        self._prev_counters = {}

    async def poll(self, container: str) -> list:
        output = await self.executor.vtysh(container, "show interface json")
        if not output:
            return await self._poll_via_ip(container)
        try:
            iface_data = json.loads(output)
            return self._parse_interface_json(container, iface_data)
        except json.JSONDecodeError:
            return await self._poll_via_ip(container)

    def _parse_interface_json(self, container: str, data: list) -> list:
        rows = []
        now = time.time()
        for iface in data if isinstance(data, list) else [data]:
            name = iface.get("name", "unknown")
            if name == "lo" or "tun" in name:
                continue
            stats = iface.get("link-interfaces", [{}])[0] if iface.get("link-interfaces") else {}
            stats2 = iface if not stats else stats
            rx_bytes = int(stats2.get("input-bytes", 0))
            rx_packets = int(stats2.get("input-packets", 0))
            rx_errors = int(stats2.get("input-errors", 0))
            rx_drops = int(stats2.get("input-drops", stats2.get("input-errors", 0)))
            tx_bytes = int(stats2.get("output-bytes", 0))
            tx_packets = int(stats2.get("output-packets", 0))
            tx_errors = int(stats2.get("output-errors", 0))
            tx_drops = int(stats2.get("output-drops", stats2.get("output-errors", 0)))
            speed = int(iface.get("bandwidth", iface.get("speed", 1000)))
            mtu = int(iface.get("mtu", 1500))
            key = f"{container}:{name}"
            rate_k = self._rate_interface[name] if hasattr(self, '_rate_interface') else {}
            prev = self._prev_counters.get(key, {})
            interval = now - prev.get("time", now) or 1
            rx_bps = int((rx_bytes - prev.get("rx_bytes", rx_bytes)) * 8 / interval) if prev else 0
            tx_bps = int((tx_bytes - prev.get("tx_bytes", tx_bytes)) * 8 / interval) if prev else 0
            self._prev_counters[key] = {
                "time": now, "rx_bytes": rx_bytes, "tx_bytes": tx_bytes
            }
            rows.append({
                "timestamp": now, "device": container, "interface": name,
                "rx_bytes": rx_bytes, "rx_packets": rx_packets,
                "rx_errors": rx_errors, "rx_drops": rx_drops,
                "tx_bytes": tx_bytes, "tx_packets": tx_packets,
                "tx_errors": tx_errors, "tx_drops": tx_drops,
                "rx_bps": rx_bps, "tx_bps": tx_bps,
                "speed_mbps": speed, "mtu": mtu, "status": iface.get("ifindex", "") if "" else "up",
            })
        return rows

    async def _poll_via_ip(self, container: str) -> list:
        output = await self.executor.execute(container, "ip", "-s", "link")
        rows = []
        now = time.time()
        blocks = re.split(r'\n\s*', output)
        name = "unknown"
        for line in blocks:
            m = re.match(r'\d+:\s+(\S+):\s+.*state\s+(\S+)', line)
            if m:
                name = m.group(1).replace("@", "").split("@")[0]
                continue
            if "RX:" in line or "TX:" in line:
                continue
            parts = line.strip().split()
            if len(parts) >= 8 and parts[0].isdigit():
                rx_bytes, rx_packets, rx_errors, rx_drops = parts[0], parts[1], parts[3], parts[4]
                continue
            if len(parts) >= 8 and not parts[0].isdigit():
                continue
        raw = output.split("\n")
        for i, line in enumerate(raw):
            m = re.match(r'\d+:\s+(\S+):\s+<.*>.*state\s+(\S+)', line)
            if not m:
                continue
            name = m.group(1).split("@")[0].replace("@", "")
            if name == "lo":
                continue
            rx_data = ""
            tx_data = ""
            for j in range(i + 1, min(i + 5, len(raw))):
                if "RX:" in raw[j]:
                    rx_data = raw[j + 1] if j + 1 < len(raw) else ""
                if "TX:" in raw[j]:
                    tx_data = raw[j + 1] if j + 1 < len(raw) else ""
            rx_parts = rx_data.strip().split()
            tx_parts = tx_data.strip().split()
            if len(rx_parts) >= 4 and len(tx_parts) >= 4:
                try:
                    row = {
                        "timestamp": now, "device": container, "interface": name,
                        "rx_bytes": int(rx_parts[0]), "rx_packets": int(rx_parts[1]),
                        "rx_errors": int(rx_parts[2]), "rx_drops": int(rx_parts[3]),
                        "tx_bytes": int(tx_parts[0]), "tx_packets": int(tx_parts[1]),
                        "tx_errors": int(tx_parts[2]), "tx_drops": int(tx_parts[3]),
                        "rx_bps": 0, "tx_bps": 0,
                        "speed_mbps": 1000, "mtu": 1500, "status": m.group(2) if m else "up",
                    }
                    key = f"{container}:{name}"
                    prev = self._prev_counters.get(key, {})
                    interval = now - prev.get("time", now) or 1
                    if prev:
                        row["rx_bps"] = int((row["rx_bytes"] - prev.get("rx_bytes", row["rx_bytes"])) * 8 / interval)
                        row["tx_bps"] = int((row["tx_bytes"] - prev.get("tx_bytes", row["tx_bytes"])) * 8 / interval)
                    self._prev_counters[key] = {"time": now, "rx_bytes": row["rx_bytes"], "tx_bytes": row["tx_bytes"]}
                    rows.append(row)
                except (ValueError, IndexError):
                    pass
        return rows


class BgpStatusPoller:
    def __init__(self, executor: DockerExecutor):
        self.executor = executor

    async def poll(self, container: str, role: str) -> list:
        rows = []
        if role in ("pe",):
            rows.extend(await self._parse_bgp_summary(container, "show bgp vpnv4 unicast summary json", "vpnv4"))
            vrf_output = await self.executor.vtysh(container, "show bgp vrf all summary json")
            if vrf_output:
                try:
                    vrf_data = json.loads(vrf_output)
                    for vrf_name, vrf_info in vrf_data.items():
                        if isinstance(vrf_info, dict):
                            rows.extend(self._parse_peer_rows(container, vrf_info, f"vrf_{vrf_name}"))
                except json.JSONDecodeError:
                    pass
        if role in ("ce",):
            rows.extend(await self._parse_bgp_summary(container, "show bgp summary json", "overlay"))
        return rows

    async def _parse_bgp_summary(self, container: str, cmd: str, table: str) -> list:
        output = await self.executor.vtysh(container, cmd)
        if not output:
            return []
        try:
            data = json.loads(output)
            return self._parse_peer_rows(container, data, table)
        except json.JSONDecodeError:
            return self._parse_bgp_text(container, output, table)

    def _parse_peer_rows(self, container: str, data: dict, table: str) -> list:
        rows = []
        now = time.time()
        peers = data.get("peers", data.get("ipv4Unicast", {}).get("peers", {})) if isinstance(data, dict) else {}
        if not peers:
            for k in ("peers", "neighbors"):
                if isinstance(data.get(k), dict):
                    peers = data[k]
                    break
        for peer, info in peers.items() if isinstance(peers, dict) else []:
            if isinstance(info, str):
                continue
            state = info.get("state", info.get("bgpState", info.get("status", "Idle")))
            prefixes = info.get("pfxRcd", info.get("prefixesReceived", info.get("prefixes", 0)))
            uptime = info.get("uptime", info.get("peerUptimeMsec", 0))
            remote_as = info.get("remoteAs", info.get("asn", 0))
            rows.append({
                "timestamp": now, "device": container, "table": table,
                "neighbor": peer, "remote_as": str(remote_as),
                "state": state, "prefixes_received": int(prefixes) if prefixes else 0,
                "uptime_sec": int(uptime) if uptime and str(uptime).isdigit() else 0,
            })
        return rows

    def _parse_bgp_text(self, container: str, output: str, table: str) -> list:
        rows = []
        now = time.time()
        for line in output.split("\n"):
            m = re.match(
                r'^([>\*]?[\w\.:]+)\s+(\d+)\s+([\d]+)\s+([\d]+)\s+([\d]+)\s+(\w+)\s+([\w\/]+)?\s*(.*)',
                line
            )
            if not m:
                m = re.match(r'^([\d\.]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\w+)', line)
            if m:
                neighbor = m.group(1).lstrip(" *>")
                state = m.group(len(m.groups()))
                if state in ("Idle", "Active", "Connect", "OpenSent", "OpenConfirm", "Established"):
                    rows.append({
                        "timestamp": now, "device": container, "table": table,
                        "neighbor": neighbor, "remote_as": "0",
                        "state": state, "prefixes_received": 0, "uptime_sec": 0,
                    })
        return rows


class OspfNeighborPoller:
    def __init__(self, executor: DockerExecutor):
        self.executor = executor

    async def poll(self, container: str) -> list:
        output = await self.executor.vtysh(container, "show ip ospf neighbor json")
        if not output:
            return []
        try:
            data = json.loads(output)
            return self._parse_ospf_json(container, data)
        except json.JSONDecodeError:
            return self._parse_ospf_text(container, output)

    def _parse_ospf_json(self, container: str, data: dict) -> list:
        rows = []
        now = time.time()
        neighbors = data.get("neighbors", [])
        if isinstance(neighbors, list):
            for n in neighbors:
                rows.append({
                    "timestamp": now, "device": container,
                    "neighbor_id": n.get("neighborId", n.get("neighbor-id", "")),
                    "interface": n.get("ifaceName", n.get("interface", "")),
                    "state": n.get("state", n.get("nbrState", "")),
                    "priority": int(n.get("priority", 1)),
                    "dr": n.get("dr", ""),
                    "bdr": n.get("bdr", ""),
                    "uptime_sec": int(n.get("uptime", n.get("uptimeMsec", 0))),
                })
        return rows

    def _parse_ospf_text(self, container: str, output: str) -> list:
        rows = []
        now = time.time()
        for line in output.split("\n"):
            m = re.match(r'^([\d\.]+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+([\d\.]+)\s+(\S+)', line)
            if m:
                rows.append({
                    "timestamp": now, "device": container,
                    "neighbor_id": m.group(1),
                    "priority": int(m.group(2)),
                    "state": m.group(3),
                    "interface": m.group(6),
                    "dr": "", "bdr": "", "uptime_sec": 0,
                })
        return rows


class MplsLdpPoller:
    def __init__(self, executor: DockerExecutor):
        self.executor = executor

    async def poll(self, container: str) -> list:
        output = await self.executor.vtysh(container, "show mpls ldp binding")
        rows = []
        now = time.time()
        for line in output.split("\n"):
            m = re.match(
                r'\s*(\d+:\d+:\d+\.\d+/\d+)\s+(\d+)\s+(\S+)\s+(\S+)',
                line
            )
            if m:
                rows.append({
                    "timestamp": now, "device": container,
                    "prefix": m.group(1),
                    "label": int(m.group(2)),
                    "label_type": m.group(3),
                    "interface": m.group(4),
                })
        if not rows:
            alt = re.findall(r'(\S+\s+\d+\s+\S+)', output)
            for a in alt[:5]:
                rows.append({
                    "timestamp": now, "device": container,
                    "prefix": a, "label": 0,
                    "label_type": "unknown", "interface": "unknown",
                })
        return rows


class GreTunnelPoller:
    def __init__(self, executor: DockerExecutor):
        self.executor = executor

    async def poll(self, container: str) -> list:
        output = await self.executor.execute(container, "ip", "-s", "link", "show", "type", "gre")
        rows = []
        now = time.time()
        blocks = output.split("\n")
        current_tunnel = ""
        current_rx = {}
        current_tx = {}
        for line in blocks:
            m = re.match(r'\d+:\s+(\S+)[@:].*state\s+(\S+)', line)
            if m:
                current_tunnel = m.group(1).split("@")[0]
                continue
            if "RX:" in line:
                continue
            if "TX:" in line:
                continue
            parts = line.strip().split()
            if len(parts) >= 4 and parts[0].isdigit():
                if current_tunnel and "rx_bytes" not in current_rx:
                    current_rx = {"bytes": parts[0], "packets": parts[1], "errors": parts[2], "dropped": parts[3]}
                elif current_tunnel:
                    current_tx = {"bytes": parts[0], "packets": parts[1], "errors": parts[2], "dropped": parts[3]}
                    rows.append({
                        "timestamp": now, "device": container,
                        "tunnel": current_tunnel,
                        "rx_bytes": int(current_rx.get("bytes", 0)),
                        "rx_packets": int(current_rx.get("packets", 0)),
                        "rx_errors": int(current_rx.get("errors", 0)),
                        "rx_dropped": int(current_rx.get("dropped", 0)),
                        "tx_bytes": int(current_tx.get("bytes", 0)),
                        "tx_packets": int(current_tx.get("packets", 0)),
                        "tx_errors": int(current_tx.get("errors", 0)),
                        "tx_dropped": int(current_tx.get("dropped", 0)),
                    })
                    current_rx = {}
                    current_tx = {}
        return rows


class IkeSaPoller:
    """
    Polls IKE SA / IPsec SA health via strongSwan statusall + xfrm kernel interface.
    Extracts SD-WAN IPsec features:
      - IKE SA state (ESTABLISHED / CONNECTING / DOWN)
      - DPD status (implicit from SA aliveness)
      - SA uptime and remaining lifetime before rekey
      - Rekey count (SPI changes indicate rekey events)
      - ESP bytes/packets in/out per SA
      - XFRM state statistics from kernel
    """
    def __init__(self, executor: DockerExecutor):
        self.executor = executor
        self._prev_spis = {}

    async def poll(self, container: str) -> list:
        rows = []
        now = time.time()

        # Poll strongSwan SA status with verbose details
        status_out = await self.executor.execute(container, "ipsec", "statusall")
        if status_out:
            rows.extend(self._parse_ipsec_statusall(container, status_out, now))

        # Poll Linux XFRM state statistics per SA
        xfrm_out = await self.executor.execute(container, "ip", "-s", "xfrm", "state")
        if xfrm_out:
            rows.extend(self._parse_xfrm_state(container, xfrm_out, now))

        return rows

    def _parse_ipsec_statusall(self, container: str, output: str, now: float) -> list:
        rows = []
        current_conn = ""
        sa_age_sec = 0
        sa_lifetime_remaining = ""
        esp_bytes_in = 0
        esp_bytes_out = 0
        esp_spi_i = ""
        esp_spi_o = ""
        ike_spi = ""

        for line in output.split("\n"):
            stripped = line.strip()

            # Match IKE SA header: "vti100[1]: ESTABLISHED 10 minutes ago, ..."
            m = re.match(
                r'^(\S+)\[(\d+)\]:\s+(ESTABLISHED|CONNECTING|CREATED)\s+'
                r'(?:(\d+)\s+(minutes?|hours?|seconds?|days?)\s+ago)?.*?'
                r'(\d+\.\d+\.\d+\.\d+)\[(\S+)\].*?(\d+\.\d+\.\d+\.\d+)\[(\S+)\]',
                stripped
            )
            if m:
                current_conn = m.group(1).strip("{}[]")
                sa_id = m.group(2)  # unique SA instance ID
                state_str = m.group(3)
                duration_val = m.group(4)
                duration_unit = m.group(5)
                local_ip = m.group(6)
                local_id = m.group(7)
                remote_ip = m.group(8)
                remote_id = m.group(9)

                # Parse SA age
                if duration_val and duration_unit:
                    mul = 1
                    if duration_unit.startswith("minute"):
                        mul = 60
                    elif duration_unit.startswith("hour"):
                        mul = 3600
                    elif duration_unit.startswith("day"):
                        mul = 86400
                    sa_age_sec = int(duration_val) * mul

                state_val = 1 if state_str == "ESTABLISHED" else 0.5 if state_str == "CONNECTING" else 0

                # Detect rekey: if SPI changed since last poll, increment rekey count
                ike_key = f"{container}:{current_conn}"
                prev_spi = self._prev_spis.get(ike_key, "")
                rekey_detected = 1 if (prev_spi and prev_spi != ike_spi) else 0
                self._prev_spis[ike_key] = ike_spi

                rows.append({
                    "timestamp": now, "device": container,
                    "connection": current_conn,
                    "local_ip": local_ip,
                    "remote_ip": remote_ip,
                    "local_id": local_id,
                    "remote_id": remote_id,
                    "state": state_val,
                    "state_str": state_str,
                    "sa_id": sa_id,
                    "uptime_sec": sa_age_sec,
                    "rekey_count": rekey_detected,
                    "lifetime_remaining": "",
                    "esp_bytes_in": 0,
                    "esp_bytes_out": 0,
                    "esp_spi_in": "",
                    "esp_spi_out": "",
                    "ike_spi": ike_spi,
                })
                continue

            # Match IKE SA rekey info: "rekeying in 23 hours"
            m_rekey = re.match(r'.*rekeying\s+in\s+(\d+)\s+(\w+)', stripped)
            if m_rekey and rows:
                rows[-1]["lifetime_remaining"] = f"{m_rekey.group(1)} {m_rekey.group(2)}"
                continue

            # Match CHILD SA (ESP): "vti100{1}:  INSTALLED, TUNNEL, ESP"
            m_child = re.match(r'^\s*(\S+)\{(\d+)\}:\s+(INSTALLED|REKEYED|DESTROYING)', stripped)
            if m_child:
                child_conn = m_child.group(1).strip("{}[]")
                child_sa_id = m_child.group(2)
                child_state = m_child.group(3)
                # If this is an ESP child SA, associate with the current IKE SA
                if rows and child_conn == current_conn:
                    rows[-1]["child_sa_id"] = child_sa_id
                    rows[-1]["child_state"] = child_state
                continue

            # Match ESP SPI and byte counters:
            # "ESP SPIs: c1234567_i d890abcd_o"
            m_esp_spi = re.match(r'.*ESP SPIs:\s+(\w+)_i\s+(\w+)_o', stripped)
            if m_esp_spi and rows:
                rows[-1]["esp_spi_in"] = m_esp_spi.group(1)
                rows[-1]["esp_spi_out"] = m_esp_spi.group(2)
                continue

            # "AES_CBC_256/HMAC_SHA2_256_128, 3600s, 123456 bytes_i, 789012 bytes_o"
            m_esp_bytes = re.match(
                r'.*,\s+(\d+)s?,\s+(\d+)\s+bytes_i,\s+(\d+)\s+bytes_o',
                stripped
            )
            if m_esp_bytes and rows:
                rows[-1]["esp_lifetime_sec"] = int(m_esp_bytes.group(1))
                rows[-1]["esp_bytes_in"] = int(m_esp_bytes.group(2))
                rows[-1]["esp_bytes_out"] = int(m_esp_bytes.group(3))
                continue

            # Match the "Security Associations" summary line for DPD status
            m_summary = re.match(
                r'Security Associations\s+\((\d+)\s+up,?\s*(\d+)?\s*(connecting|down)?',
                stripped
            )
            if m_summary:
                # This is a global summary, not per-connection
                continue

        return rows

    def _parse_xfrm_state(self, container: str, output: str, now: float) -> list:
        rows = []
        blocks = output.strip().split("\n\n")
        for block in blocks:
            lines = block.strip().split("\n")
            src = dst = ""
            spi = ""
            enc_bytes = 0
            enc_packets = 0
            add_time = 0
            used_time = 0
            in_stats = False

            for line in lines:
                sm = re.search(r'src\s+([\d\.]+)\s+dst\s+([\d\.]+)', line)
                if sm:
                    src, dst = sm.group(1), sm.group(2)
                spi_m = re.search(r'spi\s+([\da-f]+)\(', line)
                if spi_m:
                    spi = spi_m.group(1)
                if "statistics:" in line:
                    in_stats = True
                    continue
                if in_stats:
                    parts = line.strip().split()
                    if len(parts) >= 4 and parts[0].isdigit():
                        enc_bytes = int(parts[0])
                        enc_packets = int(parts[1])
                        add_time_str = parts[2] if len(parts) > 2 else "0"
                        used_time_str = parts[3] if len(parts) > 3 else "0"
                        try:
                            add_time = int(add_time_str)
                            used_time = int(used_time_str)
                        except ValueError:
                            pass
                        # XFRM SA entry complete
                        rows.append({
                            "timestamp": now, "device": container,
                            "connection": "",
                            "state": 1,
                            "state_str": "xfrm_installed",
                            "remote_ip": dst,
                            "uptime_sec": 0,
                            "xfrm_src": src,
                            "xfrm_dst": dst,
                            "xfrm_spi": spi,
                            "xfrm_bytes": enc_bytes,
                            "xfrm_packets": enc_packets,
                            "xfrm_add_time": add_time,
                            "xfrm_used_time": used_time,
                        })
                        in_stats = False
        return rows


class CpuMemoryPoller:
    def __init__(self, executor: DockerExecutor):
        self.executor = executor

    async def poll(self, container: str) -> list:
        rows = []
        now = time.time()
        meminfo = await self.executor.execute(container, "cat", "/proc/meminfo")
        uptime = await self.executor.execute(container, "cat", "/proc/uptime")
        load = await self.executor.execute(container, "cat", "/proc/loadavg")
        lines = meminfo.split("\n")
        mem = {}
        for line in lines:
            m = re.match(r'(\w+):\s+(\d+)', line)
            if m:
                mem[m.group(1)] = int(m.group(2))
        total = mem.get("MemTotal", 0)
        available = mem.get("MemAvailable", mem.get("MemFree", 0))
        used = total - available if total else 0
        load_parts = load.strip().split() if load else ["0", "0", "0"]
        up = uptime.strip().split() if uptime else ["0"]
        rows.append({
            "timestamp": now, "device": container,
            "mem_total_kb": total,
            "mem_used_kb": used,
            "mem_available_kb": available,
            "mem_used_pct": round(used / total * 100, 1) if total else 0,
            "load_1m": float(load_parts[0]) if load_parts else 0,
            "load_5m": float(load_parts[1]) if len(load_parts) > 1 else 0,
            "load_15m": float(load_parts[2]) if len(load_parts) > 2 else 0,
            "uptime_sec": float(up[0]) if up else 0,
        })
        return rows
