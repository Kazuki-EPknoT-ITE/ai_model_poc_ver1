import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./Icon.js";

export interface Macro {
  name: string;
  description: string;
  prompt: string;
}

type Net = "off" | "search" | "browse";
type Model = "auto" | "fast" | "accurate";

/**
 * 依頼の入力欄（会話の列の下端の角丸のカード）。添付（APP-02）と定型作業のスラッシュコマンド（APP-09）を扱い、
 * 作業フォルダ・モデル・ネットの小さな操作をカードの中に置く。
 */
export function Composer(p: {
  disabled: boolean;
  placeholder: string;
  running: boolean;
  paused: boolean;
  macros: Macro[];
  workspace: string;
  folderLocked: boolean;
  model: Model;
  netLevel: Net;
  /** 例のチップなどから入力欄に入れる文（n が変わるたびに入れ直す） */
  seed: { text: string; n: number } | null;
  onPickFolder: () => void;
  onModel: (m: Model) => void;
  onNet: (n: Net) => void;
  onSend: (text: string, attachments: string[]) => void;
  onPause: () => void;
  onStop: () => void;
}) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<string[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [active, setActive] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const folderBtn = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!p.seed) return;
    setText(p.seed.text);
    // 入力欄が使えない（作業フォルダが未選択）ときは、次にすること（フォルダを選ぶ）へフォーカスを移す
    if (box.current?.disabled) folderBtn.current?.focus();
    else box.current?.focus();
  }, [p.seed]);

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
  const folderName = p.workspace ? (p.workspace.split(/[\\/]/).filter(Boolean).pop() ?? p.workspace) : "";

  return (
    <div
      className={`composer ${dragOver ? "drag" : ""} ${p.disabled ? "is-disabled" : ""}`}
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
            <li key={a} className="chip" data-testid="attachment" title={a}>
              <Icon name="file" size={14} />
              <span className="chip-name">{a.split(/[\\/]/).pop()}</span>
              <button className="icon-btn tiny" aria-label={`${a} を外す`} onClick={() => setAttachments((x) => x.filter((y) => y !== a))}>
                <Icon name="x" size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
      <textarea
        ref={box}
        rows={2}
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
      <div className="composer-bar">
        <input ref={fileInput} type="file" multiple hidden onChange={(e) => addFiles(e.target.files)} data-testid="file-input" />
        <button className="icon-btn" onClick={() => fileInput.current?.click()} disabled={p.disabled} aria-label="ファイルを添付" title="ファイルを添付">
          <Icon name="paperclip" size={17} />
        </button>
        <button ref={folderBtn} className="folder-btn" onClick={p.onPickFolder} disabled={p.folderLocked} title={p.workspace || "作業フォルダ"}>
          <Icon name="folder" size={15} />
          <span className="folder-name">{folderName || "作業フォルダを選ぶ"}</span>
        </button>
        {/* 例のチップなどで文が入っていても、作業フォルダが無いと送れない。何をすればよいかをその場で示す */}
        {!p.workspace && text.trim() && (
          <span className="composer-need-folder" role="status" data-testid="need-folder">
            作業フォルダを選ぶと送信できます
          </span>
        )}
        <select className="mini-select" value={p.model} onChange={(e) => p.onModel(e.target.value as Model)} aria-label="モデル" title="モデル">
          <option value="auto">自動</option>
          <option value="fast">高速</option>
          <option value="accurate">高精度</option>
        </select>
        <select className="mini-select" value={p.netLevel} onChange={(e) => p.onNet(e.target.value as Net)} aria-label="ネットアクセス" title="ネットアクセス">
          <option value="off">ネット オフ</option>
          <option value="search">検索のみ</option>
          <option value="browse">検索＋閲覧</option>
        </select>
        <span className="spacer" />
        {p.running && (
          <>
            <button className="ghost-btn" onClick={p.onPause}>
              <Icon name={p.paused ? "play" : "pause"} size={14} />
              {p.paused ? "再開" : "一時停止"}
            </button>
            <button className="ghost-btn danger" onClick={p.onStop}>
              <Icon name="stop" size={14} />
              停止
            </button>
          </>
        )}
        <button
          className="send-btn"
          onClick={submit}
          disabled={p.disabled || !text.trim()}
          aria-label={p.running ? "追加の指示を送る" : "送信"}
          title={p.running ? "追加の指示を送る（Ctrl+Enter）" : "送信（Ctrl+Enter）"}
        >
          <Icon name="arrowUp" size={18} />
        </button>
      </div>
    </div>
  );
}
