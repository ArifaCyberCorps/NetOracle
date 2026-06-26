import asyncio
import logging

log = logging.getLogger("fault-injection")


class FaultPrimitives:
    def __init__(self, docker_network: str = "netoracle-sdwan"):
        self.network = docker_network

    async def _exec(self, container: str, cmd: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container,
            "bash", "-c", cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.warning(f"docker exec on {container} failed: {stderr.decode()}")
        return stdout.decode().strip()

    async def _vtysh(self, container: str, cmd: str) -> str:
        return await self._exec(container, f"vtysh -c '{cmd}'")

    async def tc_netem(self, container: str, interface: str,
                       delay_ms: int = 0, jitter_ms: int = 0,
                       loss_percent: float = 0.0, rate_kbps: int = 0):
        cmds = [
            f"tc qdisc replace dev {interface} root netem",
        ]
        if delay_ms:
            cmds[0] += f" delay {delay_ms}ms {jitter_ms}ms"
        if loss_percent:
            cmds[0] += f" loss {loss_percent}%"
        if rate_kbps:
            cmds[0] += f" rate {rate_kbps}kbit"
        result = await self._exec(container, cmds[0])
        log.info(f"tc netem on {container}:{interface} - "
                 f"delay={delay_ms}ms jitter={jitter_ms}ms "
                 f"loss={loss_percent}% rate={rate_kbps}kbps")
        return result

    async def tc_clear(self, container: str, interface: str):
        result = await self._exec(container,
                                  f"tc qdisc del dev {interface} root 2>/dev/null || true")
        log.info(f"tc clear on {container}:{interface}")
        return result

    async def interface_down(self, container: str, interface: str):
        result = await self._exec(container, f"ip link set {interface} down")
        log.info(f"Interface DOWN: {container}:{interface}")
        return result

    async def interface_up(self, container: str, interface: str):
        result = await self._exec(container, f"ip link set {interface} up")
        log.info(f"Interface UP: {container}:{interface}")
        return result

    async def bgp_reset(self, container: str, neighbor: str,
                        vrf: str = None, holddown_sec: int = 30):
        if vrf:
            cmd = f"clear bgp vrf {vrf} {neighbor} 2>/dev/null || " \
                  f"clear bgp {neighbor} 2>/dev/null || true"
        else:
            cmd = f"clear bgp {neighbor} 2>/dev/null || true"
        result = await self._vtysh(container, cmd)
        log.info(f"BGP reset on {container}: neighbor {neighbor} vrf={vrf}")
        return result

    async def bgp_neighbor_shutdown(self, container: str, neighbor: str,
                                    vrf: str = None):
        if vrf:
            cmd = f"router bgp 65000 vrf {vrf}"
        else:
            cmd = f"router bgp 65000"
        cmds = (
            f"configure terminal\n"
            f"{cmd}\n"
            f"neighbor {neighbor} shutdown\n"
            f"end\n"
            f"write memory\n"
        )
        result = await self._exec(container,
                                  f"vtysh -c '{cmds.replace(chr(10), ';')}'")
        log.info(f"BGP neighbor SHUTDOWN on {container}: {neighbor}")
        return result

    async def bgp_neighbor_restore(self, container: str, neighbor: str,
                                   vrf: str = None):
        if vrf:
            cmd = f"router bgp 65000 vrf {vrf}"
        else:
            cmd = f"router bgp 65000"
        cmds = (
            f"configure terminal\n"
            f"{cmd}\n"
            f"neighbor {neighbor} no shutdown\n"
            f"end\n"
        )
        result = await self._exec(container,
                                  f"vtysh -c '{cmds.replace(chr(10), ';')}'")
        log.info(f"BGP neighbor RESTORE on {container}: {neighbor}")
        return result

    async def mpls_ldp_disable(self, container: str, interface: str):
        cmds = (
            f"configure terminal\n"
            f"interface {interface}\n"
            f"no mpls ldp\n"
            f"end\n"
        )
        result = await self._exec(container,
                                  f"vtysh -c '{cmds.replace(chr(10), ';')}'")
        log.info(f"MPLS LDP DISABLE on {container}:{interface}")
        return result

    async def mpls_ldp_enable(self, container: str, interface: str):
        cmds = (
            f"configure terminal\n"
            f"interface {interface}\n"
            f"mpls ldp\n"
            f"end\n"
        )
        result = await self._exec(container,
                                  f"vtysh -c '{cmds.replace(chr(10), ';')}'")
        log.info(f"MPLS LDP ENABLE on {container}:{interface}")
        return result

    async def qos_set_dscp_filter(self, container: str, interface: str,
                                  original_dscp: int, new_dscp: int):
        cmds = [
            f"tc qdisc replace dev {interface} root handle 1: htb default 30",
            f"tc filter add dev {interface} parent 1: protocol ip prio 1 "
            f"u32 match ip tos 0x{original_dscp << 2:02x} 0xfc "
            f"action skbedit priority {new_dscp}",
        ]
        for cmd in cmds:
            await self._exec(container, cmd)
        log.info(f"QoS DSCP filter set on {container}:{interface}: "
                 f"{original_dscp} -> {new_dscp}")

    async def qos_clear(self, container: str, interface: str):
        result = await self._exec(container,
                                  f"tc qdisc del dev {interface} root 2>/dev/null || true")
        log.info(f"QoS cleared on {container}:{interface}")
        return result

    async def get_routes(self, container: str, vrf: str = None) -> list:
        if vrf:
            cmd = f"ip route show vrf {vrf}"
        else:
            cmd = "ip route show"
        result = await self._exec(container, cmd)
        routes = [line for line in result.split("\n") if line.strip()]
        return routes

    async def get_mpls_labels(self, container: str) -> list:
        result = await self._exec(container, "show mpls ldp binding 2>/dev/null || true")
        lines = [l for l in result.split("\n") if l.strip()]
        return lines

    # ============ IPsec SD-WAN fault primitives ============

    async def ipsec_sa_down(self, container: str, connection: str):
        """Terminate a specific IKE SA / IPsec SA by connection name."""
        result = await self._exec(container,
                                  f"ipsec down {connection} 2>/dev/null || true")
        log.info(f"IPsec SA DOWN: {container}:{connection}")
        return result

    async def ipsec_sa_up(self, container: str, connection: str):
        """Initiate (restore) a specific IKE SA / IPsec SA."""
        result = await self._exec(container,
                                  f"ipsec up {connection} 2>/dev/null || true")
        log.info(f"IPsec SA UP: {container}:{connection}")
        return result

    async def ipsec_rekey(self, container: str, connection: str):
        """Trigger rekeying of an IPsec SA."""
        result = await self._exec(container,
                                  f"ipsec rekey {connection} 2>/dev/null || true")
        log.info(f"IPsec SA REKEY: {container}:{connection}")
        return result

    async def ipsec_stop(self, container: str):
        """Stop the strongSwan daemon entirely (drops all SAs)."""
        result = await self._exec(container, "ipsec stop 2>/dev/null || true")
        log.info(f"IPsec STOP (all SAs down): {container}")
        return result

    async def ipsec_start(self, container: str):
        """Start the strongSwan daemon (restores all configured SAs)."""
        result = await self._exec(container, "ipsec start 2>/dev/null || true")
        log.info(f"IPsec START: {container}")
        await asyncio.sleep(2)
        return result

    async def vti_interface_down(self, container: str, vti_name: str):
        """Bring down a VTI interface (disrupts overlay routing)."""
        result = await self._exec(container, f"ip link set {vti_name} down")
        log.info(f"VTI interface DOWN: {container}:{vti_name}")
        return result

    async def vti_interface_up(self, container: str, vti_name: str):
        """Bring up a VTI interface (restores overlay routing)."""
        result = await self._exec(container, f"ip link set {vti_name} up")
        log.info(f"VTI interface UP: {container}:{vti_name}")
        return result
