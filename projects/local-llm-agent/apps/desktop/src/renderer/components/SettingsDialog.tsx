import { useEffect, useState } from "react";
import type { RuntimeCommand } from "@lla/protocol";
import { Dialog } from "./Dialog.js";

type Net = "off" | "search" | "browse";
type Model = "auto" | "fast" | "accurate";

/** 設定（UI 仕様の設定画面）。端末の設定（Main）と作業の設定（Runtime）をまとめて扱う。 */
export function SettingsDialog({
  send,
  netCap,
  onSaved,
  onClose,
}: {
  send: (type: RuntimeCommand["type"], payload: Record<string, unknown>) => Promise<unknown>;
  netCap: Net;
  onSaved: (s: { net_level: Net; model_default: Model }) => void;
  onClose: () => void;
}) {
  const [serverUrl, setServerUrl] = useState("");
  const [spareUrl, setSpareUrl] = useState("");
  const [autoStart, setAutoStart] = useState(true);
  const [net, setNet] = useState<Net>("off");
  const [model, setModel] = useState<Model>("auto");
  const [minutes, setMinutes] = useState(120);
  const [saved, setSaved] = useState(false);
  const order: Net[] = ["off", "search", "browse"];

  useEffect(() => {
    void window.app.settings.get().then((s) => {
      setServerUrl(s.serverUrl);
      setSpareUrl(s.spareUrl);
      setAutoStart(s.autoStart);
    });
    void send("settings.get", {}).then((r) => {
      const s = r as { net_level: Net; model_default: Model; notify_long_run_minutes: number };
      setNet(s.net_level);
      setModel(s.model_default);
      setMinutes(s.notify_long_run_minutes);
    });
  }, [send]);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    await window.app.settings.set({ serverUrl, spareUrl: spareUrl.trim(), autoStart });
    await send("settings.update", { net_level: net, model_default: model, notify_long_run_minutes: minutes });
    onSaved({ net_level: net, model_default: model });
    setSaved(true);
  };

  return (
    <Dialog title="設定" onClose={onClose}>
      <form className="settings" onSubmit={save}>
        <label>
          社内サーバーの URL
          <input value={serverUrl} onChange={(e) => setServerUrl(e.target.value)} />
          <span className="small muted">変更は次回のログインから有効になります</span>
        </label>
        <label>
          予備のサーバーの URL（任意）
          <input value={spareUrl} onChange={(e) => setSpareUrl(e.target.value)} placeholder="社内サーバーに繋がらないときに使います" />
        </label>
        <label>
          既定のモデル
          <select value={model} onChange={(e) => setModel(e.target.value as Model)}>
            <option value="auto">自動</option>
            <option value="fast">高速</option>
            <option value="accurate">高精度</option>
          </select>
        </label>
        <label>
          ネットアクセス
          <select value={net} onChange={(e) => setNet(e.target.value as Net)}>
            {order.map((o) => (
              <option key={o} value={o} disabled={order.indexOf(o) > order.indexOf(netCap)}>
                {o === "off" ? "オフ" : o === "search" ? "検索のみ" : "検索＋ページ閲覧"}
              </option>
            ))}
          </select>
          <span className="small muted">管理者が許可している範囲まで選べます</span>
        </label>
        <label>
          長時間の作業を知らせるまでの時間（分）
          <input type="number" min={10} max={1440} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} />
        </label>
        <label className="check">
          <input type="checkbox" checked={autoStart} onChange={(e) => setAutoStart(e.target.checked)} />
          Windows へのサインイン時に起動する
        </label>
        <div className="row">
          {saved && (
            <span className="small ok" role="status">
              保存しました
            </span>
          )}
          <button type="button" onClick={onClose}>
            閉じる
          </button>
          <button type="submit" className="primary">
            保存
          </button>
        </div>
      </form>
    </Dialog>
  );
}
