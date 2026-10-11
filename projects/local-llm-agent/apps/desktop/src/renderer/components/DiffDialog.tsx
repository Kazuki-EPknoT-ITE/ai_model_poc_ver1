import { useEffect, useState } from "react";
import { Dialog } from "./Dialog.js";

interface Change {
  kind: string;
  location: string;
  before: string | null;
  after: string | null;
}
export interface ArtifactDiff {
  kind: "docx" | "xlsx" | "text" | "binary";
  path: string;
  op: string;
  changes: Change[];
  truncated: boolean;
  note?: string;
}

const KIND_LABEL: Record<string, string> = { insert: "追加", add: "追加", delete: "削除", remove: "削除", modify: "変更" };

/** 差分プレビュー（AGT-03）。Word は段落、Excel はセル、テキストは行の単位で表示する。 */
export function DiffDialog({ path, load, onClose }: { path: string; load: () => Promise<ArtifactDiff>; onClose: () => void }) {
  const [diff, setDiff] = useState<ArtifactDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    load()
      .then(setDiff)
      .catch((e: Error) => setError(e.message.replace(/^Error invoking remote method '[^']+': (Error: )?/, "")));
  }, [load]);
  const name = path.split(/[\\/]/).pop();
  return (
    <Dialog title={`変更の確認: ${name}`} onClose={onClose} wide>
      {error && <p className="error">{error}</p>}
      {!diff && !error && <p className="muted">読み込み中…</p>}
      {diff && (
        <div data-testid="diff" data-kind={diff.kind}>
          {diff.note && <p className="muted">{diff.note}</p>}
          {diff.changes.length === 0 && !diff.note && <p className="muted">内容の変更はありません。</p>}
          {diff.kind === "xlsx" && diff.changes.length > 0 && (
            <table className="diff-table">
              <thead>
                <tr>
                  <th>セル</th>
                  <th>変更前</th>
                  <th>変更後</th>
                </tr>
              </thead>
              <tbody>
                {diff.changes.map((c) => (
                  <tr key={c.location} className={c.kind}>
                    <td className="mono">{c.location}</td>
                    <td className="del">{c.before ?? ""}</td>
                    <td className="ins">{c.after ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {diff.kind !== "xlsx" && diff.changes.length > 0 && (
            <ol className="diff-list">
              {diff.changes.map((c, i) => (
                <li key={i} className={c.kind}>
                  <span className={`badge ${c.kind}`}>{KIND_LABEL[c.kind] ?? c.kind}</span>
                  <span className="mono small muted">{c.location}</span>
                  {c.before !== null && <div className="del">− {c.before}</div>}
                  {c.after !== null && <div className="ins">＋ {c.after}</div>}
                </li>
              ))}
            </ol>
          )}
          {diff.truncated && <p className="small muted">変更が多いため、先頭の一部だけを表示しています。</p>}
        </div>
      )}
    </Dialog>
  );
}
