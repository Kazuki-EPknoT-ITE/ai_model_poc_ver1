// 書き込みの意図記録とステージング（設計書 §5.4、データモデル §2.3〜2.4）。
//
// ツールに本物のファイルを直接書かせず、同じフォルダの作業用コピー（staging）を書かせる。
//   prepared: 書き込み前の状態（pre_hash）とバックアップを記録した
//   staged:   ツールが staging を書き終え、その内容（post_hash）を記録した
//   committed: Runtime が staging を本物と置き換えた（ロック時は別名で保存）
// こうすると、どこでプロセスが落ちても、再開時に「済んでいる／やり直す／他人が編集した」を
// ハッシュだけで判定でき、同じ書き込みを2回しない（RUN-02）。
import { copyFile, mkdir, rename, rm, stat, unlink } from "node:fs/promises";
import { basename, dirname, extname, join } from "node:path";
import type { Artifact, ToolCatalogEntry, ToolResult } from "@lla/protocol";
import type { ResolvedPath } from "../policy/policy.js";
import type { IntentRow, RunStore } from "../store/store.js";
import { fileHash, pointerSet, sha256, sleep, stableStringify, ulid } from "../util.js";
import { errResult, okResult, type ToolContext } from "../tools/types.js";
import { testHook } from "../testhooks.js";

export interface WriteOptions {
  appDataDir: string;
  /** ロックが解けるのを待つ時間（既定5分。5.4 の表） */
  lockWaitMs?: number;
  now?: () => Date;
}

const LOCK_CODES = new Set(["EBUSY", "EPERM", "EACCES"]);

export class WriteCoordinator {
  private readonly lockWaitMs: number;
  constructor(
    private readonly store: RunStore,
    private readonly opts: WriteOptions,
  ) {
    this.lockWaitMs = opts.lockWaitMs ?? 5 * 60_000;
  }

  private backupPath(ctx: { conversationId: string }, intentId: string, target: string): string {
    return join(this.opts.appDataDir, "backups", ctx.conversationId, intentId, basename(target));
  }

  /**
   * 書き込み系ツールを実行する。invoke にはステージングパスに置き換えた args が渡る。
   * 戻り値の artifacts / data 内のパスは、本物のパスに書き戻して返す。
   */
  async execute(
    tool: ToolCatalogEntry,
    args: Record<string, unknown>,
    paths: ResolvedPath[],
    ctx: ToolContext,
    invoke: (args: Record<string, unknown>) => Promise<ToolResult>,
    batchItemId: string | null = null,
  ): Promise<ToolResult> {
    if (tool.name === "fs.delete") return this.deleteFile(paths[0]!.real_path, ctx, tool, args);
    if (tool.name === "fs.move") return this.moveFile(paths, ctx, tool, args);
    const targets = tool.name === "fs.mkdir" ? [] : paths.filter((p) => p.write);
    if (targets.length === 0) return invoke(args);

    const opDigest = sha256(tool.name + stableStringify(args));
    // 再開: この手の書き込みが前回の実行で終わっていた（resumeOpen が committed / conflict にした）なら、
    // やり直さない。追記のような操作を2回行わないため（試験仕様 §4 FI-13「重複 0件」）
    const prior = this.store
      .intentsForStep(ctx.stepId)
      .filter((i) => i.op_digest === opDigest && i.batch_item_id === batchItemId && (i.status === "committed" || i.status === "conflict"));
    if (prior.length && targets.every((t) => prior.some((i) => i.path === t.real_path))) {
      const artifacts: Artifact[] = targets.map((t) => {
        const i = prior.filter((x) => x.path === t.real_path).pop()!;
        const final = i.final_path ?? i.path;
        return {
          path: final === i.path ? t.path : final,
          op: i.op,
          ...(i.pre_hash ? { pre_hash: i.pre_hash } : {}),
          post_hash: i.post_hash ?? "",
          ...(i.backup_path ? { backup_path: i.backup_path } : {}),
          ...(final !== i.path ? { renamed_from: t.path } : {}),
        };
      });
      const warnings = ["前回の実行（中断前）で書き込みを終えていたため、やり直していません"];
      for (const i of prior.filter((x) => x.status === "conflict")) warnings.push(`${basename(i.path)} は他で変更されていたため、AIの変更を ${basename(i.final_path ?? "")} に保存しました`);
      return okResult({ resumed: true }, { artifacts, warnings });
    }
    const staged: { intent: IntentRow; target: ResolvedPath }[] = [];
    const newArgs = structuredClone(args);
    for (const t of targets) {
      const id = ulid();
      const pre = await fileHash(t.real_path);
      const staging = join(dirname(t.real_path), `.~lla-${id}.${basename(t.real_path)}`);
      let backup: string | null = null;
      if (pre !== null) {
        backup = this.backupPath(ctx, id, t.real_path);
        await mkdir(dirname(backup), { recursive: true });
        await copyFile(t.real_path, backup);
        await copyFile(t.real_path, staging);
      }
      const intent = this.store.addIntent({
        id,
        step_id: ctx.stepId,
        batch_item_id: batchItemId,
        path: t.real_path,
        op: tool.name === "fs.copy" ? "copy" : pre === null ? "create" : "overwrite",
        op_digest: opDigest,
        pre_hash: pre,
        backup_path: backup,
        staging_path: staging,
      });
      staged.push({ intent, target: t });
      pointerSet(newArgs, t.pointer, staging);
      testHook("intent.prepared", { path: basename(t.real_path) });
    }

    let result: ToolResult;
    try {
      result = await invoke(newArgs);
    } catch (e) {
      await this.abandon(staged.map((s) => s.intent));
      throw e;
    }
    if (!result.ok) {
      await this.abandon(staged.map((s) => s.intent));
      return result;
    }

    // staging の内容を記録してから置き換える
    for (const s of staged) {
      const post = await fileHash(s.intent.staging_path!);
      if (post === null) {
        this.store.updateIntent(s.intent.id, { status: "undone" });
        continue;
      }
      this.store.updateIntent(s.intent.id, { status: "staged", post_hash: post });
      s.intent.post_hash = post;
      s.intent.status = "staged";
    }
    const warnings = [...(result.warnings ?? [])];
    const artifacts: Artifact[] = [];
    for (const s of staged.filter((x) => x.intent.status === "staged")) {
      const final = await this.commitReplace(s.intent);
      if (final !== s.intent.path) warnings.push(`対象のファイルが使用中だったため、別名で保存しました: ${basename(final)}`);
      artifacts.push({
        path: s.target.path === s.target.real_path ? final : final === s.intent.path ? s.target.path : final,
        op: s.intent.op,
        ...(s.intent.pre_hash ? { pre_hash: s.intent.pre_hash } : {}),
        post_hash: s.intent.post_hash!,
        ...(s.intent.backup_path ? { backup_path: s.intent.backup_path } : {}),
        ...(final !== s.intent.path ? { renamed_from: s.target.path } : {}),
      });
    }
    // data の中に残るステージングパスを、利用者に見せるパスに書き戻す
    let json = JSON.stringify({ ...result, artifacts: undefined, warnings: undefined });
    for (const s of staged) {
      const from = JSON.stringify(s.intent.staging_path!).slice(1, -1);
      const to = JSON.stringify(s.target.path).slice(1, -1);
      json = json.split(from).join(to);
    }
    const rewritten = JSON.parse(json) as ToolResult;
    return { ...rewritten, artifacts, ...(warnings.length ? { warnings } : {}) };
  }

  /** staging を本物に置き換える。ロック中は待ち、解けなければ別名で保存する。戻り値は最終的なパス。 */
  async commitReplace(intent: IntentRow): Promise<string> {
    const staging = intent.staging_path!;
    const deadline = Date.now() + this.lockWaitMs;
    let wait = 200;
    for (;;) {
      try {
        await rename(staging, intent.path);
        testHook("intent.replaced", { path: basename(intent.path) });
        this.store.updateIntent(intent.id, { status: "committed", committed_at: new Date().toISOString() });
        return intent.path;
      } catch (e) {
        const code = (e as NodeJS.ErrnoException).code ?? "";
        if (!LOCK_CODES.has(code)) throw e;
        if (Date.now() >= deadline) break;
        await sleep(Math.min(wait, Math.max(0, deadline - Date.now())));
        wait = Math.min(wait * 2, 10_000);
      }
    }
    const alt = await this.altName(intent.path);
    await rename(staging, alt);
    this.store.updateIntent(intent.id, { status: "committed", final_path: alt, committed_at: new Date().toISOString() });
    return alt;
  }

  private async altName(path: string): Promise<string> {
    const d = (this.opts.now ?? (() => new Date()))();
    const pad = (n: number) => String(n).padStart(2, "0");
    const stamp = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}${pad(d.getMinutes())}`;
    const ext = extname(path);
    const base = path.slice(0, path.length - ext.length);
    for (let i = 1; ; i++) {
      const cand = `${base} (AI編集 ${stamp}${i > 1 ? ` ${i}` : ""})${ext}`;
      if (!(await exists(cand))) return cand;
    }
  }

  private async abandon(intents: IntentRow[]): Promise<void> {
    for (const i of intents) {
      if (i.staging_path) await rm(i.staging_path, { force: true });
      this.store.updateIntent(i.id, { status: "undone" });
    }
  }

  private async deleteFile(real: string, ctx: ToolContext, tool: ToolCatalogEntry, args: unknown): Promise<ToolResult> {
    const pre = await fileHash(real);
    if (pre === null) return errResult("NOT_FOUND", `${real} は存在しません`);
    const id = ulid();
    const backup = this.backupPath(ctx, id, real);
    const intent = this.store.addIntent({
      step_id: ctx.stepId,
      batch_item_id: null,
      path: real,
      op: "delete",
      op_digest: sha256(tool.name + stableStringify(args)),
      pre_hash: pre,
      backup_path: backup,
    });
    await mkdir(dirname(backup), { recursive: true });
    await moveAcross(real, backup);
    this.store.updateIntent(intent.id, { status: "committed", committed_at: new Date().toISOString() });
    const artifact: Artifact = { path: real, op: "delete", pre_hash: pre, backup_path: backup };
    return okResult({ artifact }, { artifacts: [artifact] });
  }

  private async moveFile(paths: ResolvedPath[], ctx: ToolContext, tool: ToolCatalogEntry, args: Record<string, unknown>): Promise<ToolResult> {
    const src = paths.find((p) => p.pointer[0] === "src")!;
    const dst = paths.find((p) => p.pointer[0] === "dst")!;
    if ((await fileHash(dst.real_path)) !== null && !args.overwrite) {
      return errResult("INVALID_ARGS", "移動先にファイルがあります。overwrite を指定するか、別の名前にしてください");
    }
    const copyTool = { ...tool, name: "fs.copy" } as ToolCatalogEntry;
    const copied = await this.execute(copyTool, { src: src.real_path, dst: dst.real_path }, [{ ...dst, write: true }], ctx, async (a) => {
      await copyFile(String(a.src), String(a.dst));
      return okResult({});
    });
    if (!copied.ok) return copied;
    const deleted = await this.deleteFile(src.real_path, ctx, tool, args);
    if (!deleted.ok) return deleted;
    const artifacts = [...(copied.artifacts ?? []).map((a) => ({ ...a, op: "move" as const })), ...(deleted.artifacts ?? [])];
    return okResult({ artifact: artifacts[0] }, { artifacts });
  }

  /** 起動時に、途中で止まった意図記録を解決する（データモデル §2.4 の手順1）。 */
  async resumeOpen(): Promise<{ resolved: number; warnings: string[] }> {
    const warnings: string[] = [];
    let resolved = 0;
    for (const i of this.store.openIntents()) {
      resolved++;
      const current = await fileHash(i.path);
      if (i.op === "delete") {
        const backupOk = i.backup_path ? await exists(i.backup_path) : false;
        this.store.updateIntent(i.id, { status: current === null && backupOk ? "committed" : "undone" });
        continue;
      }
      if (i.status === "prepared") {
        // ツールの書き込みが終わっていない → 何も反映していない。手順をやり直す
        if (i.staging_path) await rm(i.staging_path, { force: true });
        this.store.updateIntent(i.id, { status: "undone" });
        continue;
      }
      // staged
      if (current !== null && current === i.post_hash) {
        if (i.staging_path) await rm(i.staging_path, { force: true });
        this.store.updateIntent(i.id, { status: "committed", committed_at: new Date().toISOString() });
      } else if (current === i.pre_hash && i.staging_path && (await fileHash(i.staging_path)) === i.post_hash) {
        await this.commitReplace(i);
      } else if (i.staging_path && (await exists(i.staging_path))) {
        // 他の人が編集した。上書きせず別名で保存する（R11）
        const alt = await this.altName(i.path);
        await rename(i.staging_path, alt);
        this.store.updateIntent(i.id, { status: "conflict", final_path: alt });
        warnings.push(`${basename(i.path)} は作業中に他で変更されていたため、AIの変更を ${basename(alt)} に保存しました`);
      } else {
        this.store.updateIntent(i.id, { status: "undone" });
      }
    }
    return { resolved, warnings };
  }

  /** 元に戻す（AGT-03）。新しい変更から順に戻す。後から手で変えられたファイルは戻さず警告する。 */
  async undoRun(runId: string, onlyPaths?: string[]): Promise<{ restored: string[]; warnings: string[] }> {
    const restored: string[] = [];
    const warnings: string[] = [];
    const intents = this.store
      .intentsForRun(runId)
      .filter((i) => i.status === "committed" || i.status === "conflict")
      .reverse();
    for (const i of intents) {
      const target = i.final_path ?? i.path;
      if (onlyPaths && !onlyPaths.includes(i.path) && !onlyPaths.includes(target)) continue;
      const current = await fileHash(target);
      if (i.op === "delete") {
        if (current !== null) {
          warnings.push(`${basename(target)} は既に存在するため戻しませんでした`);
          continue;
        }
        await copyFile(i.backup_path!, target);
      } else if (current !== i.post_hash) {
        warnings.push(`${basename(target)} は後から変更されているため戻しませんでした`);
        continue;
      } else if (i.final_path || i.op === "create" || i.op === "copy" && !i.backup_path) {
        await unlink(target);
      } else {
        const tmp = join(dirname(target), `.~lla-undo-${i.id}.${basename(target)}`);
        await copyFile(i.backup_path!, tmp);
        await rename(tmp, target);
      }
      this.store.updateIntent(i.id, { status: "undone" });
      restored.push(target);
    }
    return { restored, warnings };
  }
}

async function exists(p: string): Promise<boolean> {
  try {
    await stat(p);
    return true;
  } catch {
    return false;
  }
}

async function moveAcross(src: string, dst: string): Promise<void> {
  try {
    await rename(src, dst);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== "EXDEV") throw e;
    await copyFile(src, dst);
    await unlink(src);
  }
}
