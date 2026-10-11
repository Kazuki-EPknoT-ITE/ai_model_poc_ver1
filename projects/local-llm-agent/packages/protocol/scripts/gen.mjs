// スキーマ生成スクリプト（local_llm_protocol.md の正本 = schemas/src/*.yaml）
//   node scripts/gen.mjs          … schemas/gen と src/generated を書き出す
//   node scripts/gen.mjs --check  … 生成物が最新かを検査する（差分があれば exit 1）
import { readFileSync, writeFileSync, readdirSync, mkdirSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";
import { compile } from "json-schema-to-typescript";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(ROOT, "schemas", "src");
const OUT_SCHEMA = join(ROOT, "schemas", "gen");
const OUT_TS = join(ROOT, "src", "generated");
const OUT_PY_DATA = join(ROOT, "python", "src", "lla_protocol", "data");
const PROTOCOL_VERSION = "1.0";
const SCHEMA_ID = `https://local-llm-agent/schemas/${PROTOCOL_VERSION}/protocol.json`;
const CHECK = process.argv.includes("--check");

const TOOL_META_KEYS = ["description", "effect", "path_args", "idempotent", "untrusted_output", "timeout_ms", "priority"];

function loadYaml(file) {
  return YAML.parse(readFileSync(file, "utf8"), { merge: true });
}

function listYaml(dir) {
  return readdirSync(dir)
    .filter((f) => f.endsWith(".yaml"))
    .sort()
    .map((f) => join(dir, f));
}

const defs = {};
const tools = {};
function addDefs(file, obj) {
  for (const [name, schema] of Object.entries(obj ?? {})) {
    if (defs[name]) throw new Error(`$defs の名前が重複しています: ${name} (${file})`);
    defs[name] = schema;
  }
}
for (const file of listYaml(SRC)) addDefs(file, loadYaml(file).$defs);
for (const file of listYaml(join(SRC, "tools"))) {
  const doc = loadYaml(file);
  addDefs(file, doc.$defs);
  for (const [name, t] of Object.entries(doc.tools ?? {})) {
    if (tools[name]) throw new Error(`ツール名が重複しています: ${name}`);
    for (const k of [...TOOL_META_KEYS, "args", "result", "examples"]) {
      if (t[k] === undefined) throw new Error(`${name}: ${k} がありません (${file})`);
    }
    tools[name] = t;
  }
}

// ツールの args/result の型を $defs に登録する（例: fs.list → FsListArgs / FsListResult）
const pascal = (name) =>
  name
    .split(/[._]/)
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("");
for (const [name, t] of Object.entries(tools)) {
  addDefs(name, { [`${pascal(name)}Args`]: t.args, [`${pascal(name)}Result`]: t.result });
}

const bundle = {
  $schema: "https://json-schema.org/draft/2020-12/schema",
  $id: SCHEMA_ID,
  title: "LocalLlmAgentProtocol",
  description: `local-llm-agent protocol v${PROTOCOL_VERSION}（生成物。編集しないこと）`,
  $defs: sortKeys(defs),
};

// ---- $ref を展開して自己完結したスキーマにする（guided_json / ツール単体の検証用） ----
function deref(node, stack = []) {
  if (Array.isArray(node)) return node.map((n) => deref(n, stack));
  if (node === null || typeof node !== "object") return node;
  if (typeof node.$ref === "string") {
    const m = /^#\/\$defs\/(.+)$/.exec(node.$ref);
    if (!m) throw new Error(`未対応の $ref: ${node.$ref}`);
    const target = defs[m[1]];
    if (!target) throw new Error(`$ref の参照先がありません: ${node.$ref}`);
    if (stack.includes(m[1])) throw new Error(`循環参照: ${[...stack, m[1]].join(" -> ")}`);
    const { $ref: _ref, ...rest } = node;
    return { ...deref(target, [...stack, m[1]]), ...deref(rest, stack) };
  }
  return Object.fromEntries(Object.entries(node).map(([k, v]) => [k, deref(v, stack)]));
}

function withContinuation(args) {
  // MCP の SDK は inputSchema の最上位に type: "object" を要求する
  return {
    type: "object",
    anyOf: [
      {
        type: "object",
        additionalProperties: false,
        required: ["continuation"],
        properties: { continuation: deref({ $ref: "#/$defs/Continuation" }) },
      },
      args,
    ],
  };
}

const toolSpecs = Object.keys(tools)
  .sort()
  .map((name) => {
    const t = tools[name];
    const args = deref(t.args);
    return {
      name,
      description: t.description,
      args_schema: t.paginated ? withContinuation(args) : args,
      result_schema: deref(t.result),
      effect: t.effect,
      path_args: t.path_args,
      idempotent: t.idempotent,
      untrusted_output: t.untrusted_output,
      timeout_ms: t.timeout_ms,
      priority: t.priority,
      paginated: Boolean(t.paginated),
      examples: t.examples,
    };
  });

const toolsDoc = { protocol_version: PROTOCOL_VERSION, tools: toolSpecs };

// ---- TypeScript の型 ----
// if/then の条件（type ごとの payload の形）は json-schema-to-typescript が扱えず、型全体が
// 「何でも入る」型になってしまう。型生成のときだけ条件を外す（検証用の JSON Schema には残す）。
const tsBundle = structuredClone(bundle);
for (const def of Object.values(tsBundle.$defs)) {
  if (Array.isArray(def.allOf) && def.allOf.every((x) => x && typeof x === "object" && "if" in x)) delete def.allOf;
}
// 形を決めていない object（payload・args・data など）は、TS では任意のキーを持つ型にする
(function openFreeObjects(node) {
  if (Array.isArray(node)) return node.forEach(openFreeObjects);
  if (!node || typeof node !== "object") return;
  if (node.type === "object" && !node.properties && node.additionalProperties === undefined && !node.$ref) {
    node.additionalProperties = true;
  }
  Object.values(node).forEach(openFreeObjects);
})(tsBundle);
const tsBody = await compile(tsBundle, "LocalLlmAgentProtocol", {
  bannerComment: "",
  unreachableDefinitions: true,
  additionalProperties: false,
  strictIndexSignatures: true,
  format: false,
});
const header = "/* 生成物（packages/protocol/scripts/gen.mjs）。編集しないこと。 */\n/* eslint-disable */\n";
const typesTs = header + tsBody;
const namesTs =
  header +
  `export const PROTOCOL_VERSION = ${JSON.stringify(PROTOCOL_VERSION)} as const;\n` +
  `export const TOOL_NAMES = ${JSON.stringify(toolSpecs.map((t) => t.name), null, 2)} as const;\n` +
  `export type ToolName = (typeof TOOL_NAMES)[number];\n`;

// ---- 出力 ----
const outputs = [
  [join(OUT_SCHEMA, "protocol.schema.json"), json(bundle)],
  [join(OUT_SCHEMA, "tools.json"), json(toolsDoc)],
  [join(OUT_PY_DATA, "protocol.schema.json"), json(bundle)],
  [join(OUT_PY_DATA, "tools.json"), json(toolsDoc)],
  [join(OUT_TS, "types.ts"), typesTs],
  [join(OUT_TS, "names.ts"), namesTs],
];

let stale = 0;
for (const [file, content] of outputs) {
  if (CHECK) {
    const current = existsSync(file) ? readFileSync(file, "utf8") : null;
    if (current !== content) {
      console.error(`生成物が古いです: ${file}`);
      stale++;
    }
  } else {
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, content, "utf8");
  }
}
if (CHECK && stale) {
  console.error("pnpm protocol:gen を実行してください。");
  process.exit(1);
}
console.log(`${CHECK ? "checked" : "generated"}: ${Object.keys(defs).length} defs, ${toolSpecs.length} tools`);

function json(v) {
  return JSON.stringify(v, null, 2) + "\n";
}
function sortKeys(o) {
  return Object.fromEntries(Object.keys(o).sort().map((k) => [k, o[k]]));
}
