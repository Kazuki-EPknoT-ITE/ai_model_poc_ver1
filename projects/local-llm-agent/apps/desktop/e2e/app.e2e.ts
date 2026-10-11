// デスクトップアプリの E2E（試験仕様 §1.1 の E-01/02/04/07/08/14 の一部）
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { ARTIFACTS, PROJECT, launch, login, type Ctx } from "./fixtures.js";

let ctx: Ctx | null = null;
test.afterEach(async () => {
  await ctx?.close();
  ctx = null;
});

test("E-01/E-02: login, pick folder, request, step cards, final summary", async () => {
  ctx = await launch("basic.yaml");
  writeFileSync(join(ctx.ws, "memo.txt"), "メモの本文");
  const { page } = ctx;
  await expect(page.getByRole("form", { name: "ログイン" })).toBeVisible();
  await login(page);
  await page.getByRole("button", { name: /作業フォルダを選ぶ/ }).click();
  await expect(page.getByRole("region", { name: "作業フォルダのファイル" })).toContainText("memo.txt");
  await page.getByLabel("依頼").fill("フォルダを確認して");
  await page.keyboard.press("Control+Enter");
  await expect(page.getByTestId("final-summary")).toHaveText("memo.txt を確認しました。", { timeout: 30_000 });
  await expect(page.getByTestId("run-state")).toHaveAttribute("data-state", "done");
  const cards = page.getByTestId("step-card");
  await expect(cards.filter({ hasText: "フォルダの一覧" })).toHaveCount(1);
  await expect(cards.filter({ hasText: "テキストを読む" })).toHaveCount(1);
  await expect(page.getByRole("complementary", { name: "計画と承認" })).toContainText("中身を確認");
  await expect(page.getByRole("navigation", { name: "会話の履歴" })).toContainText("フォルダを確認して");
  await page.screenshot({ path: join(ARTIFACTS, "e02-final.png") });
});

test("E-04/E-14: outside write, approval card focused on reject, approve by keyboard, executed", async () => {
  ctx = await launch("approval.yaml");
  writeFileSync(join(ctx.ws, "a.txt"), "A");
  const { page } = ctx;
  await login(page);
  await page.getByRole("button", { name: /作業フォルダを選ぶ/ }).click();
  await page.getByLabel("依頼").fill("a.txt を外にコピーして");
  await page.getByRole("button", { name: "送信" }).click();
  const card = page.getByTestId("approval");
  await expect(card).toContainText("作業フォルダの外の操作", { timeout: 30_000 });
  await expect(card).toContainText("outside");
  // 既定のフォーカスは「却下」
  await expect(card.getByRole("button", { name: "却下" })).toBeFocused();
  await page.screenshot({ path: join(ARTIFACTS, "e04-approval.png") });
  expect(existsSync(join(ctx.outside, "a-copy.txt"))).toBe(false);
  // キーボードだけで承認する（チェックボックスを飛ばして「承認」へ）
  await card.getByRole("button", { name: "承認" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("final-summary")).toHaveText("コピーしました。", { timeout: 30_000 });
  expect(readFileSync(join(ctx.outside, "a-copy.txt"), "utf8")).toBe("A");
});

test("E-07/E-08: closing the window keeps the run alive in the tray, and stop ends it", async () => {
  ctx = await launch("approval.yaml");
  writeFileSync(join(ctx.ws, "a.txt"), "A");
  const { page, app } = ctx;
  await login(page);
  await page.getByRole("button", { name: /作業フォルダを選ぶ/ }).click();
  await page.getByLabel("依頼").fill("a.txt を外にコピーして");
  await page.getByRole("button", { name: "送信" }).click();
  await expect(page.getByTestId("approval")).toBeVisible({ timeout: 30_000 });
  // ウィンドウを閉じても終了せず、トレイに残る
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]!.close());
  await expect.poll(() => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]!.isVisible())).toBe(false);
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]!.show());
  await expect(page.getByTestId("approval")).toBeVisible();
  await page.getByRole("button", { name: "停止", exact: true }).click();
  await expect(page.getByTestId("run-state")).toHaveAttribute("data-state", "stopped", { timeout: 15_000 });
  await page.screenshot({ path: join(ARTIFACTS, "e07-stopped.png") });
});

test("T-35: invoices to Excel from the UI (office-tools + mock inference), then undo", async () => {
  ctx = await launch("../invoice-basic.yaml", { office: true });
  const script = join(PROJECT, "packages", "agent-runtime", "test", "integration", "make_invoices.py");
  execFileSync("uv", ["run", "--project", PROJECT, "python", script, join(ctx.ws, "請求書"), "3"]);
  const { page } = ctx;
  await login(page);
  await page.getByRole("button", { name: /作業フォルダを選ぶ/ }).click();
  await page.getByLabel("依頼").fill("請求書フォルダのPDFを読んで、送金明細をExcelにまとめて");
  await page.getByRole("button", { name: "送信" }).click();
  await expect(page.getByTestId("final-summary")).toContainText("3件", { timeout: 90_000 });
  await expect(page.getByTestId("step-card").filter({ hasText: "一括処理" })).toHaveCount(1);
  await expect(page.getByTestId("artifact")).toContainText("送金明細.xlsx");
  await expect(page.getByRole("region", { name: "作業フォルダのファイル" })).toContainText("送金明細.xlsx");
  expect(existsSync(join(ctx.ws, "送金明細.xlsx"))).toBe(true);
  await page.screenshot({ path: join(ARTIFACTS, "t35-invoices.png") });
  // 元に戻す（新規作成したファイルは消える）
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: "この作業を元に戻す" }).click();
  await expect(page.getByRole("status").filter({ hasText: "元に戻しました" })).toBeVisible();
  expect(existsSync(join(ctx.ws, "送金明細.xlsx"))).toBe(false);
});
