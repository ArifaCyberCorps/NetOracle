#!/usr/bin/env python3
import asyncio
import csv
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from telemetry.pollers import (
    DockerExecutor, InterfaceCountersPoller, BgpStatusPoller,
    OspfNeighborPoller, MplsLdpPoller, GreTunnelPoller, IkeSaPoller,
    CpuMemoryPoller,
)
from telemetry.syslog_server import SyslogServer
from telemetry.prometheus_exporter import PrometheusMetrics, PrometheusHTTPServer

log = logging.getLogger("telemetry-collector")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


class TelemetryCollector:
    def __init__(self, config_path: str = "telemetry/config.yaml"):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self.output_dir = Path(self.config["output"]["dir"])
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.executor = DockerExecutor()
        self.interface_poller = InterfaceCountersPoller(self.executor)
        self.bgp_poller = BgpStatusPoller(self.executor)
        self.ospf_poller = OspfNeighborPoller(self.executor)
        self.mpls_poller = MplsLdpPoller(self.executor)
        self.gre_poller = GreTunnelPoller(self.executor)
        self.ike_sa_poller = IkeSaPoller(self.executor)
        self.cpu_poller = CpuMemoryPoller(self.executor)

        self.syslog_server = None
        self._csv_writers = {}
        self._csv_files = {}
        self._running = True
        self._interval_stats = {}

        self.prom_metrics = PrometheusMetrics()
        self.prom_server = PrometheusHTTPServer(self.prom_metrics, "0.0.0.0", 8000)

    def _get_writer(self, name: str, fieldnames: list):
        if name not in self._csv_writers:
            fpath = self.output_dir / f"{name}.csv"
            f = open(fpath, "a", newline="")
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            if fpath.stat().st_size == 0:
                w.writeheader()
            self._csv_files[name] = f
            self._csv_writers[name] = w
        return self._csv_writers[name]

    def _write_rows(self, name: str, fieldnames: list, rows: list):
        if not rows:
            return
        w = self._get_writer(name, fieldnames)
        for row in rows:
            row["timestamp_iso"] = datetime.fromtimestamp(
                row.get("timestamp", time.time()), tz=timezone.utc
            ).isoformat()
            w.writerow(row)
        self._csv_files[name].flush()

    async def poll_interface_counters(self):
        name = "interface_counters"
        fnames = ["timestamp", "timestamp_iso", "device", "interface",
                  "rx_bytes", "rx_packets", "rx_errors", "rx_drops",
                  "tx_bytes", "tx_packets", "tx_errors", "tx_drops",
                  "rx_bps", "tx_bps", "speed_mbps", "mtu", "status"]
        devices = [d["name"] for d in self.config["devices"]]
        while self._running:
            start = time.time()
            all_rows = []
            for dev in devices:
                try:
                    rows = await self.interface_poller.poll(dev)
                    all_rows.extend(rows)
                except Exception as e:
                    log.warning(f"Interface poll failed on {dev}: {e}")
            self._write_rows(name, fnames, all_rows)
            await self.prom_metrics.update(name, all_rows)
            elapsed = time.time() - start
            interval = self.config["poller_intervals"]["interface_counters"]
            await asyncio.sleep(max(0, interval - elapsed))

    async def poll_bgp(self):
        name = "bgp_events"
        fnames = ["timestamp", "timestamp_iso", "device", "table",
                  "neighbor", "remote_as", "state", "prefixes_received", "uptime_sec"]
        while self._running:
            start = time.time()
            all_rows = []
            for dev_cfg in self.config["devices"]:
                dev = dev_cfg["name"]
                role = dev_cfg["role"]
                if role not in ("pe", "ce"):
                    continue
                try:
                    rows = await self.bgp_poller.poll(dev, role)
                    all_rows.extend(rows)
                except Exception as e:
                    log.warning(f"BGP poll failed on {dev}: {e}")
            self._write_rows(name, fnames, all_rows)
            await self.prom_metrics.update(name, all_rows)
            elapsed = time.time() - start
            interval = self.config["poller_intervals"]["bgp_status"]
            await asyncio.sleep(max(0, interval - elapsed))

    async def poll_ospf(self):
        name = "ospf_events"
        fnames = ["timestamp", "timestamp_iso", "device",
                  "neighbor_id", "interface", "state",
                  "priority", "dr", "bdr", "uptime_sec"]
        while self._running:
            start = time.time()
            all_rows = []
            for dev_cfg in self.config["devices"]:
                if dev_cfg["role"] not in ("p", "pe"):
                    continue
                dev = dev_cfg["name"]
                try:
                    rows = await self.ospf_poller.poll(dev)
                    all_rows.extend(rows)
                except Exception as e:
                    log.warning(f"OSPF poll failed on {dev}: {e}")
            self._write_rows(name, fnames, all_rows)
            await self.prom_metrics.update(name, all_rows)
            elapsed = time.time() - start
            interval = self.config["poller_intervals"]["ospf_neighbors"]
            await asyncio.sleep(max(0, interval - elapsed))

    async def poll_mpls(self):
        name = "mpls_stats"
        fnames = ["timestamp", "timestamp_iso", "device",
                  "prefix", "label", "label_type", "interface"]
        while self._running:
            start = time.time()
            all_rows = []
            for dev_cfg in self.config["devices"]:
                if dev_cfg["role"] not in ("p", "pe"):
                    continue
                dev = dev_cfg["name"]
                try:
                    rows = await self.mpls_poller.poll(dev)
                    all_rows.extend(rows)
                except Exception as e:
                    log.warning(f"MPLS poll failed on {dev}: {e}")
            self._write_rows(name, fnames, all_rows)
            await self.prom_metrics.update(name, all_rows)
            elapsed = time.time() - start
            interval = self.config["poller_intervals"]["mpls_ldp"]
            await asyncio.sleep(max(0, interval - elapsed))

    async def poll_gre(self):
        name = "tunnel_stats"
        fnames = ["timestamp", "timestamp_iso", "device", "tunnel",
                  "rx_bytes", "rx_packets", "rx_errors", "rx_dropped",
                  "tx_bytes", "tx_packets", "tx_errors", "tx_dropped"]
        while self._running:
            start = time.time()
            all_rows = []
            for dev_cfg in self.config["devices"]:
                if dev_cfg["role"] != "ce":
                    continue
                dev = dev_cfg["name"]
                try:
                    rows = await self.gre_poller.poll(dev)
                    all_rows.extend(rows)
                except Exception as e:
                    log.warning(f"GRE poll failed on {dev}: {e}")
            self._write_rows(name, fnames, all_rows)
            await self.prom_metrics.update(name, all_rows)
            elapsed = time.time() - start
            interval = self.config["poller_intervals"]["gre_tunnels"]
            await asyncio.sleep(max(0, interval - elapsed))

    async def poll_ipsec(self):
        name = "ipsec_sa"
        fnames = [
            "timestamp", "timestamp_iso", "device",
            "connection", "local_ip", "remote_ip", "local_id", "remote_id",
            "state", "state_str", "sa_id", "uptime_sec",
            "rekey_count", "lifetime_remaining",
            "esp_bytes_in", "esp_bytes_out",
            "esp_spi_in", "esp_spi_out", "esp_lifetime_sec",
            "ike_spi", "child_sa_id", "child_state",
            "xfrm_src", "xfrm_dst", "xfrm_spi",
            "xfrm_bytes", "xfrm_packets", "xfrm_add_time", "xfrm_used_time",
        ]
        while self._running:
            start = time.time()
            all_rows = []
            for dev_cfg in self.config["devices"]:
                if dev_cfg["role"] != "ce":
                    continue
                dev = dev_cfg["name"]
                try:
                    rows = await self.ike_sa_poller.poll(dev)
                    all_rows.extend(rows)
                except Exception as e:
                    log.warning(f"IPsec SA poll failed on {dev}: {e}")
            self._write_rows(name, fnames, all_rows)
            await self.prom_metrics.update(name, all_rows)
            elapsed = time.time() - start
            interval = self.config["poller_intervals"]["ipsec_sa"]
            await asyncio.sleep(max(0, interval - elapsed))

    async def poll_cpu(self):
        name = "cpu_memory"
        fnames = ["timestamp", "timestamp_iso", "device",
                  "mem_total_kb", "mem_used_kb", "mem_available_kb",
                  "mem_used_pct", "load_1m", "load_5m", "load_15m", "uptime_sec"]
        while self._running:
            start = time.time()
            all_rows = []
            for dev_cfg in self.config["devices"]:
                dev = dev_cfg["name"]
                try:
                    rows = await self.cpu_poller.poll(dev)
                    all_rows.extend(rows)
                except Exception as e:
                    log.warning(f"CPU/memory poll failed on {dev}: {e}")
            self._write_rows(name, fnames, all_rows)
            await self.prom_metrics.update(name, all_rows)
            elapsed = time.time() - start
            interval = self.config["poller_intervals"]["cpu_memory"]
            await asyncio.sleep(max(0, interval - elapsed))

    async def run_syslog(self):
        if not self.config.get("syslog", {}).get("enabled", True):
            log.info("Syslog server disabled in config")
            return
        host = self.config["syslog"]["listen_host"]
        port = self.config["syslog"]["listen_port"]
        self.syslog_server = SyslogServer(host, port, str(self.output_dir))
        await self.syslog_server.start()

    async def run(self):
        log.info("=" * 60)
        log.info("Telemetry Collector starting")
        log.info(f"  Output: {self.output_dir}")
        log.info(f"  Devices: {[d['name'] for d in self.config['devices']]}")
        log.info("=" * 60)

        await self.prom_server.start()

        tasks = [
            asyncio.create_task(self.poll_interface_counters()),
            asyncio.create_task(self.poll_bgp()),
            asyncio.create_task(self.poll_ospf()),
            asyncio.create_task(self.poll_mpls()),
            asyncio.create_task(self.poll_gre()),
            asyncio.create_task(self.poll_ipsec()),
            asyncio.create_task(self.poll_cpu()),
            asyncio.create_task(self.run_syslog()),
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self):
        log.info("Telemetry Collector shutting down...")
        self._running = False
        if self.syslog_server:
            await self.syslog_server.stop()
        await self.prom_server.stop()
        for f in self._csv_files.values():
            f.close()
        log.info("Telemetry Collector stopped")


async def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "telemetry/config.yaml"
    collector = TelemetryCollector(config_path)
    loop = asyncio.get_event_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                sig, lambda: asyncio.create_task(collector.shutdown())
            )
        except NotImplementedError:
            pass

    await collector.run()
    await collector.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
