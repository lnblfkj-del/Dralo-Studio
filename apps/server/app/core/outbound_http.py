"""Cloud outbound requests: public DNS pinned at connection time, no proxies."""

import asyncio
import ipaddress
import socket

import httpcore
import httpx

from app.core.config import settings
from app.core.errors import ConflictError


class PublicNetworkBackend(httpcore.AnyIOBackend):
    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        async def connect():
            try:
                records = await asyncio.get_running_loop().getaddrinfo(
                    host, port, type=socket.SOCK_STREAM)
            except OSError:
                raise httpcore.ConnectError("Outbound DNS lookup failed") from None
            addresses = list(dict.fromkeys(record[4][0] for record in records))
            if not addresses or any(not self.public_address(address) for address in addresses):
                raise ConflictError("云端模型或素材地址解析到非公网网络，已阻止访问")
            # Connect to the validated numeric address, never resolve the name
            # again. httpcore retains the original Host and TLS server name.
            for address in addresses:
                try:
                    return await super(PublicNetworkBackend, self).connect_tcp(
                        address, port, timeout, local_address, socket_options)
                except (httpcore.ConnectError, httpcore.ConnectTimeout):
                    continue
            raise httpcore.ConnectError("Outbound connection failed")

        try:
            return await asyncio.wait_for(connect(), timeout=timeout)
        except asyncio.TimeoutError:
            raise httpcore.ConnectTimeout("Outbound connection timed out") from None

    @staticmethod
    def public_address(value):
        address = ipaddress.ip_address(value)
        return (address.is_global and not address.is_multicast and not address.is_unspecified
                and not getattr(address, "is_site_local", False)
                and not (address.version == 6 and address in ipaddress.ip_network("64:ff9b::/96"))
                and not getattr(address, "ipv4_mapped", None)
                and not getattr(address, "sixtofour", None)
                and not getattr(address, "teredo", None))


class PublicTransport(httpx.AsyncHTTPTransport):
    def __init__(self):
        super().__init__(trust_env=False)
        # httpx has no public backend argument. Keep this single integration
        # point covered by real transport tests when upgrading httpx/httpcore.
        self._pool = httpcore.AsyncConnectionPool(network_backend=PublicNetworkBackend())

    async def handle_async_request(self, request):
        if request.url.scheme not in {"http", "https"} or request.url.userinfo:
            raise ConflictError("云端出站地址仅支持不含账号密码的 HTTP/HTTPS 地址")
        return await super().handle_async_request(request)


def outbound_client(**kwargs):
    if settings.runtime_execution_location != "cloud":
        return httpx.AsyncClient(**kwargs)
    if kwargs.pop("proxy", None):
        raise ConflictError("云端不支持用户自定义出站代理，请移除模型代理配置")
    if any(key in kwargs for key in ("transport", "mounts")):
        raise ValueError("Cloud outbound transport cannot be overridden")
    kwargs.update(trust_env=False, follow_redirects=False, transport=PublicTransport())
    return httpx.AsyncClient(**kwargs)
