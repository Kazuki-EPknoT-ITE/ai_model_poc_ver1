// 評価ハーネスのコマンド。
//   node <vite-node> eval/harness/src/run.ts --set invoice_extraction_sample
//   node <vite-node> eval/harness/src/run.ts --set tool_tasks --gateway https://gpu-srv:8443 --login u --password p --model-id gemma3-12b-q4-lora7
// 省略時はセットのモック Gateway を起動する。--all で eval/sets の全セットを順に実行する。
import { readdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import { SETS_DIR } from "./env.js";
import { runEvaluation, type EvalOptions } from "./evaluate.js";

function parse(argv: string[]): EvalOptions & { all: boolean; strict: boolean } {
  const o: EvalOptions & { all: boolean; strict: boolean } = { set: "", all: false, strict: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    const next = () => {
      const v = argv[++i];
      if (v === undefined) throw new Error(`${a} の値がありません`);
      return v;
    };
    if (a === "--") continue;
    else if (a === "--set") o.set = next();
    else if (a === "--gateway") o.gateway = next();
    else if (a === "--login") o.login = next();
    else if (a === "--password") o.password = next();
    else if (a === "--model-id") o.modelId = next();
    else if (a === "--model") o.model = next() as EvalOptions["model"];
    else if (a === "--out") o.out = next();
    else if (a === "--cache") o.cache = next();
    else if (a === "--fi-report") o.fiReport = next();
    else if (a === "--keep") o.keep = true;
    else if (a === "--all") o.all = true;
    else if (a === "--strict") o.strict = true;
    else throw new Error(`不明な引数: ${a}`);
  }
  if (!o.set && !o.all) throw new Error("--set <名前|フォルダ> か --all を指定してください");
  return o;
}

async function main(): Promise<number> {
  const o = parse(process.argv.slice(2));
  const sets = o.all ? readdirSync(SETS_DIR).filter((n) => existsSync(join(SETS_DIR, n, "manifest.yaml"))) : [o.set];
  let failed = 0;
  for (const set of sets) {
    try {
      const runs = await runEvaluation({ ...o, set });
      for (const r of runs) {
        process.stdout.write(`${JSON.stringify({ run_id: r.runId, result: r.resultPath, verdict: r.verdict })}\n`);
        if (r.verdict !== "合格") failed++;
      }
    } catch (e) {
      failed++;
      process.stderr.write(`[${set}] 実行できません: ${(e as Error).message}\n`);
    }
  }
  return o.strict && failed ? 1 : 0;
}

main().then(
  (code) => process.exit(code),
  (e: unknown) => {
    process.stderr.write(`${(e as Error).stack ?? String(e)}\n`);
    process.exit(2);
  },
);
