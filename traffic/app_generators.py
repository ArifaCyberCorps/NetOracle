import asyncio
import logging
import random
import socket
import ssl
import struct
import time
import urllib.request
import uuid
from pathlib import Path
import tempfile

log = logging.getLogger("app-generators")


class AppProtocolGenerator:
    def __init__(self, source_ip: str, dest_ip: str, params: dict,
                 session_id: str):
        self.source_ip = source_ip
        self.dest_ip = dest_ip
        self.params = params
        self.session_id = session_id
        self.running = True

    async def generate(self):
        raise NotImplementedError

    def stop(self):
        self.running = False


class HTTPGenerator(AppProtocolGenerator):
    async def generate(self):
        port = self.params.get("port", 8080)
        duration = self.params.get("duration_sec", 300)
        requests_per_sec = self.params.get("rate_rps", 10)
        ssl_mode = self.params.get("ssl", False)
        page_count = self.params.get("page_count", 20)
        protocol = "https" if ssl_mode else "http"
        scheme = "https" if ssl_mode else "http"
        end_time = time.time() + duration

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        log.info(f"HTTP{'S' if ssl_mode else ''} generator: "
                 f"{self.source_ip} -> {self.dest_ip}:{port} "
                 f"rate={requests_per_sec}rps")

        req_count = 0
        while time.time() < end_time and self.running:
            page = random.randint(0, page_count - 1)
            url = f"{scheme}://{self.dest_ip}:{port}/page_{page}.html"
            try:
                req = urllib.request.Request(url)
                if ssl_mode:
                    resp = urllib.request.urlopen(req, timeout=5, context=ctx)
                else:
                    resp = urllib.request.urlopen(req, timeout=5)
                resp.read()
                resp.close()
                req_count += 1
            except Exception as e:
                log.debug(f"HTTP request error: {e}")
            delay = 1.0 / requests_per_sec
            await asyncio.sleep(delay * random.uniform(0.5, 1.5))
        log.info(f"HTTP{'S' if ssl_mode else ''} generator done: "
                 f"{req_count} requests in {duration}s")


class FTPGenerator(AppProtocolGenerator):
    async def generate(self):
        port = self.params.get("port", 21)
        duration = self.params.get("duration_sec", 300)
        action = self.params.get("action", "download")
        file_size_max = self.params.get("file_size_max_mb", 10)
        interval_sec = self.params.get("interval_sec", 5)

        end_time = time.time() + duration
        transfer_count = 0

        log.info(f"FTP generator: {self.source_ip} -> {self.dest_ip}:{port} "
                 f"action={action}")

        while time.time() < end_time and self.running:
            script = f"""
import ftplib, io, os, tempfile
try:
    ftp = ftplib.FTP()
    ftp.connect("{self.dest_ip}", {port}, timeout=10)
    ftp.login("user", "pass")
    files = ftp.nlst()
    if files:
        fname = files[{random.randint(0, 9)}] if len(files) > 9 else files[0]
        data = io.BytesIO()
        ftp.retrbinary(f"RETR {{fname}}", data.write)
    ftp.quit()
    print("OK")
except Exception as e:
    print(f"ERR: {{e}}")
"""
            proc = await asyncio.create_subprocess_exec(
                "python3", "-c", script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            transfer_count += 1
            await asyncio.sleep(interval_sec)
        log.info(f"FTP generator done: {transfer_count} transfers")


class SMBGenerator(AppProtocolGenerator):
    async def generate(self):
        port = self.params.get("port", 445)
        duration = self.params.get("duration_sec", 300)
        interval_sec = self.params.get("interval_sec", 10)

        end_time = time.time() + duration
        transfer_count = 0

        log.info(f"SMB generator: {self.source_ip} -> {self.dest_ip}:{port}")

        while time.time() < end_time and self.running:
            script = f"""
import tempfile, os, io
from smbprotocol.connection import Connection
from smbprotocol.session import Session
from smbprotocol.tree import TreeConnect
from smbprotocol.open import Open, FilePipePrinterAccessMask
from smbprotocol.create_contexts import CreateContextName
try:
    conn = Connection(uuid.uuid4(), "{self.dest_ip}", {port})
    conn.connect()
    session = Session(conn, "user", "pass")
    session.connect()
    tree = TreeConnect(session, f"\\\\\\\\{{self.dest_ip}}\\\share")
    tree.connect()
    open_file = Open(tree, f"sharefile_{{random.randint(0, 4)}}.bin")
    open_file.create()
    data = open_file.read(0, 1024 * 1024)
    open_file.close()
    conn.disconnect()
    print("OK")
except Exception as e:
    print(f"ERR: {{e}}")
"""
            proc = await asyncio.create_subprocess_exec(
                "python3", "-c", script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            transfer_count += 1
            await asyncio.sleep(interval_sec)
        log.info(f"SMB generator done: {transfer_count} transfers")


class DNSGenerator(AppProtocolGenerator):
    async def generate(self):
        port = self.params.get("port", 5353)
        duration = self.params.get("duration_sec", 300)
        queries_per_sec = self.params.get("rate_qps", 50)

        domains = [
            "netoracle.local", "dc1.netoracle.local", "hub.netoracle.local",
            "app.netoracle.local", "db.netoracle.local", "files.netoracle.local",
            "mail.netoracle.local", "portal.netoracle.local", "api.netoracle.local",
            "branch1.netoracle.local", "branch2.netoracle.local",
            "branch3.netoracle.local", "branch4.netoracle.local",
            "branch5.netoracle.local", "branch6.netoracle.local",
        ]

        end_time = time.time() + duration
        query_count = 0

        log.info(f"DNS generator: {self.source_ip} -> {self.dest_ip}:{port} "
                 f"rate={queries_per_sec}qps")

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2)

        while time.time() < end_time and self.running:
            domain = random.choice(domains)
            tid = random.randint(0, 65535)
            qname = b"".join(
                bytes([len(p)]) + p.encode()
                for p in domain.split(".")
            ) + b"\x00"
            query = struct.pack("!HHHHHH", tid, 0x0100, 1, 0, 0, 0)
            query += qname + struct.pack("!HH", 1, 1)
            try:
                sock.sendto(query, (self.dest_ip, port))
                data, _ = sock.recvfrom(512)
                query_count += 1
            except socket.timeout:
                pass
            except Exception as e:
                log.debug(f"DNS error: {e}")
            delay = 1.0 / queries_per_sec
            await asyncio.sleep(delay)
        sock.close()
        log.info(f"DNS generator done: {query_count} queries in {duration}s")


class VoIPGenerator(AppProtocolGenerator):
    async def generate(self):
        port = self.params.get("port", 5004)
        duration = self.params.get("duration_sec", 300)
        rate_kbps = self.params.get("rate_kbps", 80)
        packet_size = self.params.get("packet_size", 200)
        interval_ms = self.params.get("interval_ms", 20)
        dscp_val = self.params.get("dscp_val", 46)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tos = dscp_val << 2
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, tos)

        seq = 0
        ts = 0
        ssrc = random.randint(0, 0xFFFFFFFF)
        end_time = time.time() + duration
        pkt_count = 0

        log.info(f"VoIP/RTP generator: {self.source_ip} -> {self.dest_ip}:{port} "
                 f"rate={rate_kbps}kbps dscp={dscp_val}")

        while time.time() < end_time and self.running:
            rtp_header = struct.pack("!BBHII", 0x80, 0x00 | 0, seq, ts, ssrc)
            payload = b"\x00" * packet_size
            pkt = rtp_header + payload
            sock.sendto(pkt, (self.dest_ip, port))
            seq = (seq + 1) & 0xFFFF
            ts += packet_size
            pkt_count += 1
            await asyncio.sleep(interval_ms / 1000.0)
        sock.close()
        log.info(f"VoIP/RTP generator done: {pkt_count} packets")


class DBGenerator(AppProtocolGenerator):
    async def generate(self):
        port = self.params.get("port", 5432)
        duration = self.params.get("duration_sec", 300)
        rate_pps = self.params.get("rate_pps", 500)
        packet_size = self.params.get("packet_size", 256)
        burst_size = self.params.get("burst_size", 10)
        dscp_val = self.params.get("dscp_val", 18)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tos = dscp_val << 2
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, tos)

        end_time = time.time() + duration
        query_templates = [
            b"\x00" + f"SELECT * FROM users WHERE id={{{i}}}".encode()
            for i in range(10)
        ] + [
            b"\x00" + f"INSERT INTO log (msg) VALUES ('{'x' * 64}')".encode()
            for _ in range(5)
        ] + [
            b"\x00" + f"UPDATE inventory SET qty=qty-1 WHERE sku='{'A' * 16}'".encode()
            for _ in range(5)
        ]
        pkt_count = 0

        log.info(f"DB/ERP generator: {self.source_ip} -> {self.dest_ip}:{port} "
                 f"rate={rate_pps}pps dscp={dscp_val}")

        while time.time() < end_time and self.running:
            template = random.choice(query_templates)
            payload = template[:packet_size].ljust(packet_size, b"\x00")
            sock.sendto(payload, (self.dest_ip, port))
            pkt_count += 1
            delay = 1.0 / rate_pps
            await asyncio.sleep(delay)
        sock.close()
        log.info(f"DB/ERP generator done: {pkt_count} packets")
