// 一括処理ランナー（プロトコル §3.6、RUN-06）。件ごとに完了記録を残し、失敗は飛ばして記録する。
// 出力ファイルへの書き込みは、全件の処理が終わってから一度に行う。
import { isAbsolute, join, relative, sep } from "node:path";
import type { ToolResult } from "@lla/protocol";
import type { RunStore } from "../store/store.js";
import { globFiles } from "../tools/glob.js";
import { errResult, okResult, type ToolContext } from "../tools/types.js";
import { fileHash, pointerGetAll, ulid } from "../util.js";
import { isInside, resolveRealPath } from "../policy/paths.js";

interface BatchSpec {
  items: string;
  pipeline: { tool: string; args?: Record<string, unknown> };
  output?: { xlsx?: string; csv?: string; sheet?: string; columns: { header: string; from: string }[]; append?: boolean };
}

export type ExecTool = (name: string, args: Record<string, unknown>) => Promise<ToolResult>;

export interface BatchCallbacks {
  onProgress?: (batchId: string, done: number, total: number, failed: number) => void;
}

const PIPELINE_ARG: Record<string, (item: string, args: Record<string, unknown>) => Record<string, unknown>> = {
  "invoice.extract": (item, a) => ({ ...a, path: item }),
  "pdf.read_pages": (item, a) => ({ pages: "1-300", ...a, path: item }),
  "pdf.extract_tables": (item, a) => ({ pages: "1-300", ...a, path: item }),
  "convert.to_pdf": (item, a) => ({ ...a, src: item }),
  "convert.to_docx": (item, a) => ({ ...a, src: item }),
  "convert.to_xlsx": (item, a) => ({ ...a, src: item }),
};

/** items（"glob:…" か "list:…"）を、作業フォルダからの相対パスの一覧にする。 */
export async function expandBatchItems(items: string, workspace: string): Promise<string[]> {
  if (items.startsWith("list:")) {
    return items.slice(5).split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  }
  let glob = items.replace(/^glob:/, "");
  // 作業フォルダの中を絶対パスで指した glob（モデルはよく絶対パスで書く）は、作業フォルダからの相対にして照合する。
  // 作業フォルダの外を指すものは、何も一致しない（外のファイルは一括処理の対象にしない）
  if (isAbsolute(glob)) {
    const rel = relative(workspace, glob);
    if (rel.startsWith("..") || isAbsolute(rel)) return [];
    glob = rel;
  }
  const files = await globFiles(workspace, glob);
  return files.map((f) => relative(workspace, f.abs).split(sep).join("/"));
}

export class BatchRunner {
  constructor(
    private readonly store: RunStore,
    private readonly exec: ExecTool,
    private readonly cb: BatchCallbacks = {},
  ) {}

  async run(spec: BatchSpec, ctx: ToolContext): Promise<ToolResult> {
    let batch = this.store.batchForStep(ctx.stepId);
    if (!batch) {
      const items = await expandBatchItems(spec.items, ctx.workspace);
      if (items.length === 0) {
        return errResult("NOT_FOUND", `対象のファイルがありません: ${spec.items}（glob は作業フォルダからの相対パスで書きます。例: glob:請求書/*.pdf）`);
      }
      const id = ulid();
      this.store.tx(() => {
        this.store.addBatch({ id, run_id: ctx.runId, step_id: ctx.stepId, spec: JSON.stringify(spec), total: items.length, status: "running" });
        this.store.addBatchItems(id, items);
      });
      batch = this.store.getBatch(id)!;
    }
    const prepare = PIPELINE_ARG[spec.pipeline.tool];
    if (!prepare) return errResult("INVALID_ARGS", `${spec.pipeline.tool} は一括処理に使えません`);

    if (batch.status === "running") {
      for (const it of this.store.batchItems(batch.id)) {
        if (it.status === "ok" || it.status === "failed") continue;
        if (ctx.signal?.aborted) throw ctx.signal.reason;
        const abs = isAbsolute(it.item) ? it.item : join(ctx.workspace, it.item);
        if (!isInside(resolveRealPath(abs, ctx.workspace), ctx.workspace)) {
          this.store.updateBatchItem(it.id, { status: "failed", error_code: "FORBIDDEN_PATH", error_reason: "作業フォルダ外のファイルは一括処理できません。fs.copy で作業フォルダに入れてください" });
          continue;
        }
        this.store.updateBatchItem(it.id, { status: "running", attempts: it.attempts + 1, item_hash: await fileHash(abs) });
        const r = await this.exec(spec.pipeline.tool, prepare(it.item, spec.pipeline.args ?? {}));
        if (r.ok) this.store.updateBatchItem(it.id, { status: "ok", result: JSON.stringify(r.data ?? {}) });
        else this.store.updateBatchItem(it.id, { status: "failed", error_code: r.error?.code ?? "TOOL_FAILED", error_reason: r.error?.message ?? "" });
        const all = this.store.batchItems(batch.id);
        this.cb.onProgress?.(
          batch.id,
          all.filter((x) => x.status === "ok" || x.status === "failed").length,
          all.length,
          all.filter((x) => x.status === "failed").length,
        );
      }
      this.store.updateBatch(batch.id, { status: "writing_output" });
      batch.status = "writing_output";
    }

    const items = this.store.batchItems(batch.id);
    const ok = items.filter((i) => i.status === "ok");
    const failed = items.filter((i) => i.status === "failed").map((i) => ({ item: i.item, code: i.error_code ?? "", reason: i.error_reason ?? "" }));
    let outputArtifact: unknown;
    const warnings: string[] = [];
    if (spec.output && batch.status === "writing_output") {
      const rows = ok.map((i) => {
        const data = JSON.parse(i.result ?? "{}") as unknown;
        return spec.output!.columns.map((c) => {
          const v = pointerGetAll(data, c.from)[0]?.value;
          return v === undefined || (typeof v === "object" && v !== null && !("date" in v)) ? null : (v as string | number | boolean | null);
        });
      });
      const headers = spec.output.columns.map((c) => c.header);
      const r = await this.writeOutput(spec.output, headers, rows);
      if (!r.ok) return r;
      outputArtifact = r.artifacts?.[0];
      warnings.push(...(r.warnings ?? []));
    }
    this.store.updateBatch(batch.id, { status: "done", ended_at: new Date().toISOString() });
    return okResult(
      { batch_id: batch.id, total: items.length, succeeded: ok.length, failed, result_id: batch.id, ...(outputArtifact ? { output_artifact: outputArtifact } : {}) },
      { ...(outputArtifact ? { artifacts: [outputArtifact as never] } : {}), ...(warnings.length ? { warnings } : {}) },
    );
  }

  private async writeOutput(
    out: NonNullable<BatchSpec["output"]>,
    headers: string[],
    rows: (string | number | boolean | null)[][],
  ): Promise<ToolResult> {
    if (out.csv) {
      return this.exec("csv.write", { path: out.csv, header: headers, rows, overwrite: true });
    }
    const path = out.xlsx!;
    const sheet = out.sheet ?? "Sheet1";
    const info = await this.exec("xlsx.info", { path });
    if (!info.ok) {
      if (info.error?.code !== "NOT_FOUND") return info;
      return this.exec("xlsx.create", {
        path,
        sheets: [{ name: sheet, columns: headers.map((h) => ({ header: h })), rows, freeze: "A2" }],
      });
    }
    const sheets = ((info.data as { sheets?: { name: string; rows?: number; used_range?: string }[] }).sheets ?? []);
    const found = sheets.find((s) => s.name === sheet);
    if (!found) {
      const add = await this.exec("xlsx.add_sheet", { path, name: sheet });
      if (!add.ok) return add;
      return this.exec("xlsx.write_table", { path, sheet, top_left: "A1", header: headers, rows });
    }
    if (out.append) {
      const next = (found.rows ?? 1) + 1;
      return this.exec("xlsx.write_table", { path, sheet, top_left: `A${next}`, rows });
    }
    return this.exec("xlsx.write_table", { path, sheet, top_left: "A1", header: headers, rows });
  }
}

/** data.transform の { result_of } を、一括処理の結果の表に置き換える（Runtime 側で解決する）。 */
export function substituteResultOf(store: RunStore, args: Record<string, unknown>): Record<string, unknown> {
  const inputs = args.inputs as { name: string; source: Record<string, unknown> }[] | undefined;
  if (!inputs?.some((i) => "result_of" in i.source)) return args;
  const copy = structuredClone(args);
  for (const inp of copy.inputs as { name: string; source: Record<string, unknown> }[]) {
    if (!("result_of" in inp.source)) continue;
    const items = store.batchItems(String(inp.source.result_of)).filter((i) => i.status === "ok");
    const flat = items.map((i) => flatten(JSON.parse(i.result ?? "{}")));
    const header = [...new Set(flat.flatMap((f) => Object.keys(f)))];
    inp.source = { header, rows: flat.map((f) => header.map((h) => f[h] ?? null)) };
  }
  return copy;
}

function flatten(o: unknown, prefix = ""): Record<string, string | number | boolean | null> {
  const out: Record<string, string | number | boolean | null> = {};
  if (o === null || typeof o !== "object" || Array.isArray(o)) return prefix ? { [prefix]: Array.isArray(o) ? JSON.stringify(o) : (o as never) } : {};
  for (const [k, v] of Object.entries(o)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (k === "value" && prefix) {
      out[prefix] = v as never;
      continue;
    }
    if (v !== null && typeof v === "object" && !Array.isArray(v)) Object.assign(out, flatten(v, key));
    else out[key] = Array.isArray(v) ? JSON.stringify(v) : (v as never);
  }
  return out;
}

