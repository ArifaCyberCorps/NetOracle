import asyncio
import logging
import time
from collections import defaultdict

log = logging.getLogger("prometheus-exporter")


class PrometheusMetrics:
    def __init__(self):
        self._latest = {}
        self._lock = asyncio.Lock()

    async def update(self, metric_type: str, rows: list):
        async with self._lock:
            self._latest[metric_type] = rows

    async def render_metrics(self) -> str:
        async with self._lock:
            lines = [
                "# HELP netoracle_telemetry Network telemetry from NetOracle lab",
                "# TYPE netoracle_telemetry untyped",
            ]
            for mtype, rows in self._latest.items():
                handler = getattr(self, f"_format_{mtype}", None)
                if handler:
                    lines.extend(handler(rows))
            lines.append("# EOF")
            return "\n".join(lines)

    def _sanitize(self, name: str) -> str:
        return name.replace("-", "_").replace(".", "_").replace(" ", "_")

    def _labels(self, **kw) -> str:
        parts = [f'{k}="{v}"' for k, v in kw.items() if v is not None and v != ""]
        return "{" + ",".join(parts) + "}"

    def _format_interface_counters(self, rows: list) -> list:
        out = []
        for r in rows:
            dev = self._sanitize(r["device"])
            iface = self._sanitize(r["interface"])
            ts = r.get("timestamp", 0)
            base = f"netoracle_iface{self._labels(device=dev, interface=iface)}"
            out.append(f"{base}_rx_bytes {r.get('rx_bytes', 0)} {ts}")
            out.append(f"{base}_rx_packets {r.get('rx_packets', 0)} {ts}")
            out.append(f"{base}_rx_errors {r.get('rx_errors', 0)} {ts}")
            out.append(f"{base}_rx_drops {r.get('rx_drops', 0)} {ts}")
            out.append(f"{base}_tx_bytes {r.get('tx_bytes', 0)} {ts}")
            out.append(f"{base}_tx_packets {r.get('tx_packets', 0)} {ts}")
            out.append(f"{base}_tx_errors {r.get('tx_errors', 0)} {ts}")
            out.append(f"{base}_tx_drops {r.get('tx_drops', 0)} {ts}")
            out.append(f"{base}_rx_bps {r.get('rx_bps', 0)} {ts}")
            out.append(f"{base}_tx_bps {r.get('tx_bps', 0)} {ts}")
        return out

    def _format_bgp_events(self, rows: list) -> list:
        out = []
        for r in rows:
            dev = self._sanitize(r["device"])
            nbr = self._sanitize(r["neighbor"])
            tbl = self._sanitize(r.get("table", ""))
            ts = r.get("timestamp", 0)
            state = r.get("state", "Idle")
            state_val = 1 if state == "Established" else 0
            lbl = self._labels(device=dev, neighbor=nbr, table=tbl, state=state)
            out.append(f"netoracle_bgp_state{lbl} {state_val} {ts}")
            out.append(f"netoracle_bgp_prefixes{lbl} {r.get('prefixes_received', 0)} {ts}")
        return out

    def _format_ospf_events(self, rows: list) -> list:
        out = []
        state_map = {"Full": 1, "Init": 0.5, "ExStart": 0.3, "Loading": 0.4,
                     "2Way": 0.8, "Down": 0}
        for r in rows:
            dev = self._sanitize(r["device"])
            nid = self._sanitize(r.get("neighbor_id", ""))
            iface = self._sanitize(r.get("interface", ""))
            state = r.get("state", "Down")
            ts = r.get("timestamp", 0)
            sv = state_map.get(state, 0)
            lbl = self._labels(device=dev, neighbor=nid, interface=iface, state=state)
            out.append(f"netoracle_ospf_state{lbl} {sv} {ts}")
        return out

    def _format_mpls_stats(self, rows: list) -> list:
        out = []
        for r in rows:
            dev = self._sanitize(r["device"])
            prefix = self._sanitize(r.get("prefix", ""))
            iface = self._sanitize(r.get("interface", ""))
            ts = r.get("timestamp", 0)
            lbl = self._labels(device=dev, prefix=prefix, interface=iface)
            out.append(f"netoracle_mpls_label_count{lbl} {r.get('label', 0)} {ts}")
        return out

    def _format_tunnel_stats(self, rows: list) -> list:
        out = []
        for r in rows:
            dev = self._sanitize(r["device"])
            tun = self._sanitize(r.get("tunnel", ""))
            ts = r.get("timestamp", 0)
            base = f"netoracle_tunnel{self._labels(device=dev, tunnel=tun)}"
            out.append(f"{base}_rx_bytes {r.get('rx_bytes', 0)} {ts}")
            out.append(f"{base}_rx_packets {r.get('rx_packets', 0)} {ts}")
            out.append(f"{base}_rx_errors {r.get('rx_errors', 0)} {ts}")
            out.append(f"{base}_tx_bytes {r.get('tx_bytes', 0)} {ts}")
            out.append(f"{base}_tx_packets {r.get('tx_packets', 0)} {ts}")
            out.append(f"{base}_tx_errors {r.get('tx_errors', 0)} {ts}")
        return out

    def _format_ipsec_sa(self, rows: list) -> list:
        out = []
        seen = set()
        for r in rows:
            dev = self._sanitize(r["device"])
            conn = self._sanitize(r.get("connection", ""))
            if not conn:
                continue  # skip XFRM rows (handled separately)
            remote = self._sanitize(r.get("remote_ip", ""))
            s = r.get("state_str", "unknown")
            ts = r.get("timestamp", 0)
            lbl = self._labels(device=dev, connection=conn, remote=remote, state=s)

            # IKE SA state (1=ESTABLISHED, 0.5=CONNECTING, 0=DOWN)
            out.append(f"netoracle_ipsec_sa_state{lbl} {r.get('state', 0)} {ts}")

            # SA uptime in seconds
            out.append(f"netoracle_ipsec_sa_uptime_seconds{lbl} {r.get('uptime_sec', 0)} {ts}")

            # ESP bytes in/out (per SA, updated at each poll)
            lbl_b = self._labels(device=dev, connection=conn, remote=remote)
            esp_in = r.get("esp_bytes_in", 0)
            esp_out = r.get("esp_bytes_out", 0)
            out.append(f"netoracle_ipsec_esp_bytes_in{lbl_b} {esp_in} {ts}")
            out.append(f"netoracle_ipsec_esp_bytes_out{lbl_b} {esp_out} {ts}")

            # Rekey detection (1 if SPI changed since last poll)
            rekey = r.get("rekey_count", 0)
            out.append(f"netoracle_ipsec_rekey_total{lbl_b} {rekey} {ts}")

            # SA ID (instance counter, increments on rekey)
            sa_id = r.get("sa_id", "0")
            out.append(f"netoracle_ipsec_sa_id{lbl_b} {sa_id} {ts}")

            # ESP SPI values (hex, indicates rekey)
            spi_in = r.get("esp_spi_in", "0")
            spi_out = r.get("esp_spi_out", "0")
            try:
                spi_in_int = int(spi_in, 16) if spi_in else 0
                spi_out_int = int(spi_out, 16) if spi_out else 0
            except ValueError:
                spi_in_int = 0
                spi_out_int = 0
            out.append(f"netoracle_ipsec_esp_spi_in{lbl_b} {spi_in_int} {ts}")
            out.append(f"netoracle_ipsec_esp_spi_out{lbl_b} {spi_out_int} {ts}")

        # XFRM state metrics (kernel-level SA stats)
        for r in rows:
            if r.get("xfrm_spi"):
                dev = self._sanitize(r["device"])
                dst = self._sanitize(r.get("xfrm_dst", ""))
                spi = r.get("xfrm_spi", "0")
                ts = r.get("timestamp", 0)
                lbl_x = self._labels(device=dev, dst=dst, spi=spi)
                out.append(f"netoracle_xfrm_bytes{lbl_x} {r.get('xfrm_bytes', 0)} {ts}")
                out.append(f"netoracle_xfrm_packets{lbl_x} {r.get('xfrm_packets', 0)} {ts}")
        return out

    def _format_cpu_memory(self, rows: list) -> list:
        out = []
        for r in rows:
            dev = self._sanitize(r["device"])
            ts = r.get("timestamp", 0)
            lbl = self._labels(device=dev)
            out.append(f"netoracle_mem_used_pct{lbl} {r.get('mem_used_pct', 0)} {ts}")
            out.append(f"netoracle_mem_total_kb{lbl} {r.get('mem_total_kb', 0)} {ts}")
            out.append(f"netoracle_mem_available_kb{lbl} {r.get('mem_available_kb', 0)} {ts}")
            out.append(f"netoracle_load_1m{lbl} {r.get('load_1m', 0)} {ts}")
            out.append(f"netoracle_load_5m{lbl} {r.get('load_5m', 0)} {ts}")
            out.append(f"netoracle_load_15m{lbl} {r.get('load_15m', 0)} {ts}")
            out.append(f"netoracle_uptime_seconds{lbl} {r.get('uptime_sec', 0)} {ts}")
        return out

    async def health(self) -> str:
        async with self._lock:
            mtypes = list(self._latest.keys())
        return f'{{"status":"ok","metric_types":{mtypes},"timestamp":{time.time()}}}'


class PrometheusHTTPServer:
    def __init__(self, metrics: PrometheusMetrics, host="0.0.0.0", port=8000):
        self.metrics = metrics
        self.host = host
        self.port = port
        self._server = None

    async def start(self):
        loop = asyncio.get_event_loop()
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        log.info(f"Prometheus metrics endpoint: http://{self.host}:{self.port}/metrics")

    async def _handle_client(self, reader, writer):
        request = await reader.read(4096)
        req_line = request.split(b"\r\n")[0].decode(errors="replace")
        if "/metrics" in req_line:
            body = await self.metrics.render_metrics()
            resp = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/plain; version=0.0.4\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
                f"{body}"
            )
        elif "/health" in req_line:
            body = await self.metrics.health()
            resp = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
                f"{body}"
            )
        else:
            resp = "HTTP/1.1 404 Not Found\r\n\r\n"
        writer.write(resp.encode())
        await writer.drain()
        writer.close()

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
