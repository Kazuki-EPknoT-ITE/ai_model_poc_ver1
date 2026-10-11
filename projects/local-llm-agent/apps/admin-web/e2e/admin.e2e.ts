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
  await page.getByLabel("機密語", { exact: true }).fill(["平井丸", "HIRAI MARU,vessel", "平井丸"].join("\n"));
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
