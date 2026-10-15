// 完了報告（final の summary）を読みやすく表示するための、小さな Markdown の解析。
// HTML としては一切解釈しない（描画は React の要素で行い、文字はすべてそのまま表示される）。

export type Inline = { t: "text" | "b" | "code"; v: string };
export interface ListItem {
  text: string;
  children: ListBlock | null;
}
export interface ListBlock {
  t: "ul" | "ol";
  start: number;
  items: ListItem[];
}
export type Block =
  | { t: "p"; lines: string[] }
  | { t: "h"; level: 1 | 2 | 3; text: string }
  | ListBlock
  | { t: "table"; head: string[]; align: ("left" | "right" | "center" | null)[]; rows: string[][] }
  | { t: "code"; text: string }
  | { t: "hr" };

// 「- 」「1. 」のほか、日本語の文でよく使う「・」（後ろの空白なし）も箇条書きとして扱う
const LIST_RE = /^(\s*)([-*+]\s|\d{1,3}[.)．]\s|・)\s*(.*)$/;
const HEAD_RE = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const SEP_RE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function cells(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const out: string[] = [];
  let cur = "";
  for (let i = 0; i < s.length; i++) {
    const ch = s[i]!;
    if (ch === "\\" && s[i + 1] === "|") {
      cur += "|";
      i++;
    } else if (ch === "|") {
      out.push(cur.trim());
      cur = "";
    } else cur += ch;
  }
  out.push(cur.trim());
  return out;
}

/** 文章をブロック（段落・見出し・箇条書き・表・コード）に分ける */
export function parseMarkdown(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (!line.trim()) {
      i++;
      continue;
    }
    // コードブロック
    if (/^\s*```/.test(line)) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i]!)) body.push(lines[i++]!);
      i++;
      blocks.push({ t: "code", text: body.join("\n") });
      continue;
    }
    const h = HEAD_RE.exec(line);
    if (h) {
      blocks.push({ t: "h", level: Math.min(h[1]!.length, 3) as 1 | 2 | 3, text: h[2]! });
      i++;
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      blocks.push({ t: "hr" });
      i++;
      continue;
    }
    // 表（見出し行の次が区切り行）
    if (line.includes("|") && i + 1 < lines.length && SEP_RE.test(lines[i + 1]!)) {
      const head = cells(line);
      const align = cells(lines[i + 1]!).map((c) => (c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : c.startsWith(":") ? "left" : null));
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i]!.includes("|") && lines[i]!.trim()) rows.push(cells(lines[i++]!));
      blocks.push({ t: "table", head, align, rows });
      continue;
    }
    // 箇条書き・番号付き（1段の入れ子まで）
    if (LIST_RE.test(line)) {
      const top = LIST_RE.exec(line)!;
      const baseIndent = top[1]!.length;
      const ordered = /\d/.test(top[2]!);
      const list: ListBlock = { t: ordered ? "ol" : "ul", start: ordered ? parseInt(top[2]!, 10) : 1, items: [] };
      while (i < lines.length) {
        const m = LIST_RE.exec(lines[i]!);
        if (!m) {
          // 項目の続きの行（字下げされた行）
          const cont = lines[i]!;
          if (cont.trim() && /^\s+/.test(cont) && list.items.length) {
            const last = list.items[list.items.length - 1]!;
            last.text += `\n${cont.trim()}`;
            i++;
            continue;
          }
          break;
        }
        const indent = m[1]!.length;
        const isOrdered = /\d/.test(m[2]!);
        if (indent > baseIndent + 1 && list.items.length) {
          const last = list.items[list.items.length - 1]!;
          if (!last.children) last.children = { t: isOrdered ? "ol" : "ul", start: isOrdered ? parseInt(m[2]!, 10) : 1, items: [] };
          last.children.items.push({ text: m[3]!, children: null });
        } else {
          if (isOrdered !== ordered) break;
          list.items.push({ text: m[3]!, children: null });
        }
        i++;
      }
      blocks.push(list);
      continue;
    }
    // 段落（空行・見出し・箇条書き・表・コードの手前まで。改行はそのまま残す）
    const para: string[] = [];
    while (i < lines.length) {
      const l = lines[i]!;
      if (!l.trim() || HEAD_RE.test(l) || LIST_RE.test(l) || /^\s*```/.test(l)) break;
      if (l.includes("|") && i + 1 < lines.length && SEP_RE.test(lines[i + 1]!)) break;
      para.push(l.trim());
      i++;
    }
    blocks.push({ t: "p", lines: para });
  }
  return blocks;
}

/** 行の中の **太字** と `コード` を分ける（それ以外の記号はそのまま文字として出す） */
export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  const re = /`([^`\n]+)`|\*\*([^*\n]+?)\*\*|__([^_\n]+?)__/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    const at = m.index ?? 0;
    if (at > last) out.push({ t: "text", v: text.slice(last, at) });
    if (m[1] !== undefined) out.push({ t: "code", v: m[1] });
    else out.push({ t: "b", v: (m[2] ?? m[3])! });
    last = at + m[0].length;
  }
  if (last < text.length) out.push({ t: "text", v: text.slice(last) });
  return out;
}
