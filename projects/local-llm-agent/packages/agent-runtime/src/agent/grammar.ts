// Action の文法生成（T-03、プロトコル §1.3）。有効なツールの集合から guided_json 用のスキーマを作る。
import { protocolSchema, type ToolCatalogEntry } from "@lla/protocol";
import { sha256 } from "../util.js";

type Schema = Record<string, unknown>;

const cache = new Map<string, Schema>();

/** 共通定義の $ref を展開する（推論エンジンに渡すスキーマは自己完結させる）。 */
function deref(node: unknown, stack: string[] = []): unknown {
  if (Array.isArray(node)) return node.map((n) => deref(n, stack));
  if (node === null || typeof node !== "object") return node;
  const o = node as Schema;
  if (typeof o.$ref === "string") {
    const name = o.$ref.replace(/^#\/\$defs\//, "");
    if (stack.includes(name)) throw new Error(`循環参照: ${name}`);
    const { $ref: _r, ...rest } = o;
    return { ...(deref(protocolSchema.$defs[name], [...stack, name]) as Schema), ...(deref(rest, stack) as Schema) };
  }
  return Object.fromEntries(Object.entries(o).map(([k, v]) => [k, deref(v, stack)]));
}

/**
 * ToolCall は「ツールごとに tool を const にした変種」の oneOf にする。
 * これで、有効でないツールや引数の形の誤りは文法の段階で生成できなくなる。
 */
export function buildActionSchema(tools: ToolCatalogEntry[]): Schema {
  const names = tools.map((t) => t.name).sort();
  const key = sha256(names.join("\n"));
  const hit = cache.get(key);
  if (hit) return hit;

  const toolCalls = tools
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((t) => ({
      type: "object",
      additionalProperties: false,
      required: ["type", "tool", "args"],
      properties: {
        type: { const: "tool_call" },
        thought: { type: "string", maxLength: 200 },
        tool: { const: t.name },
        args: t.args_schema,
      },
    }));
  const schema: Schema = {
    oneOf: [
      ...toolCalls,
      deref({ $ref: "#/$defs/PlanUpdate" }),
      deref({ $ref: "#/$defs/AskUser" }),
      deref({ $ref: "#/$defs/Final" }),
    ],
  };
  cache.set(key, schema);
  return schema;
}

export function clearGrammarCache(): void {
  cache.clear();
}
