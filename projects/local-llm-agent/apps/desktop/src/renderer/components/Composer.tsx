import { useMemo, useRef, useState } from "react";

export interface Macro {
  name: string;
  description: string;
  prompt: string;
}

/** 依頼の入力欄。添付（APP-02）と定型作業のスラッシュコマンド（APP-09）を扱う。 */
export function Composer(p: {
  disabled: boolean;
  placeholder: string;
  running: boolean;
  paused: boolean;
  macros: Macro[];
  onSend: (text: string, attachments: string[]) => void;
  onPause: () => void;
  onStop: () => void;
}) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<string[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [active, setActive] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);

  const slash = text.startsWith("/") && !text.includes("\n") ? text.slice(1) : null;
  const suggestions = useMemo(() => (slash === null ? [] : p.macros.filter((m) => m.name.includes(slash))), [slash, p.macros]);

  const addFiles = (files: FileList | null) => {
    if (!files) return;
    const paths = [...files].map((f) => window.app.files.pathOf(f)).filter(Boolean);
    setAttachments((a) => [...new Set([...a, ...paths])]);
  };
  const pick = (m: Macro) => {
    setText(m.prompt);
    setActive(0);
  };
  const submit = () => {
    if (!text.trim() || p.disabled) return;
    p.onSend(text.trim(), attachments);
    setText("");
    setAttachments([]);
  };

  return (
    <div
      className={`composer ${dragOver ? "drag" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        addFiles(e.dataTransfer.files);
      }}
    >
      {suggestions.length > 0 && (
        <ul className="suggest" role="listbox" aria-label="定型作業">
          {suggestions.map((m, i) => (
            <li key={m.name} role="option" aria-selected={i === active}>
              <button className={i === active ? "active" : ""} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(m)}>
                <strong>/{m.name}</strong> <span className="muted small">{m.description}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {attachments.length > 0 && (
        <ul className="chips" aria-label="添付ファイル">
          {attachments.map((a) => (
            <li key={a} className="chip" data-testid="attachment">
              📄 {a.split(/[\\/]/).pop()}
              <button className="link" aria-label={`${a} を外す`} onClick={() => setAttachments((x) => x.filter((y) => y !== a))}>
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
      <textarea
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setActive(0);
        }}
        placeholder={p.placeholder}
        disabled={p.disabled}
        aria-label="依頼"
        onKeyDown={(e) => {
          if (suggestions.length > 0) {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => (a + (e.key === "ArrowDown" ? 1 : suggestions.length - 1)) % suggestions.length);
              return;
            }
            if (e.key === "Enter" || e.key === "Tab") {
              e.preventDefault();
              pick(suggestions[active]!);
              return;
            }
          }
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <div className="composer-actions">
        <input ref={fileInput} type="file" multiple hidden onChange={(e) => addFiles(e.target.files)} data-testid="file-input" />
        <button onClick={() => fileInput.current?.click()} disabled={p.disabled} aria-label="ファイルを添付">
          📎 添付
        </button>
        <span className="spacer" />
        {p.running && (
          <>
            <button onClick={p.onPause}>{p.paused ? "再開" : "一時停止"}</button>
            <button className="danger" onClick={p.onStop}>
              停止
            </button>
          </>
        )}
        <button className="primary" onClick={submit} disabled={p.disabled || !text.trim()}>
          {p.running ? "追加の指示を送る" : "送信"}
        </button>
      </div>
    </div>
  );
}
