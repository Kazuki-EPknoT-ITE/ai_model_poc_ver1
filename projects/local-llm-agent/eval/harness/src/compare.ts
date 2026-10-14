// 同じ評価セットの2回の結果を比べる（POC 条件2: 学習後がベースモデルを上回るか）。
//   pnpm eval:compare <基準の結果.json> <候補の結果.json>
// 結果 JSON は pnpm eval が eval/harness/results/<set>/<run_id>.json に出すもの。本体は lla_eval.compare。
import { uvPython } from "./env.js";

const args = process.argv.slice(2).filter((a) => a !== "--");
if (args.length < 2) {
  process.stderr.write("使い方: pnpm eval:compare <基準の結果.json> <候補の結果.json> [--alpha 0.05]\n");
  process.exit(2);
}
const r = await uvPython(["-m", "lla_eval", "compare", ...args]);
process.stderr.write(r.stderr.replace(/^.*warning: .*$/gim, "").trimStart());
process.stdout.write(r.stdout);
process.exit(r.code ?? 1);
