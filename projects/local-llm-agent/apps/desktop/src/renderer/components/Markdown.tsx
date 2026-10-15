import { Fragment, useMemo } from "react";
import { parseInline, parseMarkdown, type Block, type ListBlock } from "../markdown.js";

/** 行の中の太字・コード。改行は <br> にする（文字はすべて React がエスケープする） */
function Text({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <>
      {lines.map((line, li) => (
        <Fragment key={li}>
          {li > 0 && <br />}
          {parseInline(line).map((s, i) => (s.t === "b" ? <strong key={i}>{s.v}</strong> : s.t === "code" ? <code key={i}>{s.v}</code> : <Fragment key={i}>{s.v}</Fragment>))}
        </Fragment>
      ))}
    </>
  );
}

function List({ list }: { list: ListBlock }) {
  const items = list.items.map((it, i) => (
    <li key={i}>
      <Text text={it.text} />
      {it.children && <List list={it.children} />}
    </li>
  ));
  return list.t === "ol" ? <ol start={list.start !== 1 ? list.start : undefined}>{items}</ol> : <ul>{items}</ul>;
}

function BlockView({ b }: { b: Block }) {
  switch (b.t) {
    case "p":
      return (
        <p>
          <Text text={b.lines.join("\n")} />
        </p>
      );
    case "h":
      // 答えの中の見出しは、画面の見出しの階層に入れない（読み上げでは太字の段落として扱う）
      return (
        <p className={`md-h md-h${b.level}`}>
          <strong>
            <Text text={b.text} />
          </strong>
        </p>
      );
    case "ul":
    case "ol":
      return <List list={b} />;
    case "table":
      return (
        <div className="md-table-wrap">
          <table className="md-table">
            <thead>
              <tr>
                {b.head.map((c, i) => (
                  <th key={i} className={b.align[i] ? `al-${b.align[i]}` : undefined}>
                    <Text text={c} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {b.rows.map((r, ri) => (
                <tr key={ri}>
                  {b.head.map((_, ci) => (
                    <td key={ci} className={b.align[ci] ? `al-${b.align[ci]}` : undefined}>
                      <Text text={r[ci] ?? ""} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "code":
      return (
        <pre className="md-code">
          <code>{b.text}</code>
        </pre>
      );
    case "hr":
      return <hr />;
  }
}

/** 完了報告の本文。軽い Markdown（段落・改行・箇条書き・太字・コード・表）だけを解釈する */
export function Markdown({ text }: { text: string }) {
  const blocks = useMemo(() => parseMarkdown(text), [text]);
  return (
    <>
      {blocks.map((b, i) => (
        <BlockView key={i} b={b} />
      ))}
    </>
  );
}
