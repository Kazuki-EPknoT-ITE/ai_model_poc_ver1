// 管理画面の E2E（ADM-02〜04, NET-02/07/08, AGT-10, SEC-05）
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const ARTIFACTS = process.env.E2E_ARTIFACTS ?? "test-results";
// 本物の Gateway（server/gateway/scripts/dev_server.py）とモックで資格情報が違う
const REAL = process.env.ADMIN_E2E_REAL === "1";
const ADMIN_PW = REAL ? "admin-password-1" : "admin";
const DEV_PW = REAL ? "dev-password-1" : "dev";

async function loginAsAdmin(page: Page) {
  await page.goto("/admin/");
  await page.getByLabel("ログインID").fill("admin");
  await page.getByLabel("パスワード").fill(ADMIN_PW);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByRole("tablist", { name: "管理メニュー" })).toBeVisible();
}

test("non-admin users cannot sign in", async ({ page }) => {
  await page.goto("/admin/");
  await page.getByLabel("ログインID").fill("dev");
  await page.getByLabel("パスワード").fill(DEV_PW);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByRole("alert")).toHaveText("管理者のアカウントでログインしてください");
});

test("dashboard, users, DLP, network, instructions and audit", async ({ page }) => {
  await loginAsAdmin(page);
  await expect(page.getByTestId("tile").first()).toBeVisible();
  // GPU の値: モックは決め打ち、本物の Gateway は Prometheus 側で集めるので「取得できません」
  await expect(page.getByRole("region", { name: "ダッシュボード" })).toContainText(REAL ? "GPU の情報を取得できません" : "RTX 5080");
  await page.screenshot({ path: join(ARTIFACTS, "admin-dashboard.png") });

  // 利用者: 追加 → ネットの上限を変更 → 無効化
  await page.getByRole("tab", { name: "利用者" }).click();
  const add = page.getByRole("form", { name: "利用者を追加" });
  await add.getByLabel("ログインID").fill("tanaka");
  await add.getByLabel("表示名").fill("田中");
  await add.getByLabel("初期パスワード").fill("short");
  await add.getByRole("button", { name: "追加" }).click();
  await expect(page.getByRole("alert")).toContainText("12文字以上");
  await add.getByLabel("初期パスワード").fill("correct-horse-battery");
  await add.getByRole("button", { name: "追加" }).click();
  await expect(page.getByRole("status")).toHaveText("tanaka を追加しました");
  const row = page.getByTestId("user-tanaka");
  await row.getByLabel("tanaka のネットの上限").selectOption("search");
  await expect(page.getByRole("status")).toHaveText("ネットの上限を変更しました");
  await row.getByRole("button", { name: "無効にする" }).click();
  await expect(row).toContainText("無効");

  // 機密語辞書: 登録すると一覧では伏せ字になる
  await page.getByRole("tab", { name: "機密語辞書" }).click();
  await page.getByLabel("機密語", { exact: true }).fill(["平井丸", "HIRAI MARU,vessel,ひらいまる/hirai maru", "平井丸"].join("\n"));
  await page.getByRole("button", { name: "登録" }).click();
  await expect(page.getByRole("status")).toContainText("2 件を登録しました（重複 1 件は飛ばしました）");
  await expect(page.getByTestId("dlp-term")).toHaveCount(2);
  const list = await page.getByRole("table", { name: "機密語の一覧" }).innerText();
  expect(list).not.toContain("平井丸");
  expect(list).not.toContain("HIRAI MARU");
  expect(list).toContain("平井*");

  // ネット: 禁止サイトを追加し、全社で停止
  await page.getByRole("tab", { name: "ネットアクセス" }).click();
  await page.getByLabel("サイト", { exact: true }).fill("*.example.com");
  await page.getByLabel("種類", { exact: true }).selectOption("block");
  await page.getByRole("button", { name: "追加" }).click();
  await expect(page.getByRole("table", { name: "サイトの一覧" })).toContainText("*.example.com");
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: "全社で停止" }).click();
  await expect(page.getByRole("status")).toHaveText("全社のネットアクセスを停止しました");
  await expect(page.getByLabel("全社の上限")).toHaveValue("off");

  // 共通指示
  await page.getByRole("tab", { name: "共通指示" }).click();
  await page.getByRole("textbox", { name: "共通指示" }).fill("金額は税込で表示してください。");
  await page.getByRole("button", { name: "配信" }).click();
  await expect(page.getByRole("status")).toHaveText("第 1 版を配信しました");

  // 監査ログ: 管理操作が残り、機密語の中身は残らない。CSV で保存できる
  await page.getByRole("tab", { name: "監査ログ" }).click();
  await expect(page.getByTestId("audit-row").first()).toBeVisible();
  const audit = await page.getByRole("table", { name: "監査ログの一覧" }).innerText();
  expect(audit).toContain("admin.user.create");
  expect(audit).toContain("admin.dlp.add");
  expect(audit).not.toContain("平井丸");
  const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "CSV で保存" }).click()]);
  const csv = readFileSync((await dl.path())!, "utf8");
  expect(csv.replace(/^\uFEFF/, "").startsWith("at,user,source,action,target,result")).toBe(true);
  await page.screenshot({ path: join(ARTIFACTS, "admin-audit.png") });
});

test("model registry: evaluation gate, promote and rollback", async ({ page }) => {
  await loginAsAdmin(page);
  await page.getByRole("tab", { name: "モデル" }).click();
  const tag = Date.now().toString(36); // 本物の Gateway で作業フォルダを使い回しても衝突しないように
  const [v1, v2] = [`lora-a-${tag}`, `lora-b-${tag}`];
  const reg = page.getByRole("form", { name: "モデルを登録" });
  for (const id of [v1, v2]) {
    await reg.getByLabel("モデルID").fill(id);
    await reg.getByLabel("基本モデル").fill("gemma-3-12b");
    await reg.getByRole("button", { name: "登録" }).click();
    await expect(page.getByRole("status")).toHaveText(`${id} を登録しました`);
  }
  const ev = page.getByRole("form", { name: "評価結果を記録" });
  const record = async (id: string, value: string) => {
    await ev.getByLabel("評価するモデル").selectOption(id);
    await ev.getByLabel("評価セット").fill("office");
    await ev.getByLabel("指標").fill("pass_rate");
    await ev.getByLabel("値").fill(value);
    await ev.getByRole("button", { name: "記録" }).click();
    await expect(page.getByRole("status")).toHaveText("評価結果を記録しました");
  };
  // 評価結果がなければ本番にできない
  await page.getByTestId(`model-${v1}`).getByRole("button", { name: "本番にする" }).click();
  await expect(page.getByRole("alert")).toHaveText("評価結果のないモデルは本番化できません");
  await record(v1, "0.8");
  await page.getByTestId(`model-${v1}`).getByRole("button", { name: "本番にする" }).click();
  await expect(page.getByTestId(`model-${v1}`)).toContainText("本番");
  // 本番を下回る候補は弾かれ、下回った指標が表示される
  await record(v2, "0.7");
  await page.getByTestId(`model-${v2}`).getByRole("button", { name: "本番にする" }).click();
  await expect(page.getByRole("alert")).toContainText("本番版を下回る");
  await expect(page.getByRole("list", { name: "下回った指標" })).toContainText("office / pass_rate: 本番 0.8、候補 0.7");
  await expect(page.getByTestId(`model-${v2}`)).toContainText("候補");
  await page.screenshot({ path: join(ARTIFACTS, "admin-models.png") });
  // 切り戻し
  page.once("dialog", (d) => void d.accept());
  await page.getByTestId(`model-${v1}`).getByRole("button", { name: "切り戻す" }).click();
  await expect(page.getByRole("status")).toHaveText(`${v1} を切り戻しました`);
  await expect(page.getByTestId(`model-${v1}`)).toContainText("退役");
});

test("ADM-06: consented feedback is reviewed masked, approved and exported", async ({ page, request }) => {
  // 利用者（dev）が同意つきの評価を送る（デスクトップアプリが送るのと同じ API）
  const loginAs = async (login: string, password: string) =>
    ((await (await request.post("/v1/auth/login", { data: { login, password, device_name: "e2e" } })).json()) as { access_token: string }).access_token;
  const admin = await loginAs("admin", ADMIN_PW);
  await request.post("/admin/v1/dlp/terms", { data: { terms: [{ term: "平井丸", category: "vessel" }] }, headers: { Authorization: `Bearer ${admin}` } });
  const dev = await loginAs("dev", DEV_PW);
  const runId = `run-${Date.now().toString(36)}`;
  const res = await request.post("/v1/feedback", {
    headers: { Authorization: `Bearer ${dev}` },
    data: {
      run_id: runId,
      rating: "good",
      comment: "平井丸の転記が早かった",
      consent: true,
      transcript: [
        { role: "user", content: "平井丸の請求書を転記して" },
        { role: "tool", content: "金額 1,234,567 円" },
      ],
    },
  });
  expect((await res.json()).status).toBe("pending");

  await loginAsAdmin(page);
  await page.getByRole("tab", { name: "評価データ" }).click();
  const item = page.getByTestId("feedback-item").filter({ hasText: "転記が早かった" });
  await expect(item).toBeVisible();
  await item.getByText(/やり取り（2 件/).click();
  const text = await item.innerText();
  expect(text).not.toContain("平井丸");
  expect(text).not.toContain("1,234,567");
  expect(text).toContain("［機密語］");
  expect(text).toContain("［数字］");
  await page.screenshot({ path: join(ARTIFACTS, "admin-feedback.png") });
  await item.getByRole("button", { name: "承認" }).click();
  await expect(page.getByRole("status")).toHaveText("承認しました");
  await page.getByLabel("状態で絞り込む").selectOption("approved");
  await expect(page.getByTestId("feedback-item").filter({ hasText: "転記が早かった" })).toContainText("承認済み");
  const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: /承認済みを書き出す/ }).click()]);
  const jsonl = readFileSync((await dl.path())!, "utf8");
  expect(jsonl).toContain("［機密語］");
  expect(jsonl).not.toContain("平井丸");
  expect(jsonl).not.toContain("1,234,567");
});
