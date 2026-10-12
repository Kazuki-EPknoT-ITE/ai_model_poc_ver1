// Renderer に公開する型付き API（プロトコル §5.3）。Node の機能は渡さない。
import { contextBridge, ipcRenderer, webUtils, type IpcRendererEvent } from "electron";
import type { AppApi } from "../shared/ipc.js";

const on = <T>(channel: string, cb: (v: T) => void) => {
  const h = (_e: IpcRendererEvent, v: T) => cb(v);
  ipcRenderer.on(channel, h);
  return () => ipcRenderer.removeListener(channel, h);
};

const api: AppApi = {
  auth: {
    status: () => ipcRenderer.invoke("auth:status"),
    login: (p) => ipcRenderer.invoke("auth:login", p),
    logout: () => ipcRenderer.invoke("auth:logout"),
  },
  runtime: {
    send: (command) => ipcRenderer.invoke("runtime:send", command),
    onEvent: (cb) => on("runtime:event", cb),
    onStatus: (cb) => on("runtime:status", cb),
  },
  dialog: { pickFolder: () => ipcRenderer.invoke("dialog:pickFolder") },
  files: { pathOf: (file) => webUtils.getPathForFile(file) },
  shell: { openPath: (p, opts) => ipcRenderer.invoke("shell:openPath", p, opts) },
  workspace: { list: (dir) => ipcRenderer.invoke("workspace:list", dir) },
  settings: {
    get: () => ipcRenderer.invoke("settings:get"),
    set: (patch) => ipcRenderer.invoke("settings:set", patch),
  },
  app: { quit: () => ipcRenderer.invoke("app:quit"), version: () => ipcRenderer.invoke("app:version") },
};

contextBridge.exposeInMainWorld("app", api);
