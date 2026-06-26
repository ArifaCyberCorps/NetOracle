import asyncio
import subprocess
import logging
import tempfile
import os
from pathlib import Path

from traffic.app_generators import (
    HTTPGenerator, FTPGenerator, SMBGenerator,
    DNSGenerator, VoIPGenerator, DBGenerator,
)

log = logging.getLogger(__name__)

class BaseGenerator:
    def __init__(self, profile: dict, source_ip: str, dest_ip: str,
                 dscp_val: int, session_id: str):
        self.profile = profile
        self.source_ip = source_ip
        self.dest_ip = dest_ip
        self.dscp_val = dscp_val
        self.session_id = session_id
        self.process = None

    async def start(self):
        raise NotImplementedError

    async def stop(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()

    async def wait(self):
        if self.process:
            await self.process.wait()


class Iperf3Generator(BaseGenerator):
    async def start(self):
        params = self.profile.get("default_params", {})
        mode = self.profile.get("mode", "tcp")
        rate_mbps = params.get("rate_mbps", 100)
        duration = params.get("duration_sec", 300)
        parallel = params.get("parallel", 1)
        window = params.get("window", "256K")
        packet_size = params.get("packet_size", 0)

        cmd = [
            "iperf3", "-c", self.dest_ip,
            "-t", str(duration),
            "-P", str(parallel),
            "-w", window,
            "-b", f"{rate_mbps}M",
        ]

        if mode == "udp":
            cmd.extend(["-u", "-b", f"{rate_mbps}M"])
            if packet_size:
                cmd.extend(["-l", str(packet_size)])
        else:
            cmd.extend(["--dccp-algo", "cubic"])

        cmd.extend(["--tos", str(self.dscp_val)])

        log.info(f"iperf3: {self.source_ip} -> {self.dest_ip} "
                 f"rate={rate_mbps}Mbps dscp={self.dscp_val} duration={duration}s")

        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return self.process


class MgenGenerator(BaseGenerator):
    async def start(self):
        params = self.profile.get("default_params", {})
        rate_kbps = params.get("rate_kbps", 80)
        packet_size = params.get("packet_size", 200)
        duration = params.get("duration_sec", 300)
        interval_ms = params.get("interval_ms", 20)
        pattern = params.get("pattern", "periodic")

        script_content = f"""
0.0 ON {self.dest_ip} UDP {self.dscp_val} PERIODIC [{interval_ms}] [{packet_size}]
0.0 LISTEN UDP {self.dscp_val}
{duration}.0 OFF {self.dest_ip}
"""

        script_file = Path(tempfile.gettempdir()) / f"mgen_{self.session_id}.script"
        script_file.write_text(script_content)

        log_file = Path(tempfile.gettempdir()) / f"mgen_{self.session_id}.log"

        cmd = [
            "mgen", "input", str(script_file),
            "output", str(log_file),
        ]

        log.info(f"mgen: {self.source_ip} -> {self.dest_ip} "
                 f"rate={rate_kbps}kbps dscp={self.dscp_val} duration={duration}s")

        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return self.process


class ScapyGenerator(BaseGenerator):
    async def start(self):
        params = self.profile.get("default_params", {})
        rate_pps = params.get("rate_pps", 500)
        packet_size = params.get("packet_size", 256)
        duration = params.get("duration_sec", 300)
        burst_size = params.get("burst_size", 10)
        burst_interval_ms = params.get("burst_interval_ms", 100)

        script = f"""
import time, threading
from scapy.all import IP, UDP, Ether, send
from scapy.all import conf

conf.verb = 0

stop_event = threading.Event()
sent = 0

def generate():
    global sent
    dscp = {self.dscp_val}
    tos = dscp << 2
    pkt = IP(dst="{self.dest_ip}", tos=tos) / UDP() / ("x" * {packet_size})
    while not stop_event.is_set():
        for _ in range({burst_size}):
            send(pkt, verbose=0)
            sent += 1
        time.sleep({burst_interval_ms / 1000.0})

t = threading.Thread(target=generate, daemon=True)
t.start()
time.sleep({duration})
stop_event.set()
t.join()
import os
os._exit(0)
"""
        script_file = Path(tempfile.gettempdir()) / f"scapy_{self.session_id}.py"
        script_file.write_text(script)

        log.info(f"scapy: {self.source_ip} -> {self.dest_ip} "
                 f"rate={rate_pps}pps dscp={self.dscp_val} duration={duration}s")

        self.process = await asyncio.create_subprocess_exec(
            "python3", str(script_file),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return self.process


class AppGenerator(BaseGenerator):
    def __init__(self, profile: dict, source_ip: str, dest_ip: str,
                 dscp_val: int, session_id: str):
        super().__init__(profile, source_ip, dest_ip, dscp_val, session_id)
        self._gen_task = None

    async def start(self):
        params = self.profile.get("default_params", {})
        duration = params.get("duration_sec", 300)

        self._gen_obj = self._build_gen()
        log.info(f"app/{self.profile.get('protocol', 'app')}: "
                 f"{self.source_ip} -> {self.dest_ip} "
                 f"dscp={self.dscp_val} duration={duration}s")

        self._gen_task = asyncio.create_task(self._gen_obj.generate())
        return None

    async def stop(self):
        if self._gen_obj:
            self._gen_obj.stop()
        if self._gen_task:
            self._gen_task.cancel()
            try:
                await self._gen_task
            except asyncio.CancelledError:
                pass

    async def wait(self):
        if self._gen_task:
            await self._gen_task

    def _build_gen(self):
        protocol = self.profile.get("protocol", "app")
        params = self.profile.get("default_params", {})

        protocol_map = {
            "http": HTTPGenerator,
            "https": HTTPGenerator,
            "ftp": FTPGenerator,
            "smb": SMBGenerator,
            "dns": DNSGenerator,
            "voip": VoIPGenerator,
            "database": DBGenerator,
        }

        gen_class = protocol_map.get(protocol, HTTPGenerator)

        if protocol == "https":
            params = {**params, "ssl": True}

        return gen_class(self.source_ip, self.dest_ip, params, self.session_id)


def get_generator(profile: dict, source_ip: str, dest_ip: str,
                  dscp_val: int, session_id: str) -> BaseGenerator:
    protocol = profile.get("protocol", "iperf3")

    subprocess_protocols = {"iperf3", "mgen", "scapy"}
    app_protocols = {"http", "https", "ftp", "smb", "dns", "voip", "database"}

    if protocol in subprocess_protocols:
        if protocol == "iperf3":
            return Iperf3Generator(profile, source_ip, dest_ip, dscp_val, session_id)
        elif protocol == "mgen":
            return MgenGenerator(profile, source_ip, dest_ip, dscp_val, session_id)
        elif protocol == "scapy":
            return ScapyGenerator(profile, source_ip, dest_ip, dscp_val, session_id)
    elif protocol in app_protocols:
        return AppGenerator(profile, source_ip, dest_ip, dscp_val, session_id)
    else:
        raise ValueError(f"Unknown protocol: {protocol}")
