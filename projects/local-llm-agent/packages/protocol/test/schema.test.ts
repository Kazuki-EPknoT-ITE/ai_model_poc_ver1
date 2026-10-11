// 試験仕様 S（スキーマ）: 全ツールの例の検証・カタログの整合・主要な型の正例/負例
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { TOOL_NAMES, getTool, protocolSchema, toolCatalog, validateDef, validateToolArgs } from "../src/index.js";

const EXAMPLES = join(dirname(fileURLToPath(import.meta.url)), "..", "schemas", "examples");

describe("tool catalog", () => {
  it("has the 47 tools defined in local_llm_protocol.md §3", () => {
    expect(toolCatalog.length).toBe(47);
    expect(new Set(TOOL_NAMES).size).toBe(TOOL_NAMES.length);
  });

  it.each(toolCatalog.map((t) => [t.name, t] as const))("%s: metadata is consistent", (_name, t) => {
    const { paginated: _p, examples: _e, ...spec } = t;
    expect(validateDef("ToolSpec", spec).errors).toEqual([]);
    expect([...t.description].length).toBeLessThanOrEqual(120);
    expect(t.examples.length).toBeGreaterThan(0);
    if (t.effect === "net") expect(t.name.startsWith("web.")).toBe(true);
    if (t.effect === "delete") expect(["fs.delete", "fs.move"]).toContain(t.name);
  });

  it.each(toolCatalog.map((t) => [t.name, t] as const))("%s: path_args point to existing properties", (_name, t) => {
    // path_args は args の JSON Pointer。"*" は配列の全要素を表す。
    for (const pointer of t.path_args) {
      const top = pointer.split("/")[1];
      const schema = t.args_schema as {
        properties?: Record<string, unknown>;
        anyOf?: { properties?: Record<string, unknown> }[];
        oneOf?: unknown[];
      };
      const props = schema.properties ?? schema.anyOf?.[1]?.properties;
      expect(props, `${t.name} ${pointer}`).toBeDefined();
      expect(Object.keys(props ?? {}), `${t.name} ${pointer}`).toContain(top);
    }
  });

  it.each(toolCatalog.flatMap((t) => t.examples.map((ex, i) => [`${t.name}#${i}`, t.name, ex] as const)))(
    "%s: example is valid",
    (_label, name, ex) => {
      const r = validateToolArgs(name, ex);
      expect(r.errors).toEqual([]);
    },
  );

  it("paginated tools accept a continuation-only call", () => {
    for (const t of toolCatalog.filter((x) => x.paginated)) {
      expect(validateToolArgs(t.name, { continuation: "c1" }).ok, t.name).toBe(true);
    }
    expect(validateToolArgs("fs.stat", { continuation: "c1" }).ok).toBe(false);
  });

  it("rejects unknown properties and out-of-range values", () => {
    expect(validateToolArgs("fs.list", { path: "a", unknown: 1 }).ok).toBe(false);
    expect(validateToolArgs("fs.list", { depth: 9 }).ok).toBe(false);
    expect(validateToolArgs("docx.edit", { path: "a.docx", ops: [{ op: "replace_text", block_id: "x:1", text: "" }] }).ok).toBe(false);
    expect(validateToolArgs("web.fetch", { url: "file:///C:/Windows/win.ini" }).ok).toBe(false);
    expect(validateToolArgs("xlsx.write_cells", { path: "a.xlsx", sheet: "S", cells: [{ ref: "A1", formula: "SUM(A1)" }] }).ok).toBe(false);
  });

  it("getTool returns undefined for unknown tools", () => {
    expect(getTool("shell.exec")).toBeUndefined();
  });
});

// schemas/examples/*.json: { "def": "<$defs名>", "valid": [...], "invalid": [...] }
const exampleFiles = readdirSync(EXAMPLES).filter((f) => f.endsWith(".json"));

describe("definition examples", () => {
  it("example files exist", () => {
    expect(exampleFiles.length).toBeGreaterThan(0);
  });

  for (const file of exampleFiles) {
    const doc = JSON.parse(readFileSync(join(EXAMPLES, file), "utf8")) as {
      def: string;
      valid: unknown[];
      invalid: unknown[];
    };
    describe(file, () => {
      it("references an existing definition", () => {
        expect(protocolSchema.$defs[doc.def]).toBeDefined();
      });
      doc.valid.forEach((v, i) =>
        it(`valid #${i}`, () => {
          expect(validateDef(doc.def, v).errors).toEqual([]);
        }),
      );
      doc.invalid.forEach((v, i) =>
        it(`invalid #${i}`, () => {
          expect(validateDef(doc.def, v).ok).toBe(false);
        }),
      );
    });
  }
});
