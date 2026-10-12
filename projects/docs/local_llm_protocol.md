# ローカルLLMデスクトップエージェント プロトコル仕様

2026年10月11日 · v1.0-draft（基本設計書 §5.2・§7 の詳細化）

モデルの出力形式（アクション）、ツールの呼び出し規約と全ツールの定義、エラーの分類、Main と Runtime の間のメッセージを定める。**実装上の正本は `projects/local-llm-agent/packages/protocol/` の JSON Schema** であり、本書はその設計仕様にあたる。本書を変更したら JSON Schema も同じコミットで更新する（逆も同じ）。

- 表記: 型は TypeScript 風に書く。`?` は省略可。`string(path)` は作業フォルダからの相対パス、または承認対象になる絶対パス。
- 文字コードはすべて UTF-8。日時は ISO 8601（UTC）。
- `protocol_version`: `"1.0"`。互換性を壊す変更ではメジャー番号を上げる。学習データ（`ml/datagen`）は、この版を記録して作る。

---

## 1. アクション（モデル出力）

モデルの1手は次のどれかの JSON オブジェクトとし、推論エンジンの `guided_json` でスキーマを強制する。`tool_call` の `tool` と `args` の組み合わせは、その時点で使えるツールから oneOf を作って縛る（§1.3）。

```ts
type Action = ToolCall | PlanUpdate | AskUser | Final;

interface ToolCall {
  type: "tool_call";
  thought?: string;          // 200字以内。画面の手順カードに表示する
  tool: ToolName;            // 例 "xlsx.write_cells"
  args: object;              // ツールごとの args スキーマ
}

interface PlanUpdate {
  type: "plan_update";
  plan: { id: string; title: string; status: "todo" | "doing" | "done" | "skipped" }[]; // 1〜30件
  note?: string;
}

interface AskUser {          // 利用者への質問。承認と同じく保留扱いで、他の手順は進める
  type: "ask_user";
  question: string;          // 300字以内
  choices?: string[];        // 2〜5件。無ければ自由記述
}

interface Final {
  type: "final";
  summary: string;           // 完了報告（Markdown、2,000字以内）
  outputs: { path: string; description: string }[];
  failures: { item: string; reason: string }[];          // RUN-06 の失敗一覧
  needs_review: { path: string; location?: string; reason: string }[]; // 人の確認が必要な箇所
  citations?: { kind: "file" | "web" | "rag"; ref: string; location?: string }[]; // AGT-07, NET-05
}
```

### 1.1 制約
- `thought` は推論の記録ではなく、利用者向けの一言（「請求書10件を一括抽出します」など）。
- 1回の応答で返すアクションは1つだけ。並列のツール呼び出しはしない（小さいモデルの安定性を優先する）。並列にしたい処理は `batch.run` を使う。
- `final` は、承認待ち・質問待ちが残っていると Runtime に受理されない（`WaitingApprovals` に遷移する。設計書 §5.1）。

### 1.2 Runtime による検証
文法の外で起きる意味上の誤りは、Runtime が次の順に検証し、エラーをツール結果として返して再生成させる（AGT-05）。

1. `tool` がその会話で有効か（ネット設定、プラグインの有効化など）
2. `args` がスキーマに合うか（文法で強制されているが、再確認する）
3. 参照している ID（`block_id`、`approval_id` など）が存在するか
4. ポリシー判定（設計書 §5.3）

### 1.3 文法の動的生成
- 毎手、`{type: tool_call, tool: const, args: schema}` の oneOf を、有効なツールについて作る。ツールが30個を超える場合は、ツールの説明文を短くしたうえで同じ oneOf を使う（文法のコンパイル結果は、ツール集合のハッシュでキャッシュする）。
- 数値のフィールドには `minimum` / `maximum`、文字列には `maxLength` を必ず付け、生成が暴走しないようにする。

---

## 2. ツール呼び出しの共通規約

### 2.1 ツール名
`<名前空間>.<動詞>` の形式とする（例 `docx.edit`）。名前空間は次のとおり。

| 名前空間 | 実装 | 実行場所 |
| --- | --- | --- |
| `fs` | fs-tools（TS、Runtime 内蔵の MCP） | PC |
| `docx` `xlsx` `csv` `pdf` `data` `convert` `invoice` | office-tools（Python MCP stdio） | PC（OCR だけサーバー） |
| `batch` `memory` | Runtime 内蔵 | PC |
| `rag` | Gateway 経由 | サーバー |
| `web` | Gateway 経由で Web Relay | サーバー |
| `plugin.<id>` | 追加の MCP サーバー（AGT-09） | PC |

### 2.2 ツールのメタデータ（ToolSpec）

ツール定義の正本は `packages/protocol/schemas/src/tools/*.yaml`。生成された `tools.json` の args_schema は `$ref` を展開済みで、§2.5 の分割に対応するツール（`paginated`）は `anyOf: [{continuation}, 本来の引数]` の形になる。

```ts
interface ToolSpec {
  name: ToolName;
  description: string;        // モデルに見せる説明（120字以内）
  args_schema: JSONSchema;
  result_schema: JSONSchema;  // ToolResult.data の型
  effect: "read" | "write" | "delete" | "net" | "none";
  path_args: string[];        // ポリシー判定の対象になる引数（JSON Pointer。"*" は配列の全要素。例 "/inputs/*/source/xlsx"）
  idempotent: boolean;        // 同じ引数で2回呼んでも結果が同じか
  untrusted_output: boolean;  // 結果を <untrusted> で包むか
  timeout_ms: number;         // 一時的失敗とみなすまでの時間
  priority: "Must" | "Should" | "Could";
}
```

MCP の `tools/list` は `name` / `description` / `inputSchema` を返す。ToolSpec のそれ以外の項目は、MCP の `annotations` に `x-agent-*` キーとして入れる（例 `x-agent-effect: "write"`）。この注釈を持たないプラグインのツールは、**`effect: "write"`・承認必須として扱う**（安全側に倒す）。

### 2.3 結果の封筒（ToolResult）

```ts
interface ToolResult {
  ok: boolean;
  data?: object;                     // result_schema に従う
  error?: ToolError;
  untrusted?: UntrustedBlock[];      // 外部由来のテキスト（文書本文、Web 本文）
  continuation?: string;             // 結果が長い場合の続きトークン（§2.5）
  artifacts?: Artifact[];            // ファイルを書き換えた場合（設計書 §5.4）
  warnings?: string[];               // 例: "別名で保存しました: 送金明細 (AI編集 ...).xlsx"
  tokens_estimate: number;           // Runtime が文脈予算の計算に使う
}

interface UntrustedBlock { source: "file" | "web" | "rag" | "ocr"; ref: string; text: string; }

interface Artifact {
  path: string; op: "create" | "overwrite" | "delete" | "move" | "copy";
  pre_hash?: string; post_hash?: string;  // sha256（ファイル全体）
  backup_path?: string;
  renamed_from?: string;                   // ロックや競合で別名保存したときの元のパス
}
```

- モデルに渡すときは、`data` を JSON のまま、`untrusted` を `<untrusted source=".." ref="..">…</untrusted>` に変換して連結する（`prompts` 仕様 §4）。
- `untrusted` の中にある `</untrusted>` は `&lt;/untrusted&gt;` にエスケープする。

### 2.4 エラーの分類（ToolError）

```ts
interface ToolError {
  code: ErrorCode;
  kind: "transient" | "permanent" | "policy" | "deferred";
  message: string;          // モデル向けの日本語。次にどうすべきかを含める
  retry_after_ms?: number;  // transient のときの次回試行までの目安
  detail?: object;
}
```

| kind | Runtime の扱い | 例 |
| --- | --- | --- |
| `transient` | モデルに返さず、指数バックオフで無制限に再試行する（RUN-03）。画面に状況を表示する | `NET_UNREACHABLE`, `SERVER_BUSY`, `FILE_LOCKED`, `TIMEOUT` |
| `permanent` | モデルに返す。再試行しない | `NOT_FOUND`, `CORRUPT_FILE`, `INVALID_ARGS`, `UNSUPPORTED_FORMAT`, `BLOCK_NOT_FOUND`, `FORMULA_ERROR` |
| `policy` | モデルに返す。方針を変えるよう促す | `FORBIDDEN_PATH`, `NET_DISABLED`, `DLP_BLOCKED`, `DOMAIN_BLOCKED`, `QUOTA_EXCEEDED` |
| `deferred` | 承認待ち・質問待ちとして保留する。モデルには `approval_id` を返す | `APPROVAL_REQUIRED` |

**エラーコード一覧**（増やすときは本表に追記する）

| code | kind | 意味 | モデルへのメッセージ例 |
| --- | --- | --- | --- |
| `INVALID_ARGS` | permanent | 引数がスキーマや値域に合わない | 「range の形式が不正です。A1:D20 の形式で指定してください」 |
| `UNKNOWN_TOOL` | permanent | 無効なツール | 「web.search は現在使えません」 |
| `NOT_FOUND` | permanent | ファイルやシートがない | 「請求書/2026-09.pdf は存在しません。fs.list で確認してください」 |
| `BLOCK_NOT_FOUND` | permanent | docx/xlsx の ID がない（読み直しが必要） | 「p:57 はありません。docx.read で最新の ID を取得してください」 |
| `STALE_DOCUMENT` | permanent | 読んだ後にファイルが変わった（`expect_hash` が一致しない） | 「ファイルが更新されています。読み直してから編集してください」 |
| `CORRUPT_FILE` | permanent | 壊れていて開けない | 「このファイルは壊れているため読めません。この件は失敗として記録してください」 |
| `UNSUPPORTED_FORMAT` | permanent | 非対応の形式 | 「.doc は convert.to_docx で変換してから扱ってください」 |
| `PASSWORD_REQUIRED` | deferred | パスワード付きPDF（PDF-06）。ask_user に変換する | — |
| `FORMULA_ERROR` | permanent | 再計算後にエラー値がある | 「Sheet1!F12 が #REF! です」 |
| `RESULT_TOO_LARGE` | permanent | continuation を使っても大きすぎる | 「範囲を狭めてください」 |
| `FORBIDDEN_PATH` | policy | 禁止領域 | 「このフォルダは操作できません」 |
| `APPROVAL_REQUIRED` | deferred | 承認待ち | 「承認待ち(ap-12)。承認不要な作業を先に進めてください」 |
| `APPROVAL_REJECTED` | policy | 利用者が却下した | 「利用者が削除を却下しました。別の方法を検討してください」 |
| `NET_DISABLED` | policy | ネットがオフ、または上限を超えた | 「ネットアクセスは許可されていません」 |
| `DLP_BLOCKED` | policy | 機密語を検知した（NET-07） | 「検索語に社外秘の語が含まれます。一般的な言葉に言い換えて検索してください」 |
| `DOMAIN_BLOCKED` | policy | 禁止サイト・社内アドレス | 「このサイトは閲覧できません」 |
| `QUOTA_EXCEEDED` | policy | 検索回数の上限（NET-13） | 「今月の検索上限に達しました。ネットなしで続けてください」 |
| `FILE_LOCKED` | transient | 他のアプリが開いている | （モデルには返さない。最大5分後に別名保存へ切り替え） |
| `NET_UNREACHABLE` | transient | サーバーに接続できない | （返さない） |
| `SERVER_BUSY` | transient | 429/503 | （返さない） |
| `TIMEOUT` | transient | タイムアウト | （返さない） |
| `TOOL_CRASHED` | transient | ツールのプロセスが落ちた（再起動して再試行） | （3回続いたら permanent の `TOOL_FAILED` に変える） |
| `TOOL_FAILED` | permanent | 再試行しても失敗する内部エラー | 「この操作は失敗しました。理由: …」 |

### 2.5 長い結果の分割
- 1回の結果は約6,000トークンまで（`tokens_estimate` は文字数÷1.5 で概算する）。
- 超える場合は `continuation`（不透明な文字列、有効期限は会話が続く間）を返す。続きは同じツールを `{"continuation": "..."}` という引数だけで呼んで取得する。すべての読み取り系ツールの `args` は `continuation` 単独での呼び出しを受け付ける。

### 2.6 パスの扱い
- 引数のパスは、作業フォルダからの相対パスを基本とする。絶対パス・UNC パスも受け付けるが、ポリシー判定の対象になる。
- glob（`請求書/*.pdf`, `**/*.xlsx`）を受け付けるのは、`fs.list` / `fs.search` / `batch.run` の `items` だけ。
- 文字は NFC に正規化する。区切り文字は `/` と `\` のどちらも受け付け、内部では Windows 形式に変換する。

### 2.7 楽観的な排他（expect_hash）
書き込み系の docx / xlsx ツールは、省略可能な `expect_hash` を受け付ける。読み取り系ツールが返した `file_hash` と現在のハッシュが一致しない場合は `STALE_DOCUMENT` を返す。Runtime は、直前に同じファイルを読んでいれば自動で `expect_hash` を付ける。他人による NAS 上の同時編集を検知するための仕組み（R11）。

---

## 3. ツール定義

各表の「効果」は ToolSpec の `effect`、「冪等」は `idempotent`。

### 3.1 fs（ファイル）

| ツール | args | data（戻り値） | 効果 | 冪等 | 優先度 |
| --- | --- | --- | --- | --- | --- |
| `fs.list` | `{ path?: string = ".", glob?: string, depth?: 1..5 = 1, include_hidden?: false }` | `{ entries: { path, type: "file"|"dir", size, mtime }[] }`（500件まで、超えたら continuation） | read | ○ | Must |
| `fs.stat` | `{ path }` | `{ path, type, size, mtime, hash?, locked: boolean }` | read | ○ | Must |
| `fs.read_text` | `{ path, encoding?: "auto", offset_lines?: 0, max_lines?: 400 }` | `{ text_ref }` ＋ untrusted（txt/md/csv/json/log 用） | read | ○ | Must |
| `fs.search` | `{ path?: ".", name_glob?: string, content?: string, max_results?: 50 }` | `{ hits: { path, line?, snippet? }[] }`（docx/xlsx/pdf の本文も office-tools の索引で検索する） | read | ○ | Should |
| `fs.mkdir` | `{ path }` | `{ path }` | write | ○ | Must |
| `fs.copy` | `{ src, dst, overwrite?: false }` | `{ artifact }` | write | ○ | Must |
| `fs.move` | `{ src, dst, overwrite?: false }` | `{ artifact }`（移動元の扱いは削除と同じで、承認が必要） | delete | × | Should |
| `fs.delete` | `{ path }` | `{ artifact }`（実体はごみ箱に移す） | delete | ○ | Must |

### 3.2 docx（Word）

**ブロックID**: `docx.read` は文書を次のブロック列で返す。ID は読み取りのたびに先頭から振り直す（文書内の位置に基づく）ため、編集した後は読み直しが必要（`BLOCK_NOT_FOUND`/`STALE_DOCUMENT` で検知できる）。

```ts
type DocxBlock =
  | { id: "p:12"; kind: "heading"; level: 1..9; text: string; style: string }
  | { id: "p:13"; kind: "paragraph"; text: string; style: string; list?: { level: number; numbered: boolean } }
  | { id: "tbl:3"; kind: "table"; rows: number; cols: number;
      cells: { id: "tbl:3/r0c0"; text: string; merged?: "h"|"v" }[][] }
  | { id: "hdr:1" | "ftr:1"; kind: "header" | "footer"; section: number; text: string }
  | { id: "img:2"; kind: "image"; alt?: string; width_mm: number; height_mm: number }
  | { id: "brk:4"; kind: "page_break" };
```

| ツール | args | data | 効果 | 優先度 |
| --- | --- | --- | --- | --- |
| `docx.read` | `{ path, from_block?: string, max_blocks?: 200, include?: ("headers"|"comments"|"images")[] }` | `{ file_hash, total_blocks, blocks: DocxBlock[] }`。本文は untrusted で返す | read | Must |
| `docx.outline` | `{ path }` | `{ file_hash, headings: { id, level, text }[], tables: number, pages_estimate }` | read | Must |
| `docx.create` | `{ path, template?: string, blocks: NewBlock[], overwrite?: false }` | `{ artifact }` | write | Must |
| `docx.edit` | `{ path, ops: DocxOp[] (1..100), track_changes?: false, author?: "AI", expect_hash? }` | `{ artifact, applied: number, results: { op_index, ok, error? }[] }` | write | Must |
| `docx.diff` | `{ a, b, granularity?: "paragraph"|"char" = "char" }` | `{ changes: { kind: "insert"|"delete"|"modify", a_id?, b_id?, before?, after? }[] }` | read | Should |
| `docx.comments` | `{ path, add?: { block_id, text }[] }` | `{ comments: { id, block_id, author, text }[], artifact? }` | read / write | Could |
| `docx.images` | `{ path, describe?: false }` | `{ images: { id, path_extracted, description? }[] }` | read | Could |

```ts
type NewBlock =
  | { kind: "heading"; level: 1..9; text: string }
  | { kind: "paragraph"; text: string; style?: string; list?: { level: number; numbered: boolean } }
  | { kind: "table"; header?: string[]; rows: string[][]; style?: string }
  | { kind: "page_break" };

type DocxOp =
  | { op: "replace_text"; block_id: string; text: string }               // 段落・セルの文字を置き換える。最初の run の書式を引き継ぐ
  | { op: "replace_in_block"; block_id: string; find: string; replace: string; count?: number } // 部分置換。ほかの run の書式は保つ
  | { op: "insert_after"; block_id: string; blocks: NewBlock[] }
  | { op: "insert_before"; block_id: string; blocks: NewBlock[] }
  | { op: "delete_block"; block_id: string }
  | { op: "table_set_cell"; cell_id: string; text: string }
  | { op: "table_add_row"; table_id: string; after_row?: number; values: string[] }
  | { op: "table_delete_row"; table_id: string; row: number }
  | { op: "set_style"; block_id: string; style: string };
```

- `ops` は**先頭から順に、元の文書の ID を基準に**適用する（途中の挿入で ID がずれないよう、ツール内部で要素の参照を先に解決してから適用する）。
- `track_changes: true` の場合、変更を `w:ins` / `w:del`（作成者は `author`）で書く（DOC-04）。
- 差し込み文字列中の `\n` は段落内の改行（`w:br`）として扱う。段落を分けたい場合は `insert_after` を使う。

### 3.3 xlsx（Excel）／csv

**セル参照**: `Sheet1!B12`、範囲は `Sheet1!A1:F200`。シート名に空白や記号を含む場合は `'送金 明細'!A1` のように囲む。

| ツール | args | data | 効果 | 優先度 |
| --- | --- | --- | --- | --- |
| `xlsx.info` | `{ path }` | `{ file_hash, sheets: { name, used_range, rows, cols, merged: string[], tables: { name, ref }[], has_pivot, has_chart, hidden }[], has_macros }` | read | Must |
| `xlsx.read_range` | `{ path, range, mode?: "values"|"formulas"|"both" = "values", header_row?: number, max_cells?: 2000 }` | `{ file_hash, range, header?: string[], rows: (string|number|boolean|null)[][], formulas?: (string|null)[][], formats?: string[][] }` | read | Must |
| `xlsx.find` | `{ path, sheet?: string, query: string, match?: "exact"|"contains" = "contains", max_results?: 100 }` | `{ hits: { cell, value }[] }` | read | Should |
| `xlsx.write_cells` | `{ path, sheet, cells: { ref: string; value?: Scalar; formula?: string; number_format?: string }[] (1..5000), expect_hash?, recalc?: true }` | `{ artifact, written, recalc?: RecalcResult }` | write | Must |
| `xlsx.write_table` | `{ path, sheet, top_left: string, header?: string[], rows: Scalar[][], expect_hash?, recalc?: true }` | `{ artifact, range, recalc? }` | write | Must |
| `xlsx.insert_rows` / `xlsx.delete_rows` | `{ path, sheet, at: number, count: 1..10000, expect_hash? }` | `{ artifact }` | write | Must |
| `xlsx.insert_cols` / `xlsx.delete_cols` | `{ path, sheet, at: string, count, expect_hash? }` | `{ artifact }` | write | Should |
| `xlsx.add_sheet` / `xlsx.rename_sheet` | `{ path, name, new_name?, position? }` | `{ artifact }` | write | Must |
| `xlsx.create` | `{ path, sheets: SheetSpec[], overwrite?: false }` | `{ artifact, recalc }` | write | Must |
| `xlsx.recalc` | `{ path }` | `RecalcResult` | write | Must |
| `xlsx.chart` | `{ path, sheet, kind: "bar"|"line"|"pie"|"scatter", data_range, title?, anchor }` | `{ artifact }` | write | Should |
| `csv.read` | `{ path, encoding?: "auto", delimiter?: "auto", max_rows?: 2000, offset?: 0 }` | `{ encoding, delimiter, header, rows, total_rows }` | read | Must |
| `csv.write` | `{ path, header?, rows, encoding?: "utf-8-sig"|"cp932" = "utf-8-sig", overwrite?: false }` | `{ artifact }` | write | Must |

```ts
type Scalar = string | number | boolean | null | { date: "2026-10-11" };
interface SheetSpec {
  name: string;
  columns: { header: string; width?: number; number_format?: string }[];
  rows?: Scalar[][];
  formulas?: { ref: string; formula: string }[];   // "=SUM(D2:D11)"
  freeze?: string;                                 // "A2"
  as_table?: { name: string; style?: string };
}
interface RecalcResult { ok: boolean; errors: { cell: string; value: "#REF!"|"#DIV/0!"|"#VALUE!"|"#NAME?"|"#N/A"|"#NUM!"|"#NULL!"; formula: string }[]; }
```

- 書き込み系は既定で最後に `xlsx.recalc` を実行し、エラーがあれば `ok: true` のまま `recalc.errors` を返す（モデルに直させる）。数式を含まないブックでは再計算を省略する。
- `.xlsm` は VBA を保持して保存し、実行はしない。`.xls` は `UNSUPPORTED_FORMAT` を返し、`convert.to_xlsx` を案内する。
- ピボットテーブルは `xlsx.info` の `has_pivot` と、`read_range` によるキャッシュ値の読み取りだけに対応する（XLS-09）。

### 3.4 data（集計・突合）

AIは計算せず、宣言的な spec を書く（XLS-05、設計書 P4）。

| ツール | args | data | 効果 | 優先度 |
| --- | --- | --- | --- | --- |
| `data.transform` | `{ inputs: { name: string; source: SourceRef }[], steps: Step[], output: OutputRef }` | `{ rows_out, preview: Scalar[][] (先頭20行), artifact? }` | read / write | Must |

```ts
type SourceRef =
  | { xlsx: string; range: string; header_row?: number }
  | { csv: string } | { rows: Scalar[][]; header: string[] }
  | { result_of: string };                       // batch.run の結果ID
type Step =
  | { op: "filter"; where: Expr }
  | { op: "select"; columns: string[] } | { op: "rename"; map: Record<string, string> }
  | { op: "derive"; column: string; expr: Expr }
  | { op: "group"; by: string[]; aggs: { column: string; fn: "sum"|"count"|"avg"|"min"|"max"|"first"; as: string }[] }
  | { op: "sort"; by: { column: string; desc?: boolean }[] }
  | { op: "join"; with: string; on: { left: string; right: string }[]; how: "inner"|"left"|"outer" }
  | { op: "reconcile"; with: string; key: string[]; compare: string[]; tolerance?: number } // 突合：差異のある行に _status 列を付ける
  | { op: "dedupe"; by: string[] };
type Expr = string;  // 限定した式言語: 列名, 数値, 文字列, + - * / , == != < <= > >=, and or not, contains(), startswith(), round(x,n), date(), coalesce()
type OutputRef = { xlsx: string; sheet: string; top_left?: string; as_table?: boolean } | { csv: string } | { return_only: true };
```

- `Expr` は専用の構文解析器で評価し、Python の `eval` は使わない。
- 金額は `Decimal` で計算する（浮動小数点の誤差を出さないため）。

### 3.5 pdf／invoice／convert

| ツール | args | data | 効果 | 優先度 |
| --- | --- | --- | --- | --- |
| `pdf.info` | `{ path, password_ref?: string }` | `{ file_hash, pages, encrypted, text_pages: number[], scanned_pages: number[], title?, producer? }` | read | Must |
| `pdf.read_pages` | `{ path, pages: string /* "1-5,8" */, password_ref?, layout?: "reading"|"raw" = "reading" }` | `{ pages: { page, chars, has_text }[] }` ＋ untrusted（ページ単位、ref=`p.3`） | read | Must |
| `pdf.extract_tables` | `{ path, pages, password_ref? }` | `{ tables: { page, index, bbox, header?: string[], rows: string[][] }[] }` | read | Must |
| `pdf.ocr` | `{ path, pages, engine?: "auto"|"ocr"|"vlm" = "auto", password_ref? }` | `{ pages: { page, confidence }[] }` ＋ untrusted（source=ocr） | read | Must |
| `invoice.extract` | `{ path, schema: string /* "invoice.v1" かプロファイル定義のスキーマID */, pages?: string }` | `{ fields: Record<string, { value: Scalar; page: number; bbox?: number[]; confidence: number }>, checks: { name: string; ok: boolean; detail?: string }[], needs_review: boolean }` | read | Must |
| `convert.to_docx` | `{ src, dst? }` | `{ artifact }` | write | Should |
| `convert.to_xlsx` | `{ src, dst? }` | `{ artifact }` | write | Should |
| `convert.to_pdf` | `{ src, dst? }` | `{ artifact }` | write | Should |

- `password_ref`: 利用者が `ask_user` で入力したパスワードを、Runtime がメモリ上に保持して付ける参照 ID。パスワードの実値はモデルに渡さず、Run Store にも保存しない（PDF-06）。
- `invoice.extract` の処理: 本文を読み、テキストのないページは OCR にかける → LLM が `guided_json` 付きでスキーマどおりに抽出する（サーバーへの推論呼び出しを1回行う）→ 検算（`checks`。例: `sum(lines.amount) == total`、日付の妥当性、通貨コード）。検算に失敗した、または確信度が0.8未満の場合は `needs_review: true` にする。
- スキーマ `invoice.v1` の項目: `issue_date, due_date, issuer_name, issuer_reg_no, bill_to, vessel_name, voyage_no, currency, subtotal, tax, total, bank: {name, branch, account_type, account_no, swift?}, lines: [{description, qty, unit_price, amount}]`。会社ごとの拡張はプロファイルで定義する。

### 3.6 batch（一括処理ランナー）

| ツール | args | data | 効果 | 優先度 |
| --- | --- | --- | --- | --- |
| `batch.run` | `BatchSpec` | `{ batch_id, total, succeeded, failed: { item, code, reason }[], result_id, output_artifact? }` | write（出力がある場合） | Must |
| `batch.status` | `{ batch_id }` | 進捗（再開時の確認用） | none | Must |

```ts
interface BatchSpec {
  items: string;                        // glob（作業フォルダ内） or "list:" + 改行区切りのパス
  pipeline:
    | { tool: "invoice.extract"; args: { schema: string } }
    | { tool: "pdf.extract_tables" | "pdf.read_pages" | "convert.to_pdf" | "convert.to_docx" | "convert.to_xlsx"; args?: object };
  output?: { xlsx: string; sheet: string; columns: { header: string; from: string /* JSON Pointer。例 "/fields/total/value" */ }[]; append?: boolean }
         | { csv: string; columns: { header: string; from: string }[] };
  on_error?: "skip";                    // 固定。RUN-06
}
```

- 1件ごとに `batch_items` テーブルへ完了記録を書く（データモデル仕様 §2）。再開したら未完了の件から続ける。
- 出力ファイルへの追記は、件ごとではなく**全件の処理が終わってから一度に**行う（意図記録は1回）。途中経過は Run Store に保持する。
- 1件ごとの進捗は `RuntimeEvent: batch_progress` で画面に流す。

### 3.7 memory（固定メモリ）

| ツール | args | data | 効果 | 優先度 |
| --- | --- | --- | --- | --- |
| `memory.note` | `{ key: string (≤40), value: string (≤500) }` | `{ keys: string[] }` | none | Must |
| `memory.forget` | `{ key }` | `{ keys }` | none | Must |

固定メモリ（設計書 §5.7）に「確定した事実」を記録する。文脈を圧縮しても消えない。1会話あたり50件・合計8,000字まで。

### 3.8 rag／web

| ツール | args | data | 効果 | 優先度 |
| --- | --- | --- | --- | --- |
| `rag.search` | `{ query: string (≤200), top_k?: 1..10 = 5, filters?: { path_prefix?: string; doc_type?: string; updated_after?: string } }` | `{ hits: { ref, path, location, score }[] }` ＋ untrusted（source=rag、抜粋） | read | Should |
| `web.search` | `{ query: string (≤120), max_results?: 1..10 = 5, site?: string }` | `{ results: { rank, title, url, snippet }[] }`（title/snippet は untrusted） | net | Must（NET） |
| `web.fetch` | `{ url: string, max_chars?: ≤8000 }` | `{ url, final_url, title, content_type, chars, truncated }` ＋ untrusted（source=web） | net | Must（NET） |

- `web.*` は、ネット設定が「検索のみ」なら `web.search` だけ、「検索＋ページ閲覧」なら両方を有効にする。オフなら文法から外す（§1.3）。
- `web.*` の結果を一度でも受け取ったら、Runtime は会話に汚染フラグを立てる（設計書 §5.5）。

---

## 4. 承認（Approval）

```ts
interface ApprovalRequest {
  approval_id: string;            // "ap-12"
  run_id: string; step_id: string;
  tool: ToolName; args: object;   // 実際に実行する引数（正規化済み）
  reason: "outside_workspace" | "delete" | "move" | "plugin_write" | "tainted_outside";
  paths: { path: string; real_path: string; inside_workspace: boolean }[];
  tainted: boolean;               // true なら一括承認の対象外
  created_at: string;
}
type ApprovalDecision = { approval_id: string; decision: "approve" | "reject"; remember?: "this_run" };
```

- 承認されたら、Runtime はそのツールを実行し、結果を `approval_result` イベントとして次の推論の前に差し込む。
- `remember: "this_run"` を指定すると、同じ会話内で同じフォルダへの同じ種類の操作を自動で承認する。ただし汚染中・削除は対象外。

---

## 5. Main ⇄ Runtime メッセージ（MessagePort）

すべてのメッセージは `{ v: 1, id: string, type, payload }` の形とする。Runtime から送るイベントには、会話ごとの連番 `seq` を付ける。

### 5.1 RuntimeCommand（Main → Runtime）

| type | payload | 説明 |
| --- | --- | --- |
| `run.start` | `{ conversation_id, text, attachments: string[], workspace: string, model: "auto"|"fast"|"accurate" }` | 新しい依頼。**その会話で実行中の run がある場合は新しい run を作らず、追加指示として扱う**。次の推論の前に `user` メッセージとして差し込み、固定メモリの「依頼原文」に追記する |
| `run.pause` / `run.resume` / `run.stop` | `{ run_id }` | RUN-11 / APP-07 |
| `approval.decide` | `ApprovalDecision` | 承認・却下 |
| `question.answer` | `{ question_id, answer: string, secret?: boolean }` | ask_user への回答（`secret` が true ならパスワードとして扱う） |
| `undo.run` | `{ run_id, paths?: string[] }` | 元に戻す（AGT-03） |
| `events.since` | `{ conversation_id, seq }` | 画面を開き直したときの取り直し |
| `settings.update` | `{ net_level?, model_default?, notify_long_run_minutes? }` | 設定の変更 |
| `settings.get` | `{}` | 作業の設定を返す（`{ net_level, model_default, notify_long_run_minutes }`） |
| `config.get` | `{}` | サーバーの設定のうち画面が使うもの（`{ org, models, escalation_target, net_level_cap, features, macros }`。macros は定型作業 APP-09） |
| `artifact.diff` | `{ run_id, path }` | 差分プレビュー（AGT-03）。変更前はバックアップ、変更後は現在のファイル。戻り値は `ArtifactDiff`（`kind: docx|xlsx|text|binary`、`changes[]` は段落・セル・行の単位）。§5.3 の `main:diff.get` はこれに置き換えた |
| `conversation.list` / `.get` / `.search` / `.delete` | … | 履歴（APP-06） |

### 5.2 RuntimeEvent（Runtime → Main）

| type | payload |
| --- | --- |
| `run.state` | `{ run_id, state: "planning"|"inferring"|"tool_running"|"waiting_approval"|"waiting_question"|"compacting"|"replanning"|"stalled"|"paused"|"reconnecting"|"done"|"stopped", detail? }`。`waiting_*` になるのは、モデルが `final` を返したのに保留が残っている場合だけ。保留があってもほかの手順を進めている間は `inferring` / `tool_running` のままで、保留の件数は `detail.pending` で示す |
| `token` | `{ run_id, step_id, delta: string }`（final の summary と thought の逐次表示） |
| `step` | `{ run_id, step_id, kind: "tool"|"plan"|"final"|"ask"|"system", tool?, args_preview, result_preview?, status: "running"|"ok"|"error"|"deferred", started_at, ended_at? }` |
| `plan` | `{ run_id, plan }` |
| `approval.request` | `ApprovalRequest` |
| `question` | `{ question_id, run_id, question, choices?, secret_hint?: boolean }` |
| `artifact` | `{ run_id, artifact: Artifact, diff_ref?: string }` |
| `batch_progress` | `{ batch_id, done, total, failed }` |
| `queue` | `{ position, eta_seconds }`（APP-10） |
| `retry` | `{ attempt, next_in_ms, cause: ErrorCode }`（RUN-03） |
| `net.activity` | `{ kind: "search"|"fetch", query?, url?, blocked?: ErrorCode }`（NET-04） |
| `notify` | `{ kind: "done"|"approval"|"stalled"|"long_run", title, body }` |
| `server.status` | `{ state: "online"|"offline"|"degraded"|"failover"|"primary", endpoint, url? }`（failover: 予備機に切り替えた / primary: 本番機に戻った） |

### 5.3 Renderer ⇄ Main（preload API、`main:*`）

Runtime を経由しない操作は、preload が `window.app` として公開する型付き API で行う（`contextBridge`）。詳しい使い方は画面仕様書 `local_llm_ui_spec.md` に記載した。

| API | 説明 |
| --- | --- |
| `main:auth.login({ login, password, remember })` / `auth.logout()` / `auth.changePassword()` | Gateway の `/v1/auth/*` を呼び、トークンを `safeStorage` に保存する |
| `main:runtime.send(cmd)` / `runtime.onEvent(cb)` | RuntimeCommand の送信と RuntimeEvent の購読（§5.1, §5.2 の中継） |
| （差分の取得） | Runtime コマンド `artifact.diff` で取得する（§5.1）。当初の `main:diff.get` は実装時に廃止 |
| `main:dialog.pickFolder()` / `shell.openPath(path)` | フォルダの選択、既定アプリで開く（作業フォルダ内に限る） |
| `main:settings.get()` / `settings.set(patch)` | 端末の設定（サーバーURL・予備サーバーURL・自動起動など） |
| `main:update.check()` / `update.install()` | 自動更新（ADM-05） |
| `main:app.quit()` | 終了。実行中の run がある場合は、次回の起動時に再開する |

---

## 6. バージョン管理と互換性

- `packages/protocol/schemas/*.schema.json` の `$id` に版を入れる（例 `https://local-llm-agent/schemas/1.0/action.json`）。
- Runtime は Gateway の `/v1/config` から、サーバーが対応するプロトコル版を受け取る。メジャー番号が合わない場合は、画面に「アプリの更新が必要です」と出して推論を始めない。
- ツールの追加はマイナー版の更新とする。既存の引数の意味を変えるのはメジャー版の更新とする。
