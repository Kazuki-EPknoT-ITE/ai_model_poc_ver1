/* 生成物（packages/protocol/scripts/gen.mjs）。編集しないこと。 */
/* eslint-disable */
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "Action".
 */
export type Action = (ToolCall | PlanUpdate | AskUser | Final)
/**
 * 作業フォルダからの相対パス、または承認対象になる絶対パス・UNCパス
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "Path".
 */
export type Path = string
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ApprovalReason".
 */
export type ApprovalReason = ("outside_workspace" | "delete" | "move" | "plugin_write" | "tainted_outside")
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FileHash".
 */
export type FileHash = string
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "SheetName".
 */
export type SheetName = string
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "BlockId".
 */
export type BlockId = string
/**
 * Sheet1!B12 / '送金 明細'!A1
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "CellRef".
 */
export type CellRef = string
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "Scalar".
 */
export type Scalar = (string | number | boolean | null | DateValue)
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "NumberFormat".
 */
export type NumberFormat = string
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ColumnName".
 */
export type ColumnName = string
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "Continuation".
 */
export type Continuation = string
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "SourceRef".
 */
export type SourceRef = ({
xlsx: Path
range: RangeRef
header_row?: number
} | {
csv: Path
} | {
/**
 * @maxItems 200
 */
header: ColumnName[]
/**
 * @maxItems 10000
 */
rows: Scalar[][]
} | {
result_of: string
})
/**
 * Sheet1!A1:F200
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "RangeRef".
 */
export type RangeRef = string
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "Step".
 */
export type Step = ({
op: "filter"
where: Expr
} | {
op: "select"
/**
 * @minItems 1
 * @maxItems 200
 */
columns: [ColumnName, ...(ColumnName)[]]
} | {
op: "rename"
map: {
[k: string]: ColumnName
}
} | {
op: "derive"
column: ColumnName
expr: Expr
} | {
op: "group"
/**
 * @maxItems 20
 */
by: []|[ColumnName]|[ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]
/**
 * @minItems 1
 * @maxItems 50
 */
aggs: [{
column: ColumnName
fn: ("sum" | "count" | "avg" | "min" | "max" | "first")
as: ColumnName
}, ...({
column: ColumnName
fn: ("sum" | "count" | "avg" | "min" | "max" | "first")
as: ColumnName
})[]]
} | {
op: "sort"
/**
 * @minItems 1
 * @maxItems 10
 */
by: [{
column: ColumnName
desc?: boolean
}]|[{
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}]|[{
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}]|[{
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}]|[{
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}]|[{
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}]|[{
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}]|[{
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}]|[{
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}]|[{
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}, {
column: ColumnName
desc?: boolean
}]
} | {
op: "join"
with: string
/**
 * @minItems 1
 * @maxItems 10
 */
on: [{
left: ColumnName
right: ColumnName
}]|[{
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}]|[{
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}]|[{
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}]|[{
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}]|[{
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}]|[{
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}]|[{
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}]|[{
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}]|[{
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}, {
left: ColumnName
right: ColumnName
}]
how: ("inner" | "left" | "outer")
} | {
op: "reconcile"
with: string
/**
 * @minItems 1
 * @maxItems 10
 */
key: [ColumnName]|[ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]
/**
 * @minItems 1
 * @maxItems 50
 */
compare: [ColumnName, ...(ColumnName)[]]
tolerance?: number
} | {
op: "dedupe"
/**
 * @minItems 1
 * @maxItems 20
 */
by: [ColumnName]|[ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]|[ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName, ColumnName]
})
/**
 * 限定式（列名・数値・文字列・算術・比較・and/or/not・contains/startswith/round/date/coalesce）
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "Expr".
 */
export type Expr = string
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "OutputRef".
 */
export type OutputRef = ({
xlsx: Path
sheet: SheetName
top_left?: string
as_table?: boolean
} | {
csv: Path
} | {
return_only: true
})
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "NewBlock".
 */
export type NewBlock = ({
kind: "heading"
level: number
text: string
} | {
kind: "paragraph"
text: string
style?: string
list?: {
level: number
numbered: boolean
}
} | {
kind: "table"
/**
 * @maxItems 50
 */
header?: string[]
/**
 * @maxItems 2000
 */
rows: string[][]
style?: string
} | {
kind: "page_break"
})
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DocxOp".
 */
export type DocxOp = ({
op: "replace_text"
block_id: BlockId
text: string
} | {
op: "replace_in_block"
block_id: BlockId
find: string
replace: string
count?: number
} | {
op: ("insert_after" | "insert_before")
block_id: BlockId
/**
 * @minItems 1
 * @maxItems 100
 */
blocks: [NewBlock, ...(NewBlock)[]]
} | {
op: "delete_block"
block_id: BlockId
} | {
op: "table_set_cell"
cell_id: BlockId
text: string
} | {
op: "table_add_row"
table_id: BlockId
after_row?: number
/**
 * @maxItems 50
 */
values: string[]
} | {
op: "table_delete_row"
table_id: BlockId
row: number
} | {
op: "set_style"
block_id: BlockId
style: string
})
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "Effect".
 */
export type Effect = ("read" | "write" | "delete" | "net" | "none")
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ErrorCode".
 */
export type ErrorCode = ("INVALID_ARGS" | "UNKNOWN_TOOL" | "NOT_FOUND" | "BLOCK_NOT_FOUND" | "STALE_DOCUMENT" | "CORRUPT_FILE" | "UNSUPPORTED_FORMAT" | "PASSWORD_REQUIRED" | "FORMULA_ERROR" | "RESULT_TOO_LARGE" | "FORBIDDEN_PATH" | "APPROVAL_REQUIRED" | "APPROVAL_REJECTED" | "NET_DISABLED" | "DLP_BLOCKED" | "DOMAIN_BLOCKED" | "QUOTA_EXCEEDED" | "FILE_LOCKED" | "NET_UNREACHABLE" | "SERVER_BUSY" | "TIMEOUT" | "TOOL_CRASHED" | "TOOL_FAILED")
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ErrorKind".
 */
export type ErrorKind = ("transient" | "permanent" | "policy" | "deferred")
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "Glob".
 */
export type Glob = string
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "PageSpec".
 */
export type PageSpec = string
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ModelPref".
 */
export type ModelPref = ("auto" | "fast" | "accurate")
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "NetLevel".
 */
export type NetLevel = ("off" | "search" | "browse")
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "PasswordRef".
 */
export type PasswordRef = string
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "Priority".
 */
export type Priority = ("Must" | "Should" | "Could")
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "RunState".
 */
export type RunState = ("planning" | "inferring" | "tool_running" | "waiting_approval" | "waiting_question" | "compacting" | "replanning" | "stalled" | "paused" | "reconnecting" | "done" | "stopped")
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ShortText".
 */
export type ShortText = string

/**
 * local-llm-agent protocol v1.0（生成物。編集しないこと）
 */
export interface LocalLlmAgentProtocol {
[k: string]: unknown | undefined
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ToolCall".
 */
export interface ToolCall {
type: "tool_call"
thought?: string
tool: string
args: {
[k: string]: unknown | undefined
}
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "PlanUpdate".
 */
export interface PlanUpdate {
type: "plan_update"
/**
 * @minItems 1
 * @maxItems 30
 */
plan: [PlanItem, ...(PlanItem)[]]
note?: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "PlanItem".
 */
export interface PlanItem {
id: string
title: string
status: ("todo" | "doing" | "done" | "skipped")
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "AskUser".
 */
export interface AskUser {
type: "ask_user"
question: string
/**
 * @minItems 2
 * @maxItems 5
 */
choices?: [string, string]|[string, string, string]|[string, string, string, string]|[string, string, string, string, string]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "Final".
 */
export interface Final {
type: "final"
summary: string
/**
 * @maxItems 200
 */
outputs: {
path: Path
description: string
}[]
/**
 * @maxItems 500
 */
failures: {
item: string
reason: string
}[]
/**
 * @maxItems 200
 */
needs_review: {
path: Path
location?: string
reason: string
}[]
/**
 * @maxItems 50
 */
citations?: Citation[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "Citation".
 */
export interface Citation {
kind: ("file" | "web" | "rag")
ref: string
location?: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ApprovalDecision".
 */
export interface ApprovalDecision {
approval_id: string
decision: ("approve" | "reject")
remember?: "this_run"
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ApprovalRequest".
 */
export interface ApprovalRequest {
approval_id: string
run_id: string
step_id: string
tool: string
args: {
[k: string]: unknown | undefined
}
reason: ApprovalReason
paths: {
path: string
real_path: string
inside_workspace: boolean
}[]
tainted: boolean
created_at: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "Artifact".
 */
export interface Artifact {
path: Path
op: ("create" | "overwrite" | "delete" | "move" | "copy")
pre_hash?: FileHash
post_hash?: FileHash
backup_path?: string
renamed_from?: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ArtifactEvent".
 */
export interface ArtifactEvent {
run_id: string
artifact: Artifact
diff_ref?: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "BatchRunArgs".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "BatchSpec".
 */
export interface BatchSpec {
items: string
pipeline: ({
tool: "invoice.extract"
args: {
schema: string
}
} | {
tool: ("pdf.extract_tables" | "pdf.read_pages" | "convert.to_pdf" | "convert.to_docx" | "convert.to_xlsx")
args?: {
[k: string]: unknown | undefined
}
})
output?: ({
xlsx: Path
sheet: SheetName
/**
 * @minItems 1
 * @maxItems 100
 */
columns: [OutputColumn, ...(OutputColumn)[]]
append?: boolean
} | {
csv: Path
/**
 * @minItems 1
 * @maxItems 100
 */
columns: [OutputColumn, ...(OutputColumn)[]]
})
on_error?: "skip"
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "OutputColumn".
 */
export interface OutputColumn {
header: string
from: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "BatchRunResult".
 */
export interface BatchRunResult {
batch_id: string
total: number
succeeded: number
failed: {
item: string
code: string
reason: string
}[]
result_id: string
output_artifact?: Artifact
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "BatchStatusArgs".
 */
export interface BatchStatusArgs {
batch_id: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "BatchStatusResult".
 */
export interface BatchStatusResult {
done: number
total: number
failed?: number
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "CellWrite".
 */
export interface CellWrite {
ref: CellRef
value?: Scalar
formula?: string
number_format?: NumberFormat
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DateValue".
 */
export interface DateValue {
date: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ConvertArgs".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ConvertToDocxArgs".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ConvertToPdfArgs".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ConvertToXlsxArgs".
 */
export interface ConvertArgs {
src: Path
dst?: Path
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ConvertToDocxResult".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ConvertToPdfResult".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ConvertToXlsxResult".
 */
export interface ConvertToDocxResult {
artifact: Artifact
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "CsvReadArgs".
 */
export interface CsvReadArgs {
path: Path
encoding?: ("auto" | "utf-8" | "utf-8-sig" | "cp932")
delimiter?: ("auto" | "," | "\t" | ";" | "|")
max_rows?: number
offset?: number
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "CsvReadResult".
 */
export interface CsvReadResult {
encoding: string
delimiter: string
header?: string[]
rows: unknown[][]
total_rows: number
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "CsvWriteArgs".
 */
export interface CsvWriteArgs {
path: Path
/**
 * @maxItems 200
 */
header?: string[]
/**
 * @maxItems 100000
 */
rows: Scalar[][]
encoding?: ("utf-8-sig" | "cp932")
overwrite?: boolean
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "CsvWriteResult".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxAddSheetResult".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxChartResult".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxDeleteColsResult".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxDeleteRowsResult".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxInsertColsResult".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxInsertRowsResult".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxRenameSheetResult".
 */
export interface CsvWriteResult {
artifact: Artifact
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DataTransformArgs".
 */
export interface DataTransformArgs {
/**
 * @minItems 1
 * @maxItems 10
 */
inputs: [{
name: string
source: SourceRef
}]|[{
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}]|[{
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}]|[{
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}]|[{
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}]|[{
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}]|[{
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}]|[{
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}]|[{
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}]|[{
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}, {
name: string
source: SourceRef
}]
/**
 * @maxItems 50
 */
steps: Step[]
output: OutputRef
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DataTransformResult".
 */
export interface DataTransformResult {
rows_out: number
preview: unknown[][]
artifact?: Artifact
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DocxCommentsArgs".
 */
export interface DocxCommentsArgs {
path: Path
/**
 * @maxItems 50
 */
add?: {
block_id: BlockId
text: string
}[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DocxCommentsResult".
 */
export interface DocxCommentsResult {
comments: {
[k: string]: unknown | undefined
}[]
artifact?: Artifact
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DocxCreateArgs".
 */
export interface DocxCreateArgs {
path: Path
template?: Path
/**
 * @minItems 1
 * @maxItems 2000
 */
blocks: [NewBlock, ...(NewBlock)[]]
overwrite?: boolean
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DocxCreateResult".
 */
export interface DocxCreateResult {
artifact: Artifact
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DocxDiffArgs".
 */
export interface DocxDiffArgs {
a: Path
b: Path
granularity?: ("paragraph" | "char")
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DocxDiffResult".
 */
export interface DocxDiffResult {
changes: {
[k: string]: unknown | undefined
}[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DocxEditArgs".
 */
export interface DocxEditArgs {
path: Path
/**
 * @minItems 1
 * @maxItems 100
 */
ops: [DocxOp, ...(DocxOp)[]]
track_changes?: boolean
author?: string
expect_hash?: FileHash
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DocxEditResult".
 */
export interface DocxEditResult {
artifact: Artifact
applied: number
results: {
[k: string]: unknown | undefined
}[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DocxImagesArgs".
 */
export interface DocxImagesArgs {
path: Path
describe?: boolean
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DocxImagesResult".
 */
export interface DocxImagesResult {
images: {
[k: string]: unknown | undefined
}[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DocxOutlineArgs".
 */
export interface DocxOutlineArgs {
path: Path
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DocxOutlineResult".
 */
export interface DocxOutlineResult {
file_hash: FileHash
headings: {
[k: string]: unknown | undefined
}[]
tables?: number
pages_estimate?: number
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DocxReadArgs".
 */
export interface DocxReadArgs {
path: Path
from_block?: BlockId
max_blocks?: number
/**
 * @maxItems 3
 */
include?: []|[("headers" | "comments" | "images")]|[("headers" | "comments" | "images"), ("headers" | "comments" | "images")]|[("headers" | "comments" | "images"), ("headers" | "comments" | "images"), ("headers" | "comments" | "images")]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "DocxReadResult".
 */
export interface DocxReadResult {
file_hash: FileHash
total_blocks: number
blocks: {
[k: string]: unknown | undefined
}[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "EventsSincePayload".
 */
export interface EventsSincePayload {
conversation_id: string
seq: number
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsCopyArgs".
 */
export interface FsCopyArgs {
src: Path
dst: Path
overwrite?: boolean
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsCopyResult".
 */
export interface FsCopyResult {
artifact: Artifact
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsDeleteArgs".
 */
export interface FsDeleteArgs {
path: Path
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsDeleteResult".
 */
export interface FsDeleteResult {
artifact: Artifact
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsListArgs".
 */
export interface FsListArgs {
path?: Path
glob?: Glob
depth?: number
include_hidden?: boolean
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsListResult".
 */
export interface FsListResult {
entries: {
path: string
type: ("file" | "dir")
size?: number
mtime?: string
}[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsMkdirArgs".
 */
export interface FsMkdirArgs {
path: Path
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsMkdirResult".
 */
export interface FsMkdirResult {
path: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsMoveArgs".
 */
export interface FsMoveArgs {
src: Path
dst: Path
overwrite?: boolean
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsMoveResult".
 */
export interface FsMoveResult {
artifact: Artifact
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsReadTextArgs".
 */
export interface FsReadTextArgs {
path: Path
encoding?: ("auto" | "utf-8" | "cp932")
offset_lines?: number
max_lines?: number
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsReadTextResult".
 */
export interface FsReadTextResult {
text_ref: string
encoding?: string
total_lines?: number
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsSearchArgs".
 */
export interface FsSearchArgs {
path?: Path
name_glob?: Glob
content?: string
max_results?: number
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsSearchResult".
 */
export interface FsSearchResult {
hits: {
path: string
line?: number
snippet_ref?: string
}[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsStatArgs".
 */
export interface FsStatArgs {
path: Path
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "FsStatResult".
 */
export interface FsStatResult {
path: string
type: ("file" | "dir")
size?: number
mtime?: string
hash?: FileHash
locked: boolean
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "InvoiceExtractArgs".
 */
export interface InvoiceExtractArgs {
path: Path
schema: string
pages?: PageSpec
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "InvoiceExtractResult".
 */
export interface InvoiceExtractResult {
fields: {
[k: string]: unknown | undefined
}
checks: {
name: string
ok: boolean
detail?: string
}[]
needs_review: boolean
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "MemoryForgetArgs".
 */
export interface MemoryForgetArgs {
key: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "MemoryForgetResult".
 */
export interface MemoryForgetResult {
keys: string[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "MemoryNoteArgs".
 */
export interface MemoryNoteArgs {
key: string
value: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "MemoryNoteResult".
 */
export interface MemoryNoteResult {
keys: string[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "NotifyEvent".
 */
export interface NotifyEvent {
kind: ("done" | "approval" | "stalled" | "long_run")
title: string
body: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "PdfExtractTablesArgs".
 */
export interface PdfExtractTablesArgs {
path: Path
pages: PageSpec
password_ref?: PasswordRef
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "PdfExtractTablesResult".
 */
export interface PdfExtractTablesResult {
tables: {
[k: string]: unknown | undefined
}[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "PdfInfoArgs".
 */
export interface PdfInfoArgs {
path: Path
password_ref?: PasswordRef
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "PdfInfoResult".
 */
export interface PdfInfoResult {
file_hash: FileHash
pages: number
encrypted: boolean
text_pages: number[]
scanned_pages: number[]
title?: string
producer?: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "PdfOcrArgs".
 */
export interface PdfOcrArgs {
path: Path
pages: PageSpec
engine?: ("auto" | "ocr" | "vlm")
password_ref?: PasswordRef
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "PdfOcrResult".
 */
export interface PdfOcrResult {
pages: {
[k: string]: unknown | undefined
}[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "PdfReadPagesArgs".
 */
export interface PdfReadPagesArgs {
path: Path
pages: PageSpec
password_ref?: PasswordRef
layout?: ("reading" | "raw")
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "PdfReadPagesResult".
 */
export interface PdfReadPagesResult {
pages: {
[k: string]: unknown | undefined
}[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "QuestionAnswerPayload".
 */
export interface QuestionAnswerPayload {
question_id: string
answer: string
secret?: boolean
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "RagSearchArgs".
 */
export interface RagSearchArgs {
query: string
top_k?: number
filters?: {
path_prefix?: string
doc_type?: string
updated_after?: string
}
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "RagSearchResult".
 */
export interface RagSearchResult {
hits: {
ref: string
path: string
location?: string
score?: number
}[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "RecalcResult".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxRecalcResult".
 */
export interface RecalcResult {
ok: boolean
errors: {
cell: string
value: ("#REF!" | "#DIV/0!" | "#VALUE!" | "#NAME?" | "#N/A" | "#NUM!" | "#NULL!")
formula?: string
}[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "RunRefPayload".
 */
export interface RunRefPayload {
run_id: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "RunStartPayload".
 */
export interface RunStartPayload {
conversation_id: string
text: string
attachments: string[]
workspace: string
model: ModelPref
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "RunStateEvent".
 */
export interface RunStateEvent {
run_id: string
state: RunState
detail?: {
[k: string]: unknown | undefined
}
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "RuntimeCommand".
 */
export interface RuntimeCommand {
v: 1
id: string
type: ("run.start" | "run.pause" | "run.resume" | "run.stop" | "approval.decide" | "question.answer" | "undo.run" | "events.since" | "settings.update" | "conversation.list" | "conversation.get" | "conversation.search" | "conversation.delete")
payload: {
[k: string]: unknown | undefined
}
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "RuntimeEvent".
 */
export interface RuntimeEvent {
v: 1
id: string
seq: number
conversation_id?: string
type: ("run.state" | "token" | "step" | "plan" | "approval.request" | "question" | "artifact" | "batch_progress" | "queue" | "retry" | "net.activity" | "notify" | "server.status")
payload: {
[k: string]: unknown | undefined
}
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "SettingsPayload".
 */
export interface SettingsPayload {
net_level?: NetLevel
model_default?: ModelPref
notify_long_run_minutes?: number
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "SheetSpec".
 */
export interface SheetSpec {
name: SheetName
/**
 * @minItems 1
 * @maxItems 200
 */
columns: [{
header: string
width?: number
number_format?: NumberFormat
}, ...({
header: string
width?: number
number_format?: NumberFormat
})[]]
/**
 * @maxItems 100000
 */
rows?: Scalar[][]
/**
 * @maxItems 5000
 */
formulas?: {
ref: string
formula: string
}[]
freeze?: string
as_table?: {
name: string
style?: string
}
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "StepEvent".
 */
export interface StepEvent {
run_id: string
step_id: string
kind: ("tool" | "plan" | "final" | "ask" | "system")
tool?: string
args_preview?: string
result_preview?: string
status: ("running" | "ok" | "error" | "deferred")
started_at: string
ended_at?: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ToolError".
 */
export interface ToolError {
code: ErrorCode
kind: ErrorKind
message: string
retry_after_ms?: number
detail?: {
[k: string]: unknown | undefined
}
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ToolResult".
 */
export interface ToolResult {
ok: boolean
data?: {
[k: string]: unknown | undefined
}
error?: ToolError
untrusted?: UntrustedBlock[]
continuation?: Continuation
artifacts?: Artifact[]
warnings?: string[]
tokens_estimate: number
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "UntrustedBlock".
 */
export interface UntrustedBlock {
source: ("file" | "web" | "rag" | "ocr")
ref: string
text: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "ToolSpec".
 */
export interface ToolSpec {
name: string
description: string
args_schema: {
[k: string]: unknown | undefined
}
result_schema: {
[k: string]: unknown | undefined
}
effect: Effect
path_args: string[]
idempotent: boolean
untrusted_output: boolean
timeout_ms: number
priority: Priority
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "UndoPayload".
 */
export interface UndoPayload {
run_id: string
paths?: string[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "WebFetchArgs".
 */
export interface WebFetchArgs {
url: string
max_chars?: number
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "WebFetchResult".
 */
export interface WebFetchResult {
url: string
final_url: string
title_ref?: string
content_type?: string
chars: number
truncated: boolean
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "WebSearchArgs".
 */
export interface WebSearchArgs {
query: string
max_results?: number
site?: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "WebSearchResult".
 */
export interface WebSearchResult {
results: {
rank: number
url: string
title_ref?: string
snippet_ref?: string
}[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxAddSheetArgs".
 */
export interface XlsxAddSheetArgs {
path: Path
name: SheetName
position?: number
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxChartArgs".
 */
export interface XlsxChartArgs {
path: Path
sheet: SheetName
kind: ("bar" | "line" | "pie" | "scatter")
data_range: RangeRef
title?: string
anchor: string
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxCreateArgs".
 */
export interface XlsxCreateArgs {
path: Path
/**
 * @minItems 1
 * @maxItems 50
 */
sheets: [SheetSpec, ...(SheetSpec)[]]
overwrite?: boolean
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxCreateResult".
 */
export interface XlsxCreateResult {
artifact: Artifact
recalc?: RecalcResult
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxDeleteColsArgs".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxInsertColsArgs".
 */
export interface XlsxDeleteColsArgs {
path: Path
sheet: SheetName
at: string
count: number
expect_hash?: FileHash
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxDeleteRowsArgs".
 * 
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxInsertRowsArgs".
 */
export interface XlsxDeleteRowsArgs {
path: Path
sheet: SheetName
at: number
count: number
expect_hash?: FileHash
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxFindArgs".
 */
export interface XlsxFindArgs {
path: Path
sheet?: SheetName
query: string
match?: ("exact" | "contains")
max_results?: number
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxFindResult".
 */
export interface XlsxFindResult {
hits: {
[k: string]: unknown | undefined
}[]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxInfoArgs".
 */
export interface XlsxInfoArgs {
path: Path
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxInfoResult".
 */
export interface XlsxInfoResult {
file_hash: FileHash
sheets: {
[k: string]: unknown | undefined
}[]
has_macros: boolean
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxReadRangeArgs".
 */
export interface XlsxReadRangeArgs {
path: Path
range: RangeRef
mode?: ("values" | "formulas" | "both")
header_row?: number
max_cells?: number
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxReadRangeResult".
 */
export interface XlsxReadRangeResult {
file_hash: FileHash
range: string
header?: string[]
rows: unknown[][]
formulas?: unknown[][]
formats?: unknown[][]
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxRecalcArgs".
 */
export interface XlsxRecalcArgs {
path: Path
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxRenameSheetArgs".
 */
export interface XlsxRenameSheetArgs {
path: Path
name: SheetName
new_name: SheetName
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxWriteCellsArgs".
 */
export interface XlsxWriteCellsArgs {
path: Path
sheet: SheetName
/**
 * @minItems 1
 * @maxItems 5000
 */
cells: [CellWrite, ...(CellWrite)[]]
expect_hash?: FileHash
recalc?: boolean
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxWriteCellsResult".
 */
export interface XlsxWriteCellsResult {
artifact: Artifact
written: number
recalc?: RecalcResult
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxWriteTableArgs".
 */
export interface XlsxWriteTableArgs {
path: Path
sheet: SheetName
top_left: string
/**
 * @maxItems 200
 */
header?: string[]
/**
 * @maxItems 100000
 */
rows: Scalar[][]
expect_hash?: FileHash
recalc?: boolean
}
/**
 * This interface was referenced by `LocalLlmAgentProtocol`'s JSON-Schema
 * via the `definition` "XlsxWriteTableResult".
 */
export interface XlsxWriteTableResult {
artifact: Artifact
range: string
recalc?: RecalcResult
}
