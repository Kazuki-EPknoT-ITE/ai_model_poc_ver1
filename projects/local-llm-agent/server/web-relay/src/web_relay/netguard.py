"""SSRF 防御（NET-08, test_spec §5.4）。

- スキームは http / https のみ。userinfo（user:pass@）付きは拒否（認証情報を送らない。NET-06）。
- ポートはスキームごとの許可リストのみ（既定 80/443）。
- IP の直書きは正規の表記（IPv4 のドット10進・IPv6）のみ受け付け、10進/8進/16進・省略形は拒否する。
- ホスト名は自前で DNS 解決し、**解決したすべての IP** が公開アドレスであることを確認してから、
  そのうちの1つに固定して接続する（DNS rebinding 対策。接続側は `fetcher.PinnedBackend`）。
- localhost・ドットのない名前・社内ドメイン（サフィックス一致）・禁止サイトは常に拒否。
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import quote, urlsplit

from . import errors

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_HOST_LABEL = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$")
_NUMERIC_LABEL = re.compile(r"^(?:0x[0-9a-f]*|[0-9]+)$")

# Python の is_global に加えて明示的に拒否する帯（版による差を吸収するため重ねて書く）
_ALWAYS_BLOCKED_V4 = tuple(
    ipaddress.IPv4Network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",  # CGNAT
        "127.0.0.0/8",
        "169.254.0.0/16",  # リンクローカル（クラウドのメタデータ 169.254.169.254 を含む）
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",  # マルチキャスト
        "240.0.0.0/4",  # 予約・ブロードキャスト
    )
)
_ALWAYS_BLOCKED_V6 = tuple(
    ipaddress.IPv6Network(n)
    for n in (
        "::/128",
        "::1/128",
        "::/96",  # IPv4 互換（廃止）
        "::ffff:0:0/96",  # IPv4 射影（埋め込みの v4 も別途判定する）
        "64:ff9b:1::/48",  # ローカル用 NAT64
        "100::/64",  # 破棄用
        "2001::/32",  # Teredo（中の v4 を隠せるので丸ごと拒否）
        "2001:db8::/32",  # 文書用
        "fc00::/7",  # ULA
        "fe80::/10",  # リンクローカル
        "fec0::/10",  # サイトローカル（廃止）
        "ff00::/8",  # マルチキャスト
    )
)
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")
_6TO4 = ipaddress.IPv6Network("2002::/16")


def ip_is_forbidden(
    ip: IPAddress, extra: Sequence[ipaddress.IPv4Network | ipaddress.IPv6Network] = ()
) -> bool:
    """公開アドレス以外なら True。IPv6 に埋め込まれた IPv4 も取り出して判定する。"""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.scope_id:
            return True
        mapped = ip.ipv4_mapped
        if mapped is not None:
            return True  # IPv4 射影は中身に関わらず拒否（正規の公開 IPv4 で書けばよい）
        if ip in _NAT64:
            return ip_is_forbidden(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF), extra)
        if ip in _6TO4 and ip.sixtofour is not None and ip_is_forbidden(ip.sixtofour, extra):
            return True
        if any(ip in n for n in _ALWAYS_BLOCKED_V6):
            return True
    else:
        if any(ip in n for n in _ALWAYS_BLOCKED_V4):
            return True
    if any(ip.version == n.version and ip in n for n in extra):
        return True
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


class Resolver(Protocol):
    async def resolve(self, host: str, port: int) -> list[IPAddress]: ...


class SystemResolver:
    """OS のリゾルバで解決する（`getaddrinfo`）。"""

    def __init__(self, timeout_s: float = 5.0) -> None:
        self._timeout = timeout_s

    async def resolve(self, host: str, port: int) -> list[IPAddress]:
        loop = asyncio.get_running_loop()
        async with asyncio.timeout(self._timeout):
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        out: list[IPAddress] = []
        for _family, _type, _proto, _canon, sockaddr in infos:
            addr = str(sockaddr[0])
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                # スコープ付き（fe80::1%eth0）などは拒否対象として扱う
                ip = ipaddress.IPv6Address("fe80::1")
            if ip not in out:
                out.append(ip)
        return out


@dataclass(frozen=True)
class SiteRule:
    pattern: str  # 正規化済み（小文字・ASCII）。'*.' で始まるものは配下とその親ドメインに一致
    kind: Literal["block", "trusted"]

    def matches(self, host: str) -> bool:
        if self.pattern.startswith("*."):
            base = self.pattern[2:]
            return host == base or host.endswith("." + base)
        return host == self.pattern


def normalize_site_pattern(pattern: str) -> str:
    p = pattern.strip().lower().rstrip(".")
    wildcard = p.startswith("*.")
    body = p[2:] if wildcard else p
    try:
        body = body.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return ("*." if wildcard else "") + body


class SitePolicy:
    """禁止サイト・信頼サイト（net_sites）と社内ドメイン。"""

    def __init__(self, rules: Iterable[SiteRule], internal_domains: Iterable[str]) -> None:
        self._rules = list(rules)
        self._internal = sorted({d.strip().lower().strip(".") for d in internal_domains if d.strip()})

    def is_internal(self, host: str) -> bool:
        if "." not in host:
            return True  # ドットのない名前（intranet, localhost など）は社内名とみなす
        if host == "localhost" or host.endswith(".localhost"):
            return True
        return any(host == d or host.endswith("." + d) for d in self._internal)

    def is_blocked(self, host: str) -> bool:
        return any(r.kind == "block" and r.matches(host) for r in self._rules)

    def is_trusted(self, host: str) -> bool:
        return any(r.kind == "trusted" and r.matches(host) for r in self._rules)


@dataclass(frozen=True)
class Target:
    """検査を通過した取得先。`url` はリクエストに使う ASCII の URL（フラグメントなし）。"""

    url: str
    scheme: str
    host: str  # ASCII（IDNA）・小文字
    port: int
    ip_literal: IPAddress | None
    request_target: str  # パス＋クエリ（ASCII）


def _canonical_host(raw: str) -> tuple[str, IPAddress | None]:
    """ホストを検査して (ASCII ホスト, IP 直書きならその IP) を返す。不正なら DOMAIN_BLOCKED。"""
    host = raw.strip().lower()
    if not host:
        raise errors.domain_blocked()
    if ":" in host:  # IPv6（urlsplit で角括弧は外れている）
        if "%" in host:
            raise errors.domain_blocked()
        try:
            return host, ipaddress.IPv6Address(host)
        except ValueError as e:
            raise errors.domain_blocked() from e
    host = host.rstrip(".")
    try:
        return host, ipaddress.IPv4Address(host)  # 厳密なドット10進のみ（先頭0は不可）
    except ValueError:
        pass
    labels = host.split(".")
    # 10進・8進・16進・省略形の IPv4 表記、または最後のラベルが数字のもの（WHATWG では IPv4 として扱われる）
    if all(_NUMERIC_LABEL.match(lbl) for lbl in labels) or _NUMERIC_LABEL.match(labels[-1]):
        raise errors.domain_blocked()
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise errors.domain_blocked() from e
    if len(ascii_host) > 253 or not all(_HOST_LABEL.match(lbl) for lbl in ascii_host.split(".")):
        raise errors.domain_blocked()
    return ascii_host, None


class UrlGuard:
    def __init__(
        self,
        *,
        allowed_ports: dict[str, list[int]],
        extra_blocked_networks: Iterable[str] = (),
        resolver: Resolver,
    ) -> None:
        self._ports = {k.lower(): set(v) for k, v in allowed_ports.items()}
        self._extra: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
            ipaddress.ip_network(n, strict=False) for n in extra_blocked_networks
        ]
        self._resolver = resolver

    def check_static(self, url: str, sites: SitePolicy) -> Target:
        """DNS を使わずに判定できる部分（スキーム・ポート・ホストの形・社内/禁止サイト・IP 直書き）。"""
        if len(url) > 2048 or any(ord(c) < 0x20 or c == "\x7f" for c in url) or "\\" in url:
            raise errors.domain_blocked()
        try:
            parts = urlsplit(url.strip())
            port_opt = parts.port
            hostname = parts.hostname
        except ValueError as e:
            raise errors.domain_blocked() from e
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https") or scheme not in self._ports:
            raise errors.domain_blocked()
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            raise errors.domain_blocked()
        if hostname is None:
            raise errors.domain_blocked()
        port = port_opt if port_opt is not None else (443 if scheme == "https" else 80)
        if port not in self._ports[scheme]:
            raise errors.domain_blocked()
        host, ip = _canonical_host(hostname)
        if ip is not None:
            if ip_is_forbidden(ip, self._extra):
                raise errors.domain_blocked()
        elif sites.is_internal(host):
            raise errors.domain_blocked()
        if sites.is_blocked(host):
            raise errors.domain_blocked()
        path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parts.query, safe="/%:@!$&'()*+,;=-._~?")
        netloc_host = f"[{host}]" if isinstance(ip, ipaddress.IPv6Address) else host
        default_port = 443 if scheme == "https" else 80
        netloc = netloc_host if port == default_port else f"{netloc_host}:{port}"
        request_target = path + (f"?{query}" if query else "")
        return Target(
            url=f"{scheme}://{netloc}{request_target}",
            scheme=scheme,
            host=host,
            port=port,
            ip_literal=ip,
            request_target=request_target,
        )

    async def resolve_pinned(self, target: Target) -> IPAddress:
        """解決したすべての IP を検査し、接続に使う1つを返す（以後この IP にだけ接続する）。"""
        if target.ip_literal is not None:
            return target.ip_literal
        try:
            ips = await self._resolver.resolve(target.host, target.port)
        except (OSError, TimeoutError) as e:
            raise errors.upstream(
                "UPSTREAM_DNS_FAILED", "取得先の名前を解決できませんでした", retryable=True
            ) from e
        if not ips:
            raise errors.upstream("UPSTREAM_DNS_FAILED", "取得先の名前を解決できませんでした", retryable=True)
        if any(ip_is_forbidden(ip, self._extra) for ip in ips):
            raise errors.domain_blocked()
        v4 = [ip for ip in ips if isinstance(ip, ipaddress.IPv4Address)]
        return v4[0] if v4 else ips[0]
