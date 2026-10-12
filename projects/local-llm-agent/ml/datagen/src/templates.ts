// 手順例の雛形（T-52、設計書 §8.3）。乱数の種から、依頼の言い回し・ファイルの中身・正しい手順・確認方法を作る。
// 実データは使わない（合成のメモ・請求書・報告）。社名・船名は架空のものだけ。

export interface Episode {
  id: string;
  template: string;
  request: string;
  /** 作業フォルダに置くファイル（相対パス → 中身）。invoices は Python で PDF を作る */
  files: Record<string, string>;
  invoices?: { dir: string; count: number; broken?: Record<string, number[]> };
  /** モデルが返すべき手（順番どおり） */
  actions: Record<string, unknown>[];
  /** 抽出推論の決め打ち（invoice.extract が使う。invoices があるとき gen-invoices が作る） */
  checks: Record<string, unknown>[];
  /** 確認に使う正解ファイル（episode のフォルダからの相対 → 中身） */
  expected: Record<string, string>;
}

/** 種つきの乱数（mulberry32）。同じ種なら同じ手順例になる */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const pick = <T>(r: () => number, xs: readonly T[]): T => xs[Math.floor(r() * xs.length)]!;
const int = (r: () => number, lo: number, hi: number) => lo + Math.floor(r() * (hi - lo + 1));

const ITEMS = ["ロープ", "ペンキ", "ワイヤー", "軍手", "ボルト", "フィルター", "潤滑油", "電球", "ホース", "シャックル"];
const VESSELS = ["MV EXAMPLE STAR", "MV SAMPLE OCEAN", "MV DEMO WAVE"];
const plan = (titles: string[]) => ({ type: "plan_update", plan: titles.map((t, i) => ({ id: `t${i + 1}`, title: t, status: i === 0 ? "doing" : "todo" })) });
const tool = (name: string, args: Record<string, unknown>, thought?: string) => ({ type: "tool_call", ...(thought ? { thought } : {}), tool: name, args });
const final = (summary: string, outputs: { path: string; description: string }[] = [], failures: { item: string; reason: string }[] = []) => ({
  type: "final",
  summary,
  outputs,
  failures,
  needs_review: [],
});
const csvText = (rows: (string | number)[][]) => rows.map((r) => r.join(",")).join("\n") + "\n";

// ------------------------------------------------------------------ 雛形
/** メモから品目を拾って CSV にする */
function memoToCsv(r: () => number, id: string): Episode {
  const n = int(r, 2, 6);
  const items = [...ITEMS].sort(() => r() - 0.5).slice(0, n).map((name) => [name, int(r, 1, 30)] as [string, number]);
  const memoName = pick(r, ["メモ.txt", "発注メモ.txt", "倉庫メモ.txt"]);
  const out = pick(r, ["品目.csv", "発注品目.csv", "一覧.csv"]);
  const memo = `${pick(r, ["倉庫の在庫確認", "次回入渠の発注", "備品の補充"])}\n` + items.map(([a, b]) => `- ${a} ${b}個`).join("\n") + "\n";
  const request = pick(r, [
    `${memoName} の品名と数量を、${out} にまとめて`,
    `${memoName}を読んで、品名・数量の表を ${out} に作ってください`,
    `${out} を作って。中身は ${memoName} に書いてある品名と個数`,
  ]);
  return {
    id,
    template: "memo_to_csv",
    request,
    files: { [memoName]: memo },
    actions: [
      plan(["メモを読む", "CSV を作る", "完了報告"]),
      tool("fs.read_text", { path: memoName }, "メモの中身を確認します"),
      tool("csv.write", { path: out, header: ["品名", "数量"], rows: items }, "品名と数量を CSV に書きます"),
      final(`${out} を作りました（${n}品目）。`, [{ path: out, description: "品名と数量" }]),
    ],
    checks: [{ type: "csv_rows_equal", file: out, expected: "expected.csv" }, { type: "no_files_outside_workspace" }],
    expected: { "expected.csv": csvText([["品名", "数量"], ...items]) },
  };
}

/** 存在しないファイルを読もうとして失敗 → 一覧で確かめて正しい名前で読み直す（失敗からの回復） */
function recoverFromMissing(r: () => number, id: string): Episode {
  const ep = memoToCsv(r, id);
  const real = Object.keys(ep.files)[0]!;
  const wrong = real.replace(".txt", "_最新.txt");
  ep.template = "recover_missing_file";
  ep.request = ep.request.replace(real, wrong);
  ep.actions = [
    plan(["メモを読む", "CSV を作る", "完了報告"]),
    tool("fs.read_text", { path: wrong }, "依頼されたメモを読みます"),
    tool("fs.list", { path: "." }, "ファイルが見つからないので、フォルダの中身を確かめます"),
    tool("fs.read_text", { path: real }, `よく似た名前の ${real} を読みます`),
    ...ep.actions.slice(2, 3),
    final(`${wrong} は無かったため、${real} を使って ${(ep.actions[2] as { args: { path: string } }).args.path} を作りました。`, [
      { path: (ep.actions[2] as { args: { path: string } }).args.path, description: "品名と数量" },
    ]),
  ];
  return ep;
}

/** Word で報告書を作る */
function docxReport(r: () => number, id: string): Episode {
  const vessel = pick(r, VESSELS);
  const month = int(r, 1, 12);
  const days = int(r, 3, 14);
  const out = pick(r, ["入渠報告書.docx", `${month}月入渠報告.docx`, "報告書.docx"]);
  const paragraphs = [`船名: ${vessel}`, `入渠期間: ${days}日間`, `第${int(r, 2, 5)}条 費用の精算は入渠完了の翌月末とする。`];
  return {
    id,
    template: "docx_report",
    request: pick(r, [
      `${month}月の入渠報告書を Word で作って。船名は ${vessel}、期間は ${days} 日、精算時期も書いて`,
      `${vessel} の入渠報告（${month}月・${days}日間）を ${out} にまとめてください`,
    ]),
    files: {},
    actions: [
      plan(["報告の内容を整理する", "Word で報告書を作る", "完了報告"]),
      tool(
        "docx.create",
        { path: out, blocks: [{ kind: "heading", level: 1, text: `${month}月 入渠報告書` }, ...paragraphs.map((t) => ({ kind: "paragraph", text: t }))] },
        "報告書を新しく作ります",
      ),
      final(`${out} を作りました。`, [{ path: out, description: "入渠報告書" }]),
    ],
    checks: [{ type: "docx_contains", file: out, text: vessel }, { type: "docx_contains", file: out, text: `${days}日間` }],
    expected: {},
  };
}

/** 請求書を一括で読み取り、Excel に転記する（壊れたファイルは失敗として報告） */
function invoicesToExcel(r: () => number, id: string): Episode {
  const count = int(r, 2, 5);
  const brokenAt = r() < 0.4 ? int(r, 1, count) : null;
  const pad = (n: number) => `INV-${String(n).padStart(3, "0")}.pdf`;
  const ok = Array.from({ length: count }, (_, i) => i + 1).filter((n) => n !== brokenAt);
  const out = pick(r, ["送金明細.xlsx", "請求一覧.xlsx"]);
  return {
    id,
    template: "invoices_to_excel",
    request: pick(r, [
      `請求書フォルダの PDF を読んで、請求元と金額を ${out} にまとめて`,
      `請求書をまとめて読み取り、${out} に転記してください。列は請求元・金額`,
    ]),
    files: {},
    invoices: { dir: "請求書", count, ...(brokenAt ? { broken: { empty: [brokenAt] } } : {}) },
    actions: [
      plan(["請求書を一括で読み取る", "Excel に転記する", "完了報告"]),
      tool(
        "batch.run",
        {
          items: "glob:請求書/*.pdf",
          pipeline: { tool: "invoice.extract", args: { schema: "invoice.v1" } },
          output: {
            xlsx: out,
            sheet: "明細",
            columns: [
              { header: "請求元", from: "/fields/issuer_name/value" },
              { header: "金額", from: "/fields/total/value" },
            ],
          },
        },
        "請求書をまとめて読み取り、Excel に転記します",
      ),
      final(
        `請求書${count}件のうち${ok.length}件を ${out} に転記しました。${brokenAt ? "1件は読み取れませんでした。" : ""}`,
        [{ path: out, description: "請求元と金額" }],
        brokenAt ? [{ item: `請求書/${pad(brokenAt)}`, reason: "空のファイルです" }] : [],
      ),
    ],
    checks: [
      { type: "xlsx_equals", file: out, sheet: "明細", expected: "expected.json" },
      ...(brokenAt ? [{ type: "final_failures", items: [`請求書/${pad(brokenAt)}`] }] : []),
    ],
    expected: {
      "expected.json": JSON.stringify({ rows: [["請求元", "金額"], ...ok.map((n) => [`Example Shipping Co. ${n}`, n * 1000])] }),
    },
  };
}

export const TEMPLATES: Record<string, (r: () => number, id: string) => Episode> = {
  memo_to_csv: memoToCsv,
  recover_missing_file: recoverFromMissing,
  docx_report: docxReport,
  invoices_to_excel: invoicesToExcel,
};
