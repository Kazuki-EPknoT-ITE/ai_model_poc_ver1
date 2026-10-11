// 本物の Gateway（認証・永続キュー・SSE・暗号化。推論は FakeEngine）を相手にした E2E
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { ARTIFACTS, launch, login, type Ctx } from "./fixtures.js";

let ctx: Ctx | null = null;
test.afterEach(async () => {
  await ctx?.close();
  ctx = null;
});

test("real gateway: login with argon2/EdDSA, run through the persistent queue, final summary", async () => {
  ctx = await launch("", { realGateway: true });
  writeFileSync(join(ctx.ws, "memo.txt"), "x");
  const { page } = ctx;
  // 間違ったパスワードは本物の認証で拒否される
  await page.getByLabel("ログインID").fill("dev");
  await page.getByLabel("パスワード").fill("wrong-password");
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await login(page, "dev", "dev-password-1");
  await page.getByRole("button", { name: /作業フォルダを選ぶ/ }).click();
  // 定型作業は本物の Gateway の会社プロファイルから配られる
  await page.getByLabel("依頼").fill("/");
  await expect(page.getByRole("listbox", { name: "定型作業" })).toBeVisible();
  await page.getByLabel("依頼").fill("フォルダを確認して");
  await page.getByRole("button", { name: "送信" }).click();
  await expect(page.getByTestId("final-summary")).toHaveText("（本物の Gateway）完了しました。", { timeout: 60_000 });
  await expect(page.getByTestId("run-state")).toHaveAttribute("data-state", "done");
  await page.screenshot({ path: join(ARTIFACTS, "real-gateway.png") });
});
