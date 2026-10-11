"""読み取り専用の取得（NET-06, NET-08）。

- メソッドは GET のみ（API として他のメソッドを受け付ける経路を作らない）。本文は送らない。
- Cookie・Authorization・Referer などは送らない。httpcore を直接使い、Cookie の保存先自体を持たない
  （受け取った Set-Cookie は読み捨て）。
- 接続先は `UrlGuard.resolve_pinned` で検査・固定した IP。Host ヘッダーと TLS の SNI・証明書検証は元のホスト名。
- リダイレクトは自前で処理し、毎回 URL の検査（DLP・SSRF・禁止サイト）と DNS 解決をやり直す（最大 N 回）。
- Content-Type の許可リスト、サイズ上限（Content-Length と実際の受信量の両方）、ホップごとのタイムアウト。
"""

from __future__ import annotations

import asyncio
import ipaddress
import ssl
import zlib
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin

import httpcore

from . import errors
from .config import FetchSettings
from .netguard import IPAddress, Target, UrlGuard

_REDIRECTS = {301, 302, 303, 307, 308}


class Dialer(Protocol):
    """固定した IP:port へ TCP 接続する。試験では差し替えてローカルの試験サーバーへつなぐ。"""

    async def connect(
        self, ip: IPAddress, port: int, timeout: float | None
    ) -> httpcore.AsyncNetworkStream: ...


class DirectDialer:
    def __init__(self) -> None:
        self._backend = httpcore.AnyIOBackend()

    async def connect(self, ip: IPAddress, port: int, timeout: float | None) -> httpcore.AsyncNetworkStream:
        return await self._backend.connect_tcp(str(ip), port, timeout=timeout)


class PinnedBackend(httpcore.AsyncNetworkBackend):
    """httpcore のネットワーク層。名前解決をせず、検査済みの IP にだけ接続する。"""

    def __init__(self, host: str, port: int, ip: IPAddress, dialer: Dialer) -> None:
        self._host = host
        self._port = port
        self._ip = ip
        self._dialer = dialer

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[
            tuple[int, int, int] | tuple[int, int, bytes | bytearray] | tuple[int, int, None, int]
        ]
        | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if host.strip("[]").lower() != self._host or port != self._port:
            raise httpcore.ConnectError("unexpected connect target")
        return await self._dialer.connect(self._ip, port, timeout)

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[
            tuple[int, int, int] | tuple[int, int, bytes | bytearray] | tuple[int, int, None, int]
        ]
        | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


@dataclass
class FetchResult:
    final_url: str
    mime: str
    charset: str | None
    body: bytes
    redirects: int
    status: int


@dataclass
class _Hop:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


def _parse_content_type(value: str) -> tuple[str, str | None]:
    parts = [p.strip() for p in value.split(";")]
    mime = parts[0].lower()
    charset: str | None = None
    for p in parts[1:]:
        if p.lower().startswith("charset="):
            charset = p.split("=", 1)[1].strip().strip("\"'") or None
    return mime, charset


class _Decoder:
    """Content-Encoding（gzip / deflate）を展開しながらサイズを数える（展開爆弾の対策）。"""

    def __init__(self, encoding: str, limit: int) -> None:
        self._limit = limit
        self._size = 0
        self._chunks: list[bytes] = []
        enc = encoding.strip().lower()
        if enc in ("", "identity"):
            self._z: zlib._Decompress | None = None
        elif enc in ("gzip", "x-gzip"):
            self._z = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif enc == "deflate":
            self._z = zlib.decompressobj()
        else:
            raise errors.upstream("UPSTREAM_UNSUPPORTED_ENCODING", "対応していない圧縮形式です")

    def _push(self, data: bytes) -> None:
        self._size += len(data)
        if self._size > self._limit:
            raise errors.upstream("RESPONSE_TOO_LARGE", "応答が大きすぎるため取得を中止しました")
        self._chunks.append(data)

    def feed(self, chunk: bytes) -> None:
        if self._z is None:
            self._push(chunk)
            return
        try:
            remaining = self._limit - self._size + 1
            data = self._z.decompress(chunk, remaining)
            self._push(data)
            if self._z.unconsumed_tail:
                raise errors.upstream("RESPONSE_TOO_LARGE", "応答が大きすぎるため取得を中止しました")
        except zlib.error as e:
            raise errors.upstream("UPSTREAM_BAD_RESPONSE", "応答を展開できませんでした") from e

    def result(self) -> bytes:
        if self._z is not None:
            try:
                self._push(self._z.flush())
            except zlib.error as e:
                raise errors.upstream("UPSTREAM_BAD_RESPONSE", "応答を展開できませんでした") from e
        return b"".join(self._chunks)


UrlChecker = Callable[[str], Awaitable[Target]]


class Fetcher:
    def __init__(
        self,
        settings: FetchSettings,
        guard: UrlGuard,
        *,
        dialer: Dialer | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._s = settings
        self._guard = guard
        self._dialer: Dialer = dialer or DirectDialer()
        self._ssl = ssl_context or httpcore.default_ssl_context()
        self._allowed_types = {t.lower() for t in settings.allowed_content_types}

    async def fetch(self, url: str, check: UrlChecker) -> FetchResult:
        """`check` は URL ごとの検査（DLP・SSRF の静的検査・禁止サイト）。リダイレクト先にも毎回かける。"""
        current = url
        for redirects in range(self._s.max_redirects + 1):
            target = await check(current)
            ip = await self._guard.resolve_pinned(target)
            hop = await self._one_hop(target, ip)
            if hop.status in _REDIRECTS:
                location = hop.headers.get("location")
                if not location:
                    raise errors.upstream("UPSTREAM_BAD_RESPONSE", "リダイレクト先が指定されていません")
                current = urljoin(target.url, location.strip())
                continue
            mime, charset = _parse_content_type(hop.headers.get("content-type", ""))
            return FetchResult(
                final_url=target.url,
                mime=mime,
                charset=charset,
                body=hop.body,
                redirects=redirects,
                status=hop.status,
            )
        raise errors.upstream("TOO_MANY_REDIRECTS", "リダイレクトが多すぎます")

    def _headers(self, target: Target) -> list[tuple[bytes, bytes]]:
        host = f"[{target.host}]" if isinstance(target.ip_literal, ipaddress.IPv6Address) else target.host
        default_port = 443 if target.scheme == "https" else 80
        host_header = host if target.port == default_port else f"{host}:{target.port}"
        accept = ", ".join(self._s.allowed_content_types)
        # Cookie / Authorization / Referer / Origin は送らない（NET-06）
        return [
            (b"Host", host_header.encode("ascii")),
            (b"User-Agent", self._s.user_agent.encode("ascii", "replace")),
            (b"Accept", accept.encode("ascii")),
            (b"Accept-Language", b"ja,en;q=0.8"),
            (b"Accept-Encoding", b"gzip, deflate"),
            (b"Connection", b"close"),
        ]

    async def _one_hop(self, target: Target, ip: IPAddress) -> _Hop:
        backend = PinnedBackend(target.host, target.port, ip, self._dialer)
        hc_url = httpcore.URL(
            scheme=target.scheme.encode("ascii"),
            host=target.host.encode("ascii"),
            port=target.port,
            target=target.request_target.encode("ascii"),
        )
        timeouts = {
            "connect": self._s.connect_timeout_s,
            "read": self._s.timeout_s,
            "write": self._s.timeout_s,
            "pool": self._s.connect_timeout_s,
        }
        try:
            async with asyncio.timeout(self._s.timeout_s):
                async with httpcore.AsyncConnectionPool(
                    ssl_context=self._ssl,
                    max_connections=1,
                    http1=True,
                    http2=False,
                    retries=0,
                    network_backend=backend,
                ) as pool:
                    async with pool.stream(
                        "GET",
                        hc_url,
                        headers=self._headers(target),
                        extensions={"timeout": timeouts, "sni_hostname": target.host},
                    ) as resp:
                        headers: dict[str, str] = {}
                        for k, v in resp.headers:
                            name = k.decode("latin-1").lower()
                            if name == "set-cookie":
                                continue  # 保存も転送もしない
                            headers[name] = v.decode("latin-1")
                        hop = _Hop(status=resp.status, headers=headers)
                        if resp.status in _REDIRECTS:
                            return hop
                        if not 200 <= resp.status < 300:
                            raise errors.upstream(
                                "UPSTREAM_HTTP_ERROR",
                                f"取得先が HTTP {resp.status} を返しました",
                                retryable=resp.status >= 500 or resp.status == 429,
                            )
                        mime, _ = _parse_content_type(headers.get("content-type", ""))
                        if mime not in self._allowed_types:
                            raise errors.upstream(
                                "CONTENT_TYPE_NOT_ALLOWED",
                                "この種類のファイルは取得できません（HTML・PDF・テキストのみ）",
                            )
                        length = headers.get("content-length")
                        if (
                            length is not None
                            and length.strip().isdigit()
                            and int(length) > self._s.max_bytes
                        ):
                            raise errors.upstream(
                                "RESPONSE_TOO_LARGE", "応答が大きすぎるため取得を中止しました"
                            )
                        decoder = _Decoder(headers.get("content-encoding", ""), self._s.max_bytes)
                        async for chunk in resp.aiter_stream():
                            decoder.feed(chunk)
                        hop.body = decoder.result()
                        return hop
        except errors.RelayError:
            raise
        except TimeoutError as e:
            raise errors.upstream("UPSTREAM_TIMEOUT", "取得先の応答がありませんでした", retryable=True) from e
        except httpcore.ConnectError as e:
            raise errors.upstream(
                "UPSTREAM_CONNECT_FAILED", "取得先に接続できませんでした", retryable=True
            ) from e
        except httpcore.TimeoutException as e:
            raise errors.upstream("UPSTREAM_TIMEOUT", "取得先の応答がありませんでした", retryable=True) from e
        except (httpcore.NetworkError, httpcore.ProtocolError, ssl.SSLError) as e:
            raise errors.upstream(
                "UPSTREAM_BAD_RESPONSE", "取得先との通信に失敗しました", retryable=True
            ) from e
