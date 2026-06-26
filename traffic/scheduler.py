#!/usr/bin/env python3
import asyncio
import logging
import signal
import sys
import time
import uuid
from pathlib import Path

import yaml

from traffic.app_servers import AppServerManager
from traffic.diurnal import DiurnalPattern, RampPattern
from traffic.generator import get_generator
from traffic.ground_truth import GroundTruthLogger

log = logging.getLogger("traffic-scheduler")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


class TrafficScheduler:
    def __init__(self, config_path: str = "traffic/profiles.yaml",
                 ground_truth_dir: str = "ground_truth"):
        self.config_path = Path(config_path)
        self.gt_logger = GroundTruthLogger(ground_truth_dir)
        self.tasks = []
        self.running = True

        with open(self.config_path) as f:
            self.config = yaml.safe_load(f)

        self.profiles = {p: self.config["profiles"][p]
                         for p in self.config["profiles"]}
        self.site_pairs = {sp["name"]: sp
                           for sp in self.config.get("site_pairs", [])}
        self.server_mgr = AppServerManager()
        self._active_servers = {}

        self._server_for_protocol = {
            "http": ("start_http", {"port": 8080}),
            "https": ("start_https", {"port": 8443}),
            "ftp": ("start_ftp", {"port": 21}),
            "smb": ("start_smb", {"port": 445}),
            "dns": ("start_dns", {"port": 5353}),
            "voip": None,
            "database": None,
        }

    async def _ensure_server(self, protocol: str, container: str) -> str:
        server_info = self._server_for_protocol.get(protocol)
        if server_info is None:
            return None
        method_name, params = server_info
        server_key = f"{protocol}_{container}"
        if server_key in self._active_servers:
            return self._active_servers[server_key]
        method = getattr(self.server_mgr, method_name)
        sid = await method(container, **params)
        self._active_servers[server_key] = sid
        log.info(f"Server {protocol} started on {container}")
        return sid

    async def run_flow(self, flow_id: str, profile_name: str,
                       site_pair_name: str, profile_overrides: dict = None,
                       diurnal_type: str = "sine",
                       ramp_config: dict = None):
        profile = dict(self.profiles[profile_name])
        if profile_overrides:
            profile["default_params"] = dict(profile.get("default_params", {}))
            profile["default_params"].update(profile_overrides)

        pair = self.site_pairs[site_pair_name]
        source_ip = pair["source_ip"]
        dest_ip = pair["dest_ip"]
        source = pair["source"]
        dest = pair["dest"]

        params = profile.get("default_params", {})
        duration = params.get("duration_sec", 300)
        rate_bps = params.get("rate_mbps", 100) * 1_000_000

        dscp_str = profile.get("dscp", "DF")
        dscp_val = profile.get("dscp_val", 0)
        protocol = profile.get("protocol", "iperf3")

        session_id = str(uuid.uuid4())[:8]
        diurnal = DiurnalPattern(diurnal_type)
        ramp = RampPattern(**ramp_config) if ramp_config else RampPattern()

        start_time = time.time()
        expected_volume = rate_bps * duration

        self.gt_logger.log_flow_start(
            flow_id, profile_name, source, dest,
            source_ip, dest_ip, protocol, dscp_str,
            rate_bps, params.get("packet_size", 1400),
            duration, expected_volume,
            diurnal.multiplier(), session_id
        )

        app_protocols = {"http", "https", "ftp", "smb", "dns", "voip", "database"}
        if protocol in app_protocols:
            log.info(f"[{flow_id}] Ensuring app server for {protocol} on {dest}...")
            await self._ensure_server(protocol, dest)

        gen = get_generator(profile, source_ip, dest_ip, dscp_val, session_id)

        try:
            await gen.start()
            log.info(f"[{flow_id}] {profile_name} {source}->{dest} started "
                     f"(session={session_id})")

            elapsed = 0
            while elapsed < duration and self.running:
                await asyncio.sleep(1)
                elapsed = time.time() - start_time

            if self.running:
                await gen.wait()
            else:
                await gen.stop()

            self.gt_logger.log_flow_end(flow_id, session_id)
            log.info(f"[{flow_id}] {profile_name} {source}->{dest} completed")

        except Exception as e:
            log.error(f"[{flow_id}] Error: {e}")
            await gen.stop()

    async def run_baseline(self, duration_sec: int = 3600):
        log.info(f"Starting baseline run for {duration_sec}s")

        flow_id = 0
        for pair_name in self.site_pairs:
            for profile_name in self.profiles:
                flow_id += 1
                fid = f"baseline-{profile_name}-{pair_name.replace('_', '-')}-{flow_id}"
                task = asyncio.create_task(
                    self.run_flow(fid, profile_name, pair_name)
                )
                self.tasks.append(task)

        await asyncio.gather(*self.tasks, return_exceptions=True)
        log.info("Baseline complete")

    async def run_ramp_precursor(self, profile_name: str, site_pair_name: str,
                                 ramp_duration_sec: int = 120,
                                 peak_rate_mbps: float = None):
        log.info(f"Ramp-to-precursor: {profile_name} on {site_pair_name}")

        profile = self.profiles[profile_name]
        base_rate = profile["default_params"].get("rate_mbps", 100)
        end_rate = peak_rate_mbps if peak_rate_mbps else base_rate * 3

        flow_id = f"precursor-{profile_name}-{site_pair_name}-{int(time.time())}"

        ramp_cfg = {
            "ramp_type": "linear",
            "start_rate": 0.1,
            "end_rate": end_rate / base_rate if base_rate else 1.0,
            "ramp_duration_sec": ramp_duration_sec,
        }

        await self.run_flow(
            flow_id, profile_name, site_pair_name,
            profile_overrides={"duration_sec": ramp_duration_sec + 30,
                              "rate_mbps": max(base_rate, end_rate)},
            diurnal_type="flat",
            ramp_config=ramp_cfg,
        )

    async def run_diurnal_cycle(self, hours: float = 24):
        log.info(f"Starting diurnal cycle for {hours}h")
        end_time = time.time() + hours * 3600

        while time.time() < end_time and self.running:
            flow_id = int(time.time())
            for pair_name in list(self.site_pairs.keys())[:3]:
                for profile_name in ["business_app", "background_noise"]:
                    fid = f"diurnal-{profile_name}-{pair_name}-{flow_id}"
                    task = asyncio.create_task(
                        self.run_flow(fid, profile_name, pair_name,
                                     diurnal_type="sine")
                    )
                    self.tasks.append(task)

            await asyncio.sleep(300)

    async def shutdown(self):
        self.running = False
        for t in self.tasks:
            t.cancel()
        await self.server_mgr.stop_all()
        self.gt_logger.close()
        log.info("Scheduler shut down")


async def main():
    scheduler = TrafficScheduler()
    loop = asyncio.get_event_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(scheduler.shutdown()))
        except NotImplementedError:
            pass

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "baseline":
            duration = int(sys.argv[2]) if len(sys.argv) > 2 else 3600
            await scheduler.run_baseline(duration)
        elif cmd == "precursor":
            profile = sys.argv[2]
            pair = sys.argv[3]
            ramp_dur = int(sys.argv[4]) if len(sys.argv) > 4 else 120
            await scheduler.run_ramp_precursor(profile, pair, ramp_dur)
        elif cmd == "diurnal":
            hours = float(sys.argv[2]) if len(sys.argv) > 2 else 24
            await scheduler.run_diurnal_cycle(hours)
        else:
            print(f"Usage: {sys.argv[0]} [baseline|precursor|diurnal] [args]")
    else:
        await scheduler.run_baseline(600)

    await scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
