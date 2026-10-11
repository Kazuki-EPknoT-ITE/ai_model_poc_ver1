// local-llm-agent のプロトコル（TS 側の入口）。正本は schemas/src/*.yaml、型と JSON は生成物。
import { Ajv2020, type ValidateFunction, type ErrorObject } from "ajv/dist/2020.js";
import addFormatsModule from "ajv-formats";
import bundleJson from "../schemas/gen/protocol.schema.json" with { type: "json" };
import toolsJson from "../schemas/gen/tools.json" with { type: "json" };
import type { ToolSpec as GeneratedToolSpec } from "./generated/types.js";
import type { ToolName } from "./generated/names.js";

export * from "./generated/types.js";
export { PROTOCOL_VERSION, TOOL_NAMES, type ToolName } from "./generated/names.js";

/** tools.json の1件。生成した ToolSpec に、カタログ専用の項目を足したもの。 */
export interface ToolCatalogEntry extends Omit<GeneratedToolSpec, "name"> {
  name: ToolName;
  paginated: boolean;
  examples: unknown[];
}

type JsonSchema = Record<string, unknown>;

export const protocolSchema = bundleJson as unknown as { $id: string; $defs: Record<string, JsonSchema> };
export const toolCatalog = (toolsJson as unknown as { tools: ToolCatalogEntry[] }).tools;

const toolsByName = new Map<string, ToolCatalogEntry>(toolCatalog.map((t) => [t.name, t]));

export function getTool(name: string): ToolCatalogEntry | undefined {
  return toolsByName.get(name);
}

// ajv-formats は CJS の default export
const addFormats = (addFormatsModule as unknown as { default?: typeof addFormatsModule }).default ?? addFormatsModule;

function createAjv(): Ajv2020 {
  const ajv = new Ajv2020({ strict: true, allErrors: true, allowUnionTypes: true });
  (addFormats as unknown as (a: Ajv2020) => void)(ajv);
  ajv.addSchema(protocolSchema as JsonSchema);
  return ajv;
}

const ajv = createAjv();
const defValidators = new Map<string, ValidateFunction>();
const toolValidators = new Map<string, ValidateFunction>();

export interface ValidationResult {
  ok: boolean;
  errors: ErrorObject[];
}

/** $defs の名前（"Action", "ToolResult", "RuntimeEvent" など）で検証する。 */
export function validateDef(defName: string, value: unknown): ValidationResult {
  let v = defValidators.get(defName);
  if (!v) {
    if (!protocolSchema.$defs[defName]) throw new Error(`unknown definition: ${defName}`);
    const found = ajv.getSchema(`${protocolSchema.$id}#/$defs/${defName}`);
    if (!found) throw new Error(`cannot compile definition: ${defName}`);
    v = found;
    defValidators.set(defName, v);
  }
  const ok = v(value) as boolean;
  return { ok, errors: ok ? [] : [...(v.errors ?? [])] };
}

/** ツールの args を、そのツールの args_schema（$ref 展開済み）で検証する。 */
export function validateToolArgs(toolName: string, args: unknown): ValidationResult {
  let v = toolValidators.get(toolName);
  if (!v) {
    const t = getTool(toolName);
    if (!t) throw new Error(`unknown tool: ${toolName}`);
    v = ajv.compile(t.args_schema as JsonSchema);
    toolValidators.set(toolName, v);
  }
  const ok = v(args) as boolean;
  return { ok, errors: ok ? [] : [...(v.errors ?? [])] };
}
