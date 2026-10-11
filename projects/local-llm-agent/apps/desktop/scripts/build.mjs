// main / preload / runtime-host を esbuild でまとめる（renderer は vite）。
import { build } from "esbuild";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const common = { bundle: true, platform: "node", target: "node20", sourcemap: true, logLevel: "warning" };

await Promise.all([
  build({ ...common, entryPoints: [join(root, "src/main/main.ts")], outfile: join(root, "dist/main/main.cjs"), format: "cjs", external: ["electron"] }),
  build({ ...common, entryPoints: [join(root, "src/preload/preload.ts")], outfile: join(root, "dist/preload/preload.cjs"), format: "cjs", external: ["electron"] }),
  // Runtime は Electron ではなく Node で動かす。ネイティブモジュールは同梱せず node_modules から読む
  build({
    ...common,
    entryPoints: [join(root, "src/runtime-host/host.ts")],
    outfile: join(root, "dist/runtime-host/host.mjs"),
    format: "esm",
    external: ["better-sqlite3-multiple-ciphers"],
    banner: { js: "import { createRequire as __cr } from 'node:module'; const require = __cr(import.meta.url);" },
    // 障害注入試験のフック（packages/agent-runtime/src/testhooks.ts）を配布物から取り除く
    define: { "process.env.AGENT_TEST_HOOKS": '"0"' },
    minifySyntax: true,
    sourcesContent: false,
  }),
]);
// 試験仕様 §4: 配布物に試験フックを読むコードが残っていないこと
for (const f of ["dist/runtime-host/host.mjs", "dist/runtime-host/host.mjs.map"]) {
  if (readFileSync(join(root, f), "utf8").includes("AGENT_TEST_")) throw new Error(`${f} に試験フックが残っています`);
}
console.log("built main / preload / runtime-host");
