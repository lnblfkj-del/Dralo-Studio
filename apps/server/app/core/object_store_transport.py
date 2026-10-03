"""Instance-scoped SDK pools: validate DNS once, connect only to vetted IPs."""

import socket
from urllib.parse import urlsplit

import requests
from botocore.awsrequest import AWSHTTPSConnection, AWSHTTPSConnectionPool
from botocore.httpsession import URLLib3Session
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.exceptions import NewConnectionError

from app.core.errors import ConflictError
from app.core.outbound_http import PublicNetworkBackend


def require_https(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ConflictError("云端对象存储必须使用无内嵌凭证的 HTTPS 公网地址")


class PublicSocket:
    def _new_conn(self):
        records = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        if not records or any(not PublicNetworkBackend.public_address(row[4][0]) for row in records):
            raise ConflictError("对象存储解析到非公网网络，已阻止访问")
        for family, socktype, proto, _, address in records:
            sock = socket.socket(family, socktype, proto)
            try:
                sock.settimeout(self.timeout)
                for option in self.socket_options or []:
                    sock.setsockopt(*option)
                if self.source_address:
                    sock.bind(self.source_address)
                # No second DNS lookup; keep self.host unchanged for TLS and signing.
                sock.connect(address)
                return sock
            except OSError:
                sock.close()
        raise NewConnectionError(self, "Object storage connection failed")


class PublicHTTPSConnection(PublicSocket, HTTPSConnection):
    pass


class PublicAWSConnection(PublicSocket, AWSHTTPSConnection):
    pass


class PublicHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = PublicHTTPSConnection


class PublicAWSPool(AWSHTTPSConnectionPool):
    ConnectionCls = PublicAWSConnection


class PublicAdapter(requests.adapters.HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"https": PublicHTTPSPool}

    def send(self, request, **kwargs):
        require_https(request.url)
        if kwargs.get("proxies"):
            raise ConflictError("云端对象存储不允许代理")
        return super().send(request, **kwargs)


def oss_transport():
    from alibabacloud_oss_v2.transport import RequestsHttpClient

    class PublicOSSClient(RequestsHttpClient):
        def _init_session(self, session):
            session.trust_env = False
            super()._init_session(session)

    return PublicOSSClient(adapter=PublicAdapter(max_retries=0),
                           connect_timeout=10, readwrite_timeout=60,
                           enabled_redirect=False)


class PublicS3Session(URLLib3Session):
    def __init__(self):
        super().__init__(timeout=(10, 60), proxies={})
        self._pool_classes_by_scheme = {"https": PublicAWSPool}
        self._manager.pool_classes_by_scheme = self._pool_classes_by_scheme

    def send(self, request):
        require_https(request.url)
        return super().send(request)


def protect_s3(client):
    # botocore has no public transport setter. Cover this pinned integration
    # alongside OSS/urllib3 whenever upgrading the object-store dependencies.
    old = client._endpoint.http_session
    client._endpoint.http_session = PublicS3Session()
    old.close()
