import asyncio
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger("app-servers")


class AppServerManager:
    def __init__(self):
        self.servers = {}
        self._container_for_server = {}

    async def _docker_exec(self, container: str, script: str,
                           python: bool = True) -> asyncio.subprocess.Process:
        if python:
            cmd = ["docker", "exec", "-d", container, "python3", "-c", script]
        else:
            cmd = ["docker", "exec", "-d", container, "bash", "-c", script]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return proc

    async def start_http(self, container: str, host: str = "0.0.0.0",
                         port: int = 8080) -> str:
        server_id = f"http_{container}_{port}"
        if server_id in self.servers:
            return server_id
        script = (
            "import http.server, os, tempfile; "
            "d = tempfile.mkdtemp(prefix='http_'); "
            "os.makedirs(d, exist_ok=True); "
            "for i in range(50): open(f'{d}/page_{i}.html','w').write(f'<html><body>Page {i}</body></html>'); "
            "for i in range(10): open(f'{d}/asset_{i}.bin','wb').write(os.urandom(51200)); "
            f"os.chdir(d); "
            f"http.server.test(HandlerClass=http.server.SimpleHTTPRequestHandler, port={port}, bind='{host}')"
        )
        proc = await self._docker_exec(container, script)
        self.servers[server_id] = proc
        self._container_for_server[server_id] = container
        log.info(f"HTTP server started on {container}:{port}")
        await asyncio.sleep(0.5)
        return server_id

    async def start_https(self, container: str, host: str = "0.0.0.0",
                          port: int = 8443) -> str:
        server_id = f"https_{container}_{port}"
        if server_id in self.servers:
            return server_id
        script = (
            "import http.server, ssl, os, tempfile; "
            "d = tempfile.mkdtemp(prefix='https_'); "
            "os.makedirs(d, exist_ok=True); "
            "os.chdir(d); "
            "for i in range(50): open(f'page_{i}.html','w').write(f'<html><body>Page {i}</body></html>'); "
            "for i in range(10): open(f'asset_{i}.bin','wb').write(os.urandom(51200)); "
            "open('/tmp/server.crt','w').write(open('/etc/ssl/certs/ssl-cert-snakeoil.pem').read()); "
            "open('/tmp/server.key','w').write(open('/etc/ssl/private/ssl-cert-snakeoil.key').read()); "
            f"h=http.server.HTTPServer(('{host}',{port}), http.server.SimpleHTTPRequestHandler); "
            "h.socket=ssl.wrap_socket(h.socket,server_side=True,"
            "certfile='/tmp/server.crt',keyfile='/tmp/server.key'); h.serve_forever()"
        )
        proc = await self._docker_exec(container, script)
        self.servers[server_id] = proc
        self._container_for_server[server_id] = container
        log.info(f"HTTPS server started on {container}:{port}")
        await asyncio.sleep(0.5)
        return server_id

    async def start_ftp(self, container: str, host: str = "0.0.0.0",
                        port: int = 21) -> str:
        server_id = f"ftp_{container}_{port}"
        if server_id in self.servers:
            return server_id
        script = (
            "from pyftpdlib.authorizers import DummyAuthorizer; "
            "from pyftpdlib.handlers import FTPHandler; "
            "from pyftpdlib.servers import FTPServer; "
            "import tempfile, os; "
            "d=tempfile.mkdtemp(prefix='ftp_'); "
            "for i in range(10): open(f'{d}/file_{i}.dat','wb').write(os.urandom(1024*1024*(i+1))); "
            "a=DummyAuthorizer(); a.add_user('user','pass',d,perm='elradfmw'); "
            "a.add_anonymous(d,perm='elr'); "
            "h=FTPHandler; h.authorizer=a; "
            f"s=FTPServer(('{host}',{port}),h); s.serve_forever()"
        )
        proc = await self._docker_exec(container, script)
        self.servers[server_id] = proc
        self._container_for_server[server_id] = container
        log.info(f"FTP server started on {container}:{port}")
        await asyncio.sleep(0.5)
        return server_id

    async def start_smb(self, container: str, host: str = "0.0.0.0",
                        port: int = 445) -> str:
        server_id = f"smb_{container}_{port}"
        if server_id in self.servers:
            return server_id
        script = (
            "import tempfile, os, socketserver, threading; "
            "d=tempfile.mkdtemp(prefix='smb_'); "
            "for i in range(5): open(f'{d}/share_{i}.bin','wb').write(os.urandom(1024*1024*2)); "
            "import socketserver; "
            "class H(socketserver.BaseRequestHandler): "
            "  def handle(self): "
            "    data=self.request.recv(1024); "
            "    self.request.send(b'\\x00'*4); "
            f"s=socketserver.TCPServer(('{host}',{port}),H); "
            "t=threading.Thread(target=s.serve_forever,daemon=True); t.start(); "
            "t.join()"
        )
        proc = await self._docker_exec(container, script)
        self.servers[server_id] = proc
        self._container_for_server[server_id] = container
        log.info(f"SMB server started on {container}:{port}")
        await asyncio.sleep(0.5)
        return server_id

    async def start_dns(self, container: str, host: str = "0.0.0.0",
                        port: int = 5353) -> str:
        server_id = f"dns_{container}_{port}"
        if server_id in self.servers:
            return server_id
        script = (
            "import socket,struct; "
            "R={'netoracle.local.':'172.16.0.1','dc1.netoracle.local.':'172.16.0.2',"
            "'hub.netoracle.local.':'172.16.2.2','app.netoracle.local.':'172.16.0.2',"
            "'db.netoracle.local.':'172.16.2.2','files.netoracle.local.':'172.16.1.2',"
            "'mail.netoracle.local.':'172.16.0.2','portal.netoracle.local.':'172.16.2.2',"
            "'api.netoracle.local.':'172.16.0.2',"
            "'branch1.netoracle.local.':'172.16.3.2','branch2.netoracle.local.':'172.16.4.2',"
            "'branch3.netoracle.local.':'172.16.5.2','branch4.netoracle.local.':'172.16.6.2',"
            "'branch5.netoracle.local.':'172.16.7.2','branch6.netoracle.local.':'172.16.8.2'}; "
            "def resp(data): "
            "  if len(data)<12: return None; "
            "  qs=12; "
            "  while qs<len(data) and data[qs]!=0: qs+=1; "
            "  lbl=[]; i=12; "
            "  while i<len(data) and data[i]!=0: l=data[i]; lbl.append(data[i+1:i+1+l].decode('ascii','replace')); i+=l+1; "
            "  n='.'.join(lbl)+'.'; "
            "  a=None; "
            "  if n in R: ip=[int(x) for x in R[n].split('.')]; a=struct.pack('!2HIH4s',0xc00c,1,1,300,bytes(ip)); "
            "  h=struct.pack('!HHHHHH',*struct.unpack('!H',data[:2])+(0x8180,1,1 if a else 0,0,0)); "
            "  r=h+data[12:qs+1]+struct.pack('!HH',1,1); "
            "  if a: r+=a; "
            "  return r; "
            f"s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(('{host}',{port})); "
            "while True: d,a=s.recvfrom(512); r=resp(d); "
            "if r: s.sendto(r,a)"
        )
        proc = await self._docker_exec(container, script)
        self.servers[server_id] = proc
        self._container_for_server[server_id] = container
        log.info(f"DNS server started on {container}:{port}")
        await asyncio.sleep(0.5)
        return server_id

    async def stop_all(self):
        for sid, proc in self.servers.items():
            container = self._container_for_server.get(sid, "")
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except asyncio.TimeoutError:
                    proc.kill()
            log.info(f"Stopped server: {sid} on {container}")
        self.servers.clear()
        self._container_for_server.clear()
