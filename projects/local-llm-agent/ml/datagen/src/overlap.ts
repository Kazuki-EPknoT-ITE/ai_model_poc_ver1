// 学習データの入力ファイルと評価セットのファイルの重複照合（test_spec §3.1）。
// 評価側の split_check と同じく、ファイルの sha256 で照合する。NAS にしかないセットは照合できないので skipped に挙げる。
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SETS_DIR, uvPython } from "../../../eval/harness/src/env.js";

const OVERLAP_PY = `
import json, sys
from pathlib import Path
import yaml
from lla_eval.manifest import data_files, file_sha256, parse_manifest, resolve_data_root
train = set(Path(sys.argv[1]).read_text(encoding="utf-8").split())
res = {"overlap": [], "checked": [], "skipped": []}
for sd in sorted(Path(sys.argv[2]).iterdir()):
    mf = sd / "manifest.yaml"
    if not mf.exists():
        continue
    try:
        m = parse_manifest(yaml.safe_load(mf.read_text(encoding="utf-8")))
        root = resolve_data_root(m, sd, Path(sys.argv[3]))
        files = data_files(root)
    except Exception as e:  # noqa: BLE001
        res["skipped"].append(f"{sd.name}: {type(e).__name__}")
        continue
    res["checked"].append(sd.name)
    res["overlap"] += [f"{sd.name}/{p.relative_to(root).as_posix()}" for p in files if file_sha256(p) in train]
print(json.dumps(res, ensure_ascii=False))
`;

export interface OverlapResult {
  overlap: string[];
  checked: string[];
  skipped: string[];
}

export async function checkEvalOverlap(hashes: string[], setsDir = SETS_DIR): Promise<OverlapResult> {
  const dir = mkdtempSync(join(tmpdir(), "lla-overlap-"));
  const list = join(dir, "hashes.txt");
  writeFileSync(list, hashes.join("\n"));
  const r = await uvPython(["-c", OVERLAP_PY, list, setsDir, join(dir, "cache")]);
  const line = r.stdout.trim().split(/\r?\n/).pop() ?? "";
  if (!line.startsWith("{")) throw new Error(`評価セットとの照合に失敗しました: ${r.stderr.slice(-300)}`);
  return JSON.parse(line) as OverlapResult;
}
