// 予備機への切替（OPS-03）: 本番機に接続できない状態が続いたら予備機へ、本番機が戻ったら切り戻す。
import { describe, expect, it } from "vitest";
import { GatewayClient, TransientError } from "../src/gateway/client.js";

const tokens = { accessToken: async () => "t", refresh: async () => "t" };

function fakeNet() {
  const down = new Set<string>();
  const calls: string[] = [];
  const f = (async (input: string | URL | Request) => {
    const url = String(input);
    calls.push(url);
    const host = new URL(url).host;
    if (down.has(host)) throw new TypeError("fetch failed");
    if (url.endsWith("/healthz")) return new Response("ok");
    return new Response(JSON.stringify({ host }), { headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  return { f, down, calls };
}

describe("GatewayClient failover (OPS-03)", () => {
  it("switches to the spare after repeated connection failures and back when the primary recovers", async () => {
    const net = fakeNet();
    let now = 0;
    const c = new GatewayClient(["http://primary:8443/", "http://spare:8443"], tokens, { fetch: net.f, failoverAfter: 3, probeIntervalMs: 60_000, now: () => now });
    const switches: [string, number][] = [];
    c.onSwitch((url, i) => switches.push([url, i]));
    expect(c.currentUrl).toBe("http://primary:8443");
    expect(await c.post<{ host: string }>("/v1/x", {})).toEqual({ host: "primary:8443" });

    net.down.add("primary:8443");
    for (let i = 0; i < 3; i++) await expect(c.post("/v1/x", {})).rejects.toBeInstanceOf(TransientError);
    expect(c.currentUrl).toBe("http://spare:8443");
    expect(switches).toEqual([["http://spare:8443", 1]]);
    expect(await c.post<{ host: string }>("/v1/x", {})).toEqual({ host: "spare:8443" });

    // 確認の間隔が来るまでは本番機を見に行かない
    net.down.delete("primary:8443");
    now += 30_000;
    await c.post("/v1/x", {});
    expect(c.currentUrl).toBe("http://spare:8443");
    now += 31_000;
    expect(await c.post<{ host: string }>("/v1/x", {})).toEqual({ host: "primary:8443" });
    expect(switches.at(-1)).toEqual(["http://primary:8443", 0]);
    expect(net.calls).toContain("http://primary:8443/healthz");
  });

  it("does not fail over on HTTP errors (the server is reachable)", async () => {
    const net = fakeNet();
    const f = (async (input: string | URL | Request) => {
      net.calls.push(String(input));
      return new Response(JSON.stringify({ error: { code: "ENGINE_UNAVAILABLE", retryable: true } }), { status: 503 });
    }) as typeof fetch;
    const c = new GatewayClient(["http://primary", "http://spare"], tokens, { fetch: f, failoverAfter: 2 });
    for (let i = 0; i < 4; i++) await expect(c.post("/v1/x", {})).rejects.toBeInstanceOf(TransientError);
    expect(c.currentUrl).toBe("http://primary");
  });

  it("a single URL never switches", async () => {
    const net = fakeNet();
    net.down.add("only");
    const c = new GatewayClient("http://only", tokens, { fetch: net.f, failoverAfter: 1 });
    for (let i = 0; i < 3; i++) await expect(c.post("/v1/x", {})).rejects.toBeInstanceOf(TransientError);
    expect(c.currentUrl).toBe("http://only");
  });
});
