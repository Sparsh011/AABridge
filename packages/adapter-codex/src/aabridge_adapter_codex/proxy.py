"""Authenticated, per-worker CONNECT relay. TLS stays end-to-end with Codex.

This is not a bridge RPC listener or an unrestricted forward proxy. It accepts
only chatgpt.com:443, resolves outside the worker, rejects non-public addresses,
and never records tunnel content or credentials.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import secrets
import socket


class ProviderProxy:
    def __init__(self):
        self.secret = secrets.token_urlsafe(32)
        self.expected = "Basic " + base64.b64encode(("aabridge:" + self.secret).encode()).decode()
        self.server = None
        self.connections = set()
        self.active = 0
        self.stats = {"connections": 0, "destination_denied": 0, "authentication_denied": 0, "upstream_connected": 0}

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._accept, "127.0.0.1", 0, limit=8192)
        return self.server.sockets[0].getsockname()[1]

    async def _accept(self, reader, writer):
        task = asyncio.current_task()
        self.connections.add(task)
        self.stats["connections"] += 1
        upstream = None
        counted = False
        try:
            headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            if len(headers) > 8192:
                return
            lines = headers.decode("ascii").split("\r\n")
            if lines[0] not in {"CONNECT chatgpt.com:443 HTTP/1.1", "CONNECT chatgpt.com:443 HTTP/1.0"}:
                self.stats["destination_denied"] += 1
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return
            values = {}
            for line in lines[1:]:
                if line:
                    key, value = line.split(":", 1)
                    if key.lower() in values:
                        return
                    values[key.lower()] = value.strip()
            if not secrets.compare_digest(values.get("proxy-authorization", ""), self.expected):
                self.stats["authentication_denied"] += 1
                writer.write(b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="aabridge"\r\nContent-Length: 0\r\n\r\n')
                await writer.drain()
                return
            if self.active >= 4:
                return
            self.active += 1
            counted = True
            addresses = await asyncio.get_running_loop().getaddrinfo("chatgpt.com", 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
            public = sorted({entry[4][0] for entry in addresses})
            if not public or not all(ipaddress.ip_address(address).is_global for address in public):
                return
            remote_reader = None
            for address in public:
                try:
                    remote_reader, upstream = await asyncio.wait_for(asyncio.open_connection(address, 443), 10)
                    break
                except OSError:
                    continue
            if remote_reader is None:
                return
            self.stats["upstream_connected"] += 1
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            async def forward(source, destination):
                total = 0
                while chunk := await source.read(16384):
                    total += len(chunk)
                    if total > 4_194_304:
                        return
                    destination.write(chunk)
                    await destination.drain()
            flows = [asyncio.create_task(forward(reader, upstream)), asyncio.create_task(forward(remote_reader, writer))]
            try:
                await asyncio.wait_for(asyncio.wait(flows, return_when=asyncio.FIRST_COMPLETED), 300)
            finally:
                for flow in flows:
                    flow.cancel()
                await asyncio.gather(*flows, return_exceptions=True)
        except (OSError, ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            pass
        finally:
            if counted:
                self.active -= 1
            if upstream:
                upstream.close()
            writer.close()
            self.connections.discard(task)

    async def close(self):
        if self.server:
            self.server.close()
        # Newer asyncio versions wait for accepted connections as well as the
        # listening socket. Cancel our tunnel handlers before waiting, otherwise
        # a provider keep-alive can block cleanup and all subsequent task turns.
        for task in tuple(self.connections):
            task.cancel()
        await asyncio.gather(*tuple(self.connections), return_exceptions=True)
        if self.server:
            await self.server.wait_closed()
