import csv
import json
import time
import os
from datetime import datetime, timezone
from pathlib import Path

class GroundTruthLogger:
    def __init__(self, log_dir: str = "ground_truth"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.start_time = time.time()

        flow_log_path = self.log_dir / "flow_log.csv"
        fault_log_path = self.log_dir / "fault_log.csv"

        self.flow_file = open(flow_log_path, "w", newline="")
        self.flow_writer = csv.writer(self.flow_file)
        self.flow_writer.writerow([
            "timestamp_unix", "timestamp_iso", "flow_id", "profile",
            "source", "dest", "source_ip", "dest_ip",
            "protocol", "dscp", "rate_bps", "packet_size",
            "duration_sec", "expected_volume_bytes", "diurnal_multiplier",
            "session_id", "status"
        ])
        self.flow_file.flush()

        self.fault_file = open(fault_log_path, "w", newline="")
        self.fault_writer = csv.writer(self.fault_file)
        self.fault_writer.writerow([
            "timestamp_unix", "timestamp_iso", "scenario_id",
            "fault_type", "target", "parameters_json",
            "phase", "description"
        ])
        self.fault_file.flush()

    def log_flow_start(self, flow_id: str, profile: str, source: str, dest: str,
                       source_ip: str, dest_ip: str, protocol: str, dscp: str,
                       rate_bps: int, packet_size: int, duration_sec: int,
                       expected_volume_bytes: int, diurnal_multiplier: float,
                       session_id: str):
        now = time.time()
        iso = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        self.flow_writer.writerow([
            round(now, 3), iso, flow_id, profile,
            source, dest, source_ip, dest_ip,
            protocol, dscp, rate_bps, packet_size,
            duration_sec, expected_volume_bytes, round(diurnal_multiplier, 4),
            session_id, "started"
        ])
        self.flow_file.flush()

    def log_flow_end(self, flow_id: str, session_id: str):
        now = time.time()
        iso = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        self.flow_writer.writerow([
            round(now, 3), iso, flow_id, "", "", "", "", "",
            "", "", 0, 0, 0, 0, 0.0,
            session_id, "completed"
        ])
        self.flow_file.flush()

    def log_fault(self, scenario_id: str, fault_type: str, target: str,
                  parameters: dict, phase: str, description: str = ""):
        now = time.time()
        iso = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        self.fault_writer.writerow([
            round(now, 3), iso, scenario_id,
            fault_type, target, json.dumps(parameters),
            phase, description
        ])
        self.fault_file.flush()

    def close(self):
        self.flow_file.close()
        self.fault_file.close()
