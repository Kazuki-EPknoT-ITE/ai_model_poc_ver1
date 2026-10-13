// 手順例の雛形（T-52、設計書 §8.3）。乱数の種から、依頼の言い回し・ファイルの中身・正しい手順・確認方法を作る。
// 実データは使わない（合成のメモ・請求書・報告）。社名・船名は架空のものだけ。

export interface Episode {
  id: string;
  template: string;
  request: string;
  /** 作業フォルダに置くファイル（相対パス → 中身）。invoices は Python で PDF を作る */
  files: Record<string, string>;
  invoices?: { dir: string; count: number; broken?: Record<string, number[]> };
  /** 承認待ちになったら、利用者としてこの判断を返す（承認待ちで完了を出したあと） */
  approval?: "approve" | "reject";
  /** 質問（ask_user）への利用者の回答（承認待ちと同じく、待ちの完了を出したあとに返す） */
  answer?: string;
  /** 作業フォルダに置く Excel（相対パス → シートと行）。Python（openpyxl）で作る */
  xlsx?: Record<string, { sheet: string; rows: (string | number)[][] }>;
  /** 作業フォルダに置く Word（相対パス → 見出しと段落）。Python（python-docx）で作る。ID は見出しが p:1、段落が p:2〜 */
  docx?: Record<string, { heading: string; paragraphs: string[] }>;
  /** モック Gateway のシナリオの fixtures（rag など）と features */
  fixtures?: Record<string, unknown>;
  features?: Record<string, boolean>;
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

/** 経費の CSV を部署ごとに集計する（計算は data.transform に任せ、自分で足し算しない） */
function expenseSummary(r: () => number, id: string): Episode {
  const depts = [...["工務部", "総務部", "営業部", "船舶部"]].sort(() => r() - 0.5).slice(0, int(r, 2, 4));
  const kinds = ["交通費", "消耗品", "通信費", "会議費"];
  const rows = Array.from({ length: int(r, 4, 12) }, () => [pick(r, depts), pick(r, kinds), int(r, 1, 50) * 100] as [string, string, number]);
  const src = pick(r, ["経費.csv", "経費明細.csv"]);
  const out = pick(r, ["部署別集計.csv", "集計.csv"]);
  const sums = new Map<string, number>();
  for (const [d, , v] of rows) sums.set(d, (sums.get(d) ?? 0) + v);
  const expected = [...sums.entries()].sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  return {
    id,
    template: "expense_summary",
    request: pick(r, [`${src} を部署ごとに合計して、${out} に出して`, `部署別の経費の合計を ${out} にまとめてください（元は ${src}）`]),
    files: { [src]: csvText([["部署", "費目", "金額"], ...rows]) },
    actions: [
      plan(["経費の CSV を確認する", "部署ごとに集計する", "完了報告"]),
      tool("csv.read", { path: src }, "列と中身を確認します"),
      tool(
        "data.transform",
        {
          inputs: [{ name: "exp", source: { csv: src } }],
          steps: [
            { op: "group", by: ["部署"], aggs: [{ column: "金額", fn: "sum", as: "合計" }] },
            { op: "sort", by: [{ column: "部署" }] },
          ],
          output: { csv: out },
        },
        "部署ごとの合計を data.transform で計算します",
      ),
      final(`${out} に部署別の合計（${expected.length}部署）を出しました。`, [{ path: out, description: "部署別の経費合計" }]),
    ],
    checks: [{ type: "csv_rows_equal", file: out, expected: "expected.csv" }],
    expected: { "expected.csv": csvText([["部署", "合計"], ...expected]) },
  };
}

/**
 * 作業フォルダの外へのコピー（承認が要る）。承認を待つあいだに別の作業を進め、
 * 承認されたら完了、却下されたら失敗として報告する（保留への対応）
 */
function copyOutsideWithApproval(r: () => number, id: string): Episode {
  const ep = memoToCsv(r, id);
  const memo = Object.keys(ep.files)[0]!;
  const write = ep.actions[2] as { args: { path: string } };
  const dst = `../outside/${pick(r, ["共有", "提出用"])}_${memo}`;
  const decision = r() < 0.3 ? "reject" : "approve";
  ep.template = "copy_outside_approval";
  ep.approval = decision;
  ep.request = `${memo} を ${dst} にコピーしておいて。それと、品名と数量を ${write.args.path} にまとめて`;
  ep.actions = [
    plan(["メモを読む", "メモを作業フォルダの外へコピーする", "CSV を作る", "完了報告"]),
    tool("fs.read_text", { path: memo }, "メモの中身を確認します"),
    tool("fs.copy", { src: memo, dst }, "メモを指定の場所へコピーします（承認が要ります）"),
    tool("csv.write", write.args, "承認を待つあいだに CSV を作ります"),
    final(`${write.args.path} を作りました。コピーは承認待ちです。`, [{ path: write.args.path, description: "品名と数量" }]),
    decision === "approve"
      ? final(`${write.args.path} を作り、${memo} を ${dst} にコピーしました。`, [
          { path: write.args.path, description: "品名と数量" },
          { path: dst, description: "メモのコピー" },
        ])
      : final(`${write.args.path} を作りました。${memo} のコピーは却下されたため、行っていません。`, [{ path: write.args.path, description: "品名と数量" }], [
          { item: memo, reason: "作業フォルダ外へのコピーが却下されました" },
        ]),
  ];
  ep.checks = [
    { type: "csv_rows_equal", file: write.args.path, expected: "expected.csv" },
    decision === "approve" ? { type: "file_exists", file: dst } : { type: "final_failures", items: [memo] },
  ];
  return ep;
}

/** Excel の単価表の1か所を直す（探してから、セルの ID を指定して書く） */
function xlsxPriceUpdate(r: () => number, id: string): Episode {
  const items = [...ITEMS].sort(() => r() - 0.5).slice(0, int(r, 3, 8)).map((name) => [name, int(r, 1, 99) * 10] as [string, number]);
  const k = int(r, 0, items.length - 1);
  const target = items[k]![0];
  const price = int(r, 1, 99) * 10 + 5;
  const file = pick(r, ["単価表.xlsx", "備品単価.xlsx"]);
  const sheet = pick(r, ["単価", "Sheet1"]);
  const before = [["品名", "単価"], ...items];
  const after = before.map((row, i) => (i === k + 1 ? [row[0]!, price] : row));
  return {
    id,
    template: "xlsx_price_update",
    request: pick(r, [`${file} の${target}の単価を ${price} 円に直して`, `${target}の単価が ${price} 円に変わったので、${file} を更新してください`]),
    files: {},
    xlsx: { [file]: { sheet, rows: before } },
    actions: [
      plan([`${target}の行を探す`, "単価を書き換える", "完了報告"]),
      tool("xlsx.find", { path: file, sheet, query: target, match: "exact" }, `${target}の行を探します`),
      tool("xlsx.write_cells", { path: file, sheet, cells: [{ ref: `B${k + 2}`, value: price }] }, `B${k + 2} の単価を書き換えます`),
      final(`${file} の${target}の単価を ${price} 円に直しました（${sheet}!B${k + 2}）。`, [{ path: file, description: "単価表" }]),
    ],
    checks: [{ type: "xlsx_equals", file, sheet, expected: "expected.json" }],
    expected: { "expected.json": JSON.stringify({ rows: after }) },
  };
}

/** どのファイルか決められないときは質問し、回答を待ってから続ける */
function askWhichMemo(r: () => number, id: string): Episode {
  const months = [int(r, 1, 6), int(r, 7, 12)];
  const memos = months.map((m) => {
    const items = [...ITEMS].sort(() => r() - 0.5).slice(0, int(r, 2, 5)).map((name) => [name, int(r, 1, 30)] as [string, number]);
    return { name: `発注メモ_${m}月.txt`, items, text: `${m}月の発注\n` + items.map(([a, b]) => `- ${a} ${b}個`).join("\n") + "\n" };
  });
  const chosen = memos[r() < 0.5 ? 0 : 1]!;
  const out = pick(r, ["発注品目.csv", "品目.csv"]);
  return {
    id,
    template: "ask_which_memo",
    request: pick(r, [`発注メモの品名と数量を ${out} にまとめて`, `発注メモから ${out} を作ってください`]),
    files: Object.fromEntries(memos.map((m) => [m.name, m.text])),
    answer: chosen.name,
    actions: [
      plan(["対象のメモを確かめる", "メモを読む", "CSV を作る", "完了報告"]),
      tool("fs.list", { path: "." }, "発注メモを探します"),
      { type: "ask_user", question: `発注メモが2つあります（${memos[0]!.name}、${memos[1]!.name}）。どちらを使いますか？`, choices: memos.map((m) => m.name) },
      final("どちらの発注メモを使うか、回答を待っています。"),
      tool("fs.read_text", { path: chosen.name }, `回答のあった ${chosen.name} を読みます`),
      tool("csv.write", { path: out, header: ["品名", "数量"], rows: chosen.items }, "品名と数量を CSV に書きます"),
      final(`${chosen.name} から ${out} を作りました（${chosen.items.length}品目）。`, [{ path: out, description: "品名と数量" }]),
    ],
    checks: [{ type: "csv_rows_equal", file: out, expected: "expected.csv" }],
    expected: { "expected.csv": csvText([["品名", "数量"], ...chosen.items]) },
  };
}

const CLAUSES = [
  (d: number) => `支払期限は、請求書の受領後${d}日以内とする。`,
  (d: number) => `検収は、納品後${d}日以内に行う。`,
  (d: number) => `本契約の解約は、${d}日前までに書面で通知する。`,
];

/** Word の契約書の条文を1か所直す（読んで段落の ID を確かめ、その段落だけを置き換える） */
function docxClauseFix(r: () => number, id: string): Episode {
  const file = pick(r, ["業務委託契約書.docx", "契約書_案.docx"]);
  const days = [int(r, 1, 6) * 10, int(r, 1, 6) * 10, int(r, 1, 6) * 10];
  const paragraphs = CLAUSES.map((c, i) => `第${i + 1}条 ${c(days[i]!)}`);
  const k = int(r, 0, 2);
  const from = `${days[k]}日`;
  const to = `${days[k]! + int(r, 1, 3) * 15}日`;
  // 半分は「変更履歴を付けて」と頼まれる（相手方に見せる修正案）
  const track = r() < 0.5;
  return {
    id,
    template: "docx_clause_fix",
    request:
      pick(r, [`${file} の第${k + 1}条、${from}を${to}に直してください`, `契約書（${file}）の第${k + 1}条の日数を ${to} に変更して`]) +
      (track ? "。変更履歴を付けてください" : ""),
    files: {},
    docx: { [file]: { heading: "業務委託契約書", paragraphs } },
    actions: [
      plan([`第${k + 1}条の段落を確かめる`, "日数を書き換える", "完了報告"]),
      tool("docx.read", { path: file }, "契約書を読み、第" + (k + 1) + "条の段落を確かめます"),
      tool(
        "docx.edit",
        { path: file, ops: [{ op: "replace_in_block", block_id: `p:${k + 2}`, find: from, replace: to }], ...(track ? { track_changes: true } : {}) },
        `第${k + 1}条（p:${k + 2}）の${from}を${to}に置き換えます${track ? "（変更履歴付き）" : ""}`,
      ),
      final(`${file} の第${k + 1}条を「${from}」から「${to}」に直しました${track ? "（変更履歴付き）" : ""}。`, [{ path: file, description: "修正した契約書" }]),
    ],
    checks: [
      { type: "docx_contains", file, text: paragraphs[k]!.replace(from, to) },
      ...paragraphs.filter((_, i) => i !== k).map((text) => ({ type: "docx_contains", file, text })),
    ],
    expected: {},
  };
}

const RULES = [
  { path: "規程/旅費規程.docx", location: "第3条", topic: "国内出張の日当", q: "出張 日当", fact: (n: number) => `1日${n.toLocaleString("en-US")}円` },
  { path: "規程/慶弔規程.docx", location: "第5条", topic: "結婚祝い金", q: "結婚 祝い金", fact: (n: number) => `${n.toLocaleString("en-US")}円` },
  { path: "規程/車両管理規程.docx", location: "第8条", topic: "社用車の私用での走行の上限", q: "社用車 私用", fact: (n: number) => `月${n}km` },
];

/** 社内文書の検索で答える。見つかれば出典を付け、見つからなければ推測で答えずにそう伝える */
function ragAnswer(r: () => number, id: string): Episode {
  const rule = pick(r, RULES);
  const n = int(r, 2, 30) * 100;
  const found = r() < 0.75;
  const fact = rule.fact(n);
  return {
    id,
    template: "rag_answer",
    request: pick(r, [`${rule.topic}を規程で確認して教えて`, `社内規程で${rule.topic}を調べてください`]),
    files: {},
    features: { rag: true, ocr: false, web: false },
    fixtures: {
      rag: { hits: found ? [{ ref: "d1", path: rule.path, location: rule.location, score: 0.9, excerpt: `${rule.topic}は${fact}とする。` }] : [] },
    },
    actions: [
      plan(["社内文書を検索する", "根拠を確かめる", "回答する"]),
      tool("rag.search", { query: rule.q, top_k: 5 }, "社内規程を検索します"),
      found
        ? { ...final(`${rule.topic}は${fact}です（${rule.path} ${rule.location}）。`), citations: [{ kind: "rag", ref: rule.path, location: rule.location }] }
        : final(`社内文書を検索しましたが、${rule.topic}の定めは見つかりませんでした。総務に確認してください。`),
    ],
    checks: [{ type: "final_cites", summary_contains: found ? [fact] : ["見つかりませんでした"], refs: found ? [rule.path] : [] }],
    expected: {},
  };
}

export const TEMPLATES: Record<string, (r: () => number, id: string) => Episode> = {
  memo_to_csv: memoToCsv,
  recover_missing_file: recoverFromMissing,
  docx_report: docxReport,
  invoices_to_excel: invoicesToExcel,
  expense_summary: expenseSummary,
  copy_outside_approval: copyOutsideWithApproval,
  xlsx_price_update: xlsxPriceUpdate,
  ask_which_memo: askWhichMemo,
  docx_clause_fix: docxClauseFix,
  rag_answer: ragAnswer,
};
