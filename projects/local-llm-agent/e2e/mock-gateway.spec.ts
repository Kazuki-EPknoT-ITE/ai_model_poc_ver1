// E2E: モック Gateway（local_llm_api.md の主要フロー）。T-05 の完了確認。
import { expect, test, type APIRequestContext } from "@playwright/test";
import { validateDef } from "@lla/protocol";

const ARTIFACTS = process.env.E2E_ARTIFACTS ?? "test-results";

interface SseEvent {
  id: number;
  event: string;
  data: Record<string, unknown>;
}

function parseSse(text: string): SseEvent[] {
  return text
    .split(/\r?\n\r?\n/)
    .map((block) => block.split(/\r?\n/).filter((l) => l && !l.startsWith(":")))
    .filter((lines) => lines.length > 0)
    .map((lines) => {
      const f = Object.fromEntries(lines.map((l) => [l.slice(0, l.indexOf(":")), l.slice(l.indexOf(":") + 2)]));
      return { id: Number(f.id), event: f.event ?? "", data: JSON.parse(f.data ?? "{}") as Record<string, unknown> };
    });
}

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const r = await request.post("/v1/auth/login", { data: { login: "dev", password: "dev", device_name: "e2e" } });
  expect(r.status()).toBe(200);
  const body = (await r.json()) as { access_token: string };
  return { Authorization: `Bearer ${body.access_token}` };
}

async function runStep(request: APIRequestContext, h: Record<string, string>, key: string, messages: object[]) {
  const sub = await request.post("/v1/steps", {
    headers: { ...h, "Idempotency-Key": key },
    data: { model: "local-12b", messages, stream: true },
  });
  expect(sub.status()).toBe(202);
  const { job_id } = (await sub.json()) as { job_id: string };
  const ev = await request.get(`/v1/steps/${job_id}/events`, { headers: h });
  expect(ev.headers()["content-type"]).toContain("text/event-stream");
  const events = parseSse(await ev.text());
  const done = events.at(-1);
  expect(done?.event).toBe("done");
  const output = String(done?.data.output);
  const streamed = events.filter((e) => e.event === "token").map((e) => String(e.data.delta)).join("");
  expect(streamed).toBe(output);
  const action = JSON.parse(output) as { type: string; tool?: string };
  expect(validateDef("Action", action).errors).toEqual([]);
  return { action, events, job_id };
}

test("health, auth and config", async ({ request }) => {
  expect((await request.get("/healthz")).ok()).toBe(true);
  expect((await request.get("/v1/config")).status()).toBe(401);
  const h = await login(request);
  const cfg = (await (await request.get("/v1/config", { headers: h })).json()) as {
    protocol_version: string;
    models: { id: string; available: boolean }[];
  };
  expect(cfg.protocol_version).toBe("1.0");
  expect(cfg.models.find((m) => m.id === "local-12b")?.available).toBe(true);
});

test("smoke scenario: plan → fs.list → final, with resume and idempotency", async ({ request }) => {
  const h = await login(request);
  const run = `e2e-${Date.now()}`;
  const user = [{ role: "system", content: "sys" }, { role: "user", content: "フォルダを確認して" }];

  const s0 = await runStep(request, h, `${run}:1:0`, user);
  expect(s0.action.type).toBe("plan_update");

  // 同じ Idempotency-Key の再投入は同じジョブを返す（200）
  const again = await request.post("/v1/steps", {
    headers: { ...h, "Idempotency-Key": `${run}:1:0` },
    data: { model: "local-12b", messages: user, stream: true },
  });
  expect(again.status()).toBe(200);
  expect(((await again.json()) as { job_id: string }).job_id).toBe(s0.job_id);

  // Last-Event-ID で途中から取り直せる
  const resumed = await request.get(`/v1/steps/${s0.job_id}/events`, { headers: { ...h, "Last-Event-ID": "2" } });
  expect(parseSse(await resumed.text())).toEqual(s0.events.slice(2));

  const s1 = await runStep(request, h, `${run}:2:0`, user);
  expect(s1.action).toMatchObject({ type: "tool_call", tool: "fs.list" });

  const toolResult = { role: "user", content: '[tool_result step=2 tool=fs.list ok=true]\n{"entries":[]}\n[/tool_result]' };
  const s2 = await runStep(request, h, `${run}:3:0`, [...user, toolResult]);
  expect(s2.action.type).toBe("final");
});

test("browser can open the readiness page", async ({ page }) => {
  await page.goto("/readyz");
  await expect(page.locator("body")).toContainText('"scenario":"smoke"');
  await page.screenshot({ path: `${ARTIFACTS}/mock-gateway-readyz.png` });
});
