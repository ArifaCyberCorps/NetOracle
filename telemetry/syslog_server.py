import asyncio
import logging
import csv
import re
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("syslog-server")


class SyslogServer:
    FACILITY_MAP = {
        0: "kern", 1: "user", 2: "mail", 3: "daemon",
        4: "auth", 5: "syslog", 6: "lpr", 7: "news",
        8: "uucp", 9: "cron", 10: "authpriv", 11: "ftp",
        16: "local0", 17: "local1", 18: "local2", 19: "local3",
        20: "local4", 21: "local5", 22: "local6", 23: "local7",
    }
    SEVERITY_MAP = {
        0: "emerg", 1: "alert", 2: "crit", 3: "error",
        4: "warning", 5: "notice", 6: "info", 7: "debug",
    }

    FRR_PATTERNS = [
        (r"BGP.*(?:down|up|state|change)", "bgp"),
        (r"OSPF.*(?:Neighbor|Adjacency|state|change)", "ospf"),
        (r"LDP.*(?:session|label|binding)", "mpls"),
        (r"Interface.*(?:up|down|state|status)", "interface"),
        (r"Route.*(?:update|change|withdraw)", "routing"),
        (r"Configuration.*(?:change|commit)", "config"),
        (r"(?:DPD|IKE SA|Rekey|VTI.*health)", "ipsec"),
    ]

    def __init__(self, host: str = "0.0.0.0", port: int = 5514,
                 output_dir: str = "telemetry_data"):
        self.host = host
        self.port = port
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._running = False
        self._log_file = self.output_dir / "syslog.csv"
        self._csv_writer = None
        self._file_handle = None

    def _init_csv(self):
        if self._file_handle is None:
            self._file_handle = open(self._log_file, "a", newline="")
            self._csv_writer = csv.writer(self._file_handle)
            if self._log_file.stat().st_size == 0:
                self._csv_writer.writerow([
                    "timestamp_unix", "timestamp_iso", "source_ip",
                    "facility", "severity", "app_name",
                    "message", "category"
                ])
                self._file_handle.flush()

    def _parse_syslog(self, data: bytes, addr: tuple) -> dict:
        msg = data.decode(errors="replace").strip()
        priority = 0
        body = msg
        m = re.match(r"<(\d+)>(.*)", msg)
        if m:
            priority = int(m.group(1))
            body = m.group(2).strip()
        facility = priority >> 3
        severity = priority & 0x07
        fac_name = self.FACILITY_MAP.get(facility, f"facility_{facility}")
        sev_name = self.SEVERITY_MAP.get(severity, f"severity_{severity}")

        app_match = re.match(r"([\w\.\-/]+)\s+(\d+)?\s*:\s*(.*)", body)
        app_name = app_match.group(1).split("/")[0] if app_match else "unknown"
        message = app_match.group(3) if app_match else body

        category = "general"
        for pattern, cat in self.FRR_PATTERNS:
            if re.search(pattern, message, re.IGNORECASE):
                category = cat
                break

        now = time.time()
        iso = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        return {
            "timestamp_unix": round(now, 3),
            "timestamp_iso": iso,
            "source_ip": addr[0],
            "facility": fac_name,
            "severity": sev_name,
            "app_name": app_name,
            "message": message,
            "category": category,
        }

    async def start(self):
        self._init_csv()
        self._running = True
        loop = asyncio.get_event_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: SyslogProtocol(self._handle_message),
            local_addr=(self.host, self.port),
        )
        log.info(f"Syslog server listening on {self.host}:{self.port}")
        self._transport = transport
        while self._running:
            await asyncio.sleep(1)

    def _handle_message(self, data: bytes, addr: tuple):
        entry = self._parse_syslog(data, addr)
        if self._csv_writer:
            self._csv_writer.writerow([
                entry["timestamp_unix"], entry["timestamp_iso"],
                entry["source_ip"], entry["facility"], entry["severity"],
                entry["app_name"], entry["message"], entry["category"],
            ])
            self._file_handle.flush()

    async def stop(self):
        self._running = False
        if hasattr(self, "_transport"):
            self._transport.close()
        if self._file_handle:
            self._file_handle.close()
        log.info("Syslog server stopped")


class SyslogProtocol(asyncio.DatagramProtocol):
    def __init__(self, handler):
        self.handler = handler

    def datagram_received(self, data: bytes, addr: tuple):
        self.handler(data, addr)

    def error_received(self, exc):
        log.warning(f"Syslog receive error: {exc}")
