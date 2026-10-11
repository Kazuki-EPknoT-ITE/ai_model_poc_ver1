"""SSRF（test_spec §5.4 RX-01〜09）と DNS rebinding・リダイレクトの再判定。

合否は「拒否されたこと」と「接続が試みられなかったこと（MappingDialer の記録）」の両方で確認する。
"""

from __future__ import annotations

import ipaddress

import pytest
from wr_helpers import PUBLIC_IP, Env, FakeResolver, LocalServer, RebindingResolver, build_env

from web_relay import errors
from web_relay.netguard import SitePolicy, UrlGuard, ip_is_forbidden


@pytest.mark.parametrize(
    "url",
    [
        # RX-01 ループバック
        "http://127.0.0.1/",
        "http://127.0.0.2/x",
        "http://localhost/",
        "http://LOCALHOST./",
        "http://foo.localhost/",
        "http://[::1]/",
        # RX-02 プライベート
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        # RX-03 リンクローカル・メタデータ
        "http://169.254.169.254/latest/meta-data/",
        "http://[fe80::1]/",
        # RX-04 10進・16進・8進・省略形
        "http://2130706433/",
        "http://0x7f.1/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://127.1/",
        "http://017700000001/",
        # RX-05 IPv4 射影・埋め込み
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:7f00:1]/",
        "http://[::ffff:8.8.8.8]/",
        "http://[64:ff9b::7f00:1]/",
        "http://[2002:7f00:1::]/",
        "http://[::127.0.0.1]/",
        # その他の予約帯
        "http://0.0.0.0/",
        "http://100.64.0.1/",  # CGNAT
        "http://224.0.0.1/",  # マルチキャスト
        "http://255.255.255.255/",
        "http://[fd00::1]/",  # ULA
        "http://[ff02::1]/",
        "http://[fe80::1%25eth0]/",
        # RX-08 社内ドメイン・ドットのない名前
        "http://intranet.hirai.local/",
        "http://wiki.internal/",
        "http://portal.hirai.co.jp/",
        "http://intranet/",
        # RX-09 スキーム
        "file:///etc/passwd",
        "ftp://example.com/",
        "gopher://example.com/",
        "javascript:alert(1)",
        "data:text/html,hi",
        # ポート・認証情報
        "http://public.test:8080/",
        "https://public.test:80/",
        "http://public.test:22/",
        "http://user:pass@public.test/",
        "http://public.test@127.0.0.1/",
        # 禁止サイト
        "http://www.blocked.test/",
        "http://blocked.test/",
        "http://evil-org.test/",
    ],
)
def test_rejected_without_connecting(env: Env, url: str) -> None:
    r = env.fetch(url)
    assert r.status_code == 403, (url, r.text)
    assert r.json()["error"]["code"] == "DOMAIN_BLOCKED"
    assert env.dialer.connects == []
    assert env.server.requests == []


@pytest.mark.parametrize(
    "host", ["evil.test", "mixed.test", "meta.test", "v6loop.test", "mapped.test", "cgnat.test"]
)
def test_hostname_resolving_to_private_is_rejected(env: Env, host: str) -> None:
    r = env.fetch(f"http://{host}/page")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "DOMAIN_BLOCKED"
    assert env.resolver.calls == [host]
    assert env.dialer.connects == []


def test_public_host_is_fetched_via_pinned_ip(env: Env) -> None:
    r = env.fetch("http://public.test/page")
    assert r.status_code == 200, r.text
    assert env.dialer.connects == [(PUBLIC_IP, 80)]
    # Host ヘッダーは元のホスト名
    assert env.server.requests[0].headers["host"] == "public.test"


def test_redirect_to_private_is_rejected(env: Env) -> None:
    # RX-06: 公開ドメイン → 302 で社内アドレス
    r = env.fetch("http://public.test/redirect-private")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "DOMAIN_BLOCKED"
    assert env.dialer.connects == [(PUBLIC_IP, 80)]  # 10.0.0.5 へは接続しない


def test_redirect_to_localhost_name_and_file_are_rejected(env: Env) -> None:
    for path in ("/redirect-loopback-name", "/redirect-file"):
        r = env.fetch(f"http://public.test{path}")
        assert r.status_code == 403, path
        assert r.json()["error"]["code"] == "DOMAIN_BLOCKED"
    assert all(ip == PUBLIC_IP for ip, _ in env.dialer.connects)


def test_redirect_to_public_is_followed_and_rechecked(env: Env) -> None:
    r = env.fetch("http://public.test/redirect-public")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["final_url"] == "http://other.test/page2"
    assert env.resolver.calls == ["public.test", "other.test"]  # リダイレクトのたびに解決し直す


def test_redirect_with_dlp_term_is_blocked(env: Env) -> None:
    r = env.fetch("http://public.test/redirect-dlp")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "DLP_BLOCKED"
    assert len(env.dialer.connects) == 1


def test_too_many_redirects(env: Env) -> None:
    r = env.fetch("http://public.test/redirect-loop")
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "TOO_MANY_REDIRECTS"
    assert len(env.dialer.connects) == 6  # 最初の1回＋リダイレクト5回


def test_dns_rebinding_connects_only_to_first_public_ip(http_server: LocalServer) -> None:
    # RX-07: 1回目は公開 IP、2回目は 127.0.0.1 を返す解決器
    resolver = RebindingResolver({})
    e = build_env(http_server, resolver=resolver)
    r = e.fetch("http://rebind.test/page")
    assert r.status_code == 200, r.text
    assert resolver.calls == ["rebind.test"]  # 1回だけ解決し、その IP に固定して接続
    assert e.dialer.connects == [(PUBLIC_IP, 80)]


def test_dns_rebinding_on_redirect_is_rejected(http_server: LocalServer) -> None:
    # 同じホストへのリダイレクトで2回目の解決がループバックになる → 拒否（127.0.0.1 へは接続しない）
    resolver = RebindingResolver({})
    e = build_env(http_server, resolver=resolver)
    r = e.fetch("http://rebind.test/redirect-self")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "DOMAIN_BLOCKED"
    assert e.dialer.connects == [(PUBLIC_IP, 80)]


def test_dns_failure_is_reported_without_connecting(http_server: LocalServer) -> None:
    e = build_env(http_server, resolver=FakeResolver({}))
    r = e.fetch("http://unknown.test/")
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "UPSTREAM_DNS_FAILED"
    assert e.dialer.connects == []


def test_custom_ports_setting() -> None:
    guard = UrlGuard(allowed_ports={"http": [80, 8080], "https": [443]}, resolver=FakeResolver({}))
    sites = SitePolicy([], [])
    assert guard.check_static("http://public.test:8080/x", sites).port == 8080
    with pytest.raises(errors.RelayError):
        guard.check_static("https://public.test:8443/x", sites)


def test_extra_blocked_networks() -> None:
    nets = [ipaddress.ip_network("8.8.8.0/24")]
    assert ip_is_forbidden(ipaddress.ip_address("8.8.8.8"), nets)
    assert not ip_is_forbidden(ipaddress.ip_address("8.8.4.4"), nets)
    assert not ip_is_forbidden(ipaddress.ip_address("2606:4700::1111"))
    assert ip_is_forbidden(ipaddress.ip_address("2001:db8::1"))


def test_idn_host_is_converted_to_ascii() -> None:
    guard = UrlGuard(allowed_ports={"http": [80]}, resolver=FakeResolver({}))
    t = guard.check_static("http://例え.テスト/パス?q=値", SitePolicy([], []))
    assert t.host == "xn--r8jz45g.xn--zckzah"
    assert t.request_target.isascii()
