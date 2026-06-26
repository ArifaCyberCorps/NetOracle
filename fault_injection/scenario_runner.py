#!/usr/bin/env python3
import asyncio
import logging
import signal
import sys
import time
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from traffic.scheduler import TrafficScheduler
from traffic.ground_truth import GroundTruthLogger
from fault_injection.faults import FaultPrimitives

log = logging.getLogger("scenario-runner")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


DSCP_MAP = {
    "EF": 46, "AF41": 34, "AF21": 18, "AF31": 26,
    "CS1": 8, "CS2": 16, "CS3": 24, "CS4": 32,
    "CS5": 40, "CS6": 48, "CS7": 56, "BE": 0, "DF": 0,
}


class ScenarioRunner:
    def __init__(self, scenarios_path: str = "fault_injection/scenarios.yaml"):
        self.fp = FaultPrimitives()
        self.gt_logger = GroundTruthLogger("ground_truth")
        self.running = True
        self.active_traffic_tasks = []

        with open(scenarios_path) as f:
            self.config = yaml.safe_load(f)

        self.scenarios = self.config["scenarios"]

    async def run_scenario(self, scenario_name: str):
        if scenario_name not in self.scenarios:
            log.error(f"Unknown scenario: {scenario_name}")
            log.info(f"Available: {list(self.scenarios.keys())}")
            return

        scenario = self.scenarios[scenario_name]
        log.info(f"=== Starting scenario: {scenario_name} ===")
        log.info(f"Description: {scenario['description']}")

        self.gt_logger.log_fault(
            scenario_name, "scenario_start", "",
            {"description": scenario["description"]}, "init"
        )

        traffic_configs = await self._start_background_traffic(scenario_name)

        for fault in scenario["faults"]:
            if not self.running:
                break

            fault_type = fault["type"]
            target = fault["target"]
            params = fault.get("params", {})
            phase = fault.get("phase", "unknown")

            log.info(f"[{phase}] Injecting fault: {fault_type} on {target}")

            self.gt_logger.log_fault(
                scenario_name, fault_type, target, params, phase,
                f"Fault {fault_type} on {target} ({phase})"
            )

            await self._inject_fault(fault_type, target, params)

            wait_sec = params.get("duration_sec", params.get("holddown_sec", 30))
            if wait_sec > 0 and "traffic" not in fault_type:
                log.info(f"Waiting {wait_sec}s for fault to propagate...")
                await self._wait_with_interrupt(wait_sec)

        await self._cleanup_traffic()

        self.gt_logger.log_fault(
            scenario_name, "scenario_end", "",
            {}, "complete"
        )
        log.info(f"=== Scenario {scenario_name} complete ===")

    async def _inject_fault(self, fault_type: str, target: str, params: dict):
        try:
            if fault_type == "traffic_ramp":
                profile = params.get("profile", "bulk_transfer")
                dur = params.get("ramp_duration_sec", 180)
                task = asyncio.create_task(
                    self._run_ramp(profile, target, dur, params.get("peak_rate_mbps"))
                )
                self.active_traffic_tasks.append(task)

            elif fault_type == "traffic_stop":
                log.info(f"Traffic stop placeholder for {target}")

            elif fault_type == "bgp_reset":
                container = target
                neighbor = params["neighbor"]
                vrf = params.get("vrf")
                holddown = params.get("holddown_sec", 30)
                await self.fp.bgp_reset(container, neighbor, vrf)
                await self.fp.bgp_neighbor_shutdown(container, neighbor, vrf)
                await asyncio.sleep(holddown)
                await self.fp.bgp_neighbor_restore(container, neighbor, vrf)

            elif fault_type == "mpls_ldp_disable":
                iface = params.get("interface", "eth1")
                dur = params.get("duration_sec", 60)
                await self.fp.mpls_ldp_disable(target, iface)
                await asyncio.sleep(dur)

            elif fault_type == "mpls_ldp_enable":
                iface = params.get("interface", "eth1")
                await self.fp.mpls_ldp_enable(target, iface)

            elif fault_type == "interface_flap":
                iface = params.get("interface", "eth1")
                down_dur = params.get("down_duration_sec", 30)
                up_dur = params.get("up_duration_sec", 20)
                cycles = params.get("cycles", 1)
                for i in range(cycles):
                    await self.fp.interface_down(target, iface)
                    await asyncio.sleep(down_dur)
                    await self.fp.interface_up(target, iface)
                    if i < cycles - 1:
                        await asyncio.sleep(up_dur)

            elif fault_type == "qos_change":
                iface = params.get("interface", "eth1")
                orig = DSCP_MAP.get(params.get("original_dscp", "EF"), 46)
                new = DSCP_MAP.get(params.get("new_dscp", "BE"), 0)
                await self.fp.qos_set_dscp_filter(target, iface, orig, new)

            elif fault_type == "qos_restore":
                iface = params.get("interface", "eth1")
                await self.fp.qos_clear(target, iface)

            # ============ IPsec SD-WAN faults ============
            elif fault_type == "ipsec_sa_down":
                conn = params.get("connection", "")
                dur = params.get("duration_sec", 30)
                await self.fp.ipsec_sa_down(target, conn)
                await asyncio.sleep(dur)

            elif fault_type == "ipsec_sa_up":
                conn = params.get("connection", "")
                await self.fp.ipsec_sa_up(target, conn)

            elif fault_type == "ipsec_rekey":
                conn = params.get("connection", "")
                await self.fp.ipsec_rekey(target, conn)

            elif fault_type == "ipsec_stop":
                await self.fp.ipsec_stop(target)

            elif fault_type == "ipsec_start":
                await self.fp.ipsec_start(target)

            elif fault_type == "vti_down":
                vti = params.get("vti_name", "")
                dur = params.get("duration_sec", 30)
                await self.fp.vti_interface_down(target, vti)
                await asyncio.sleep(dur)

            elif fault_type == "vti_up":
                vti = params.get("vti_name", "")
                await self.fp.vti_interface_up(target, vti)

            elif fault_type == "wait":
                dur = params.get("duration_sec", 10)
                await asyncio.sleep(dur)

            else:
                log.warning(f"Unknown fault type: {fault_type}")

        except Exception as e:
            log.error(f"Fault injection error on {target}: {e}")

    async def _run_ramp(self, profile: str, site_pair: str,
                        duration_sec: int, peak_rate_mbps: float = None):
        scheduler = TrafficScheduler("traffic/profiles.yaml", "ground_truth")
        await scheduler.run_ramp_precursor(profile, site_pair,
                                           duration_sec, peak_rate_mbps)

    async def _start_background_traffic(self, scenario_name: str):
        scheduler = TrafficScheduler("traffic/profiles.yaml", "ground_truth")

        pairs = ["branch1_to_hub", "branch2_to_dc1", "branch3_to_hub"]
        for pair in pairs:
            for profile_name in ["voice", "business_app", "background_noise"]:
                fid = f"{scenario_name}-bg-{profile_name}-{pair}"
                task = asyncio.create_task(
                    scheduler.run_flow(fid, profile_name, pair)
                )
                self.active_traffic_tasks.append(task)

        return []

    async def _cleanup_traffic(self):
        for task in self.active_traffic_tasks:
            task.cancel()
        self.active_traffic_tasks.clear()

    async def _wait_with_interrupt(self, seconds: int):
        for _ in range(seconds):
            if not self.running:
                break
            await asyncio.sleep(1)

    async def shutdown(self):
        self.running = False
        await self._cleanup_traffic()
        self.gt_logger.close()
        log.info("Scenario runner shut down")


async def main():
    runner = ScenarioRunner()
    loop = asyncio.get_event_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                sig, lambda: asyncio.create_task(runner.shutdown())
            )
        except NotImplementedError:
            pass

    if len(sys.argv) > 1:
        scenario_name = sys.argv[1]
        await runner.run_scenario(scenario_name)
    else:
        log.info("Available scenarios:")
        for name in runner.scenarios:
            log.info(f"  - {name}")
        log.info(f"Usage: {sys.argv[0]} <scenario_name>")

    await runner.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
