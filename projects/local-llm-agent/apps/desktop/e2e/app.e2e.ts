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

test("E-03/E-13: attach a file and call a macro with /", async () => {
  ctx = await launch("basic.yaml");
  writeFileSync(join(ctx.ws, "memo.txt"), "x");
  const attach = join(ctx.outside, "添付資料.txt");
  writeFileSync(attach, "attached");
  const { page } = ctx;
  await login(page);
  await page.getByRole("button", { name: /作業フォルダを選ぶ/ }).click();
  // 定型作業: "/" で候補が出て、Enter で依頼文が入る
  const box = page.getByLabel("依頼");
  await box.fill("/請求");
  await expect(page.getByRole("listbox", { name: "定型作業" })).toContainText("/請求書取込");
  await box.press("Enter");
  await expect(box).toHaveValue("請求書フォルダのPDFを読んで、送金明細をExcelにまとめて");
  // 添付: ファイルを選ぶとチップが出て、依頼と一緒に送られる
  await box.fill("フォルダを確認して");
  await page.getByTestId("file-input").setInputFiles(attach);
  await expect(page.getByTestId("attachment")).toContainText("添付資料.txt");
  await page.getByRole("button", { name: "送信" }).click();
  await expect(page.getByTestId("attachment")).toHaveCount(0);
  await expect(page.getByTestId("final-summary")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: /新しい会話/ }).click();
  await page.getByRole("navigation", { name: "会話の履歴" }).getByRole("button").first().click();
  await expect(page.locator(".bubble.user").first()).toContainText("（添付）");
  await expect(page.locator(".bubble.user").first()).toContainText("添付資料.txt");
});

test("E-06: diff preview of an overwritten CSV", async () => {
  ctx = await launch("diff.yaml", { office: true });
  writeFileSync(join(ctx.ws, "data.csv"), "品名,金額\r\n塗装,1000\r\n");
  const { page } = ctx;
  await login(page);
  await page.getByRole("button", { name: /作業フォルダを選ぶ/ }).click();
  await page.getByLabel("依頼").fill("data.csv を更新して");
  await page.getByRole("button", { name: "送信" }).click();
  await expect(page.getByTestId("final-summary")).toHaveText("data.csv を更新しました。", { timeout: 60_000 });
  await page.getByTestId("artifact").getByRole("button", { name: "差分" }).click();
  const dlg = page.getByRole("dialog", { name: /変更の確認/ });
  await expect(dlg.getByTestId("diff")).toHaveAttribute("data-kind", "text");
  await expect(dlg).toContainText("塗装,1000");
  await expect(dlg).toContainText("塗装,1200");
  await expect(dlg).toContainText("部品,800");
  await page.screenshot({ path: join(ARTIFACTS, "e06-diff.png") });
  await page.keyboard.press("Escape");
  await expect(dlg).toHaveCount(0);
});

test("settings are saved and reloaded", async () => {
  ctx = await launch("basic.yaml");
  const { page } = ctx;
  await login(page);
  await page.getByRole("button", { name: "設定" }).click();
  const dlg = page.getByRole("dialog", { name: "設定" });
  await dlg.getByLabel("長時間の作業を知らせるまでの時間（分）").fill("30");
  await dlg.getByLabel("既定のモデル").selectOption("accurate");
  await dlg.getByRole("button", { name: "保存" }).click();
  await expect(dlg.getByRole("status")).toHaveText("保存しました");
  await dlg.getByRole("button", { name: "閉じる" }).first().click();
  await expect(page.getByLabel("モデル", { exact: true })).toHaveValue("accurate");
  await page.getByRole("button", { name: "設定" }).click();
  await expect(page.getByRole("dialog", { name: "設定" }).getByLabel("長時間の作業を知らせるまでの時間（分）")).toHaveValue("30");
});
