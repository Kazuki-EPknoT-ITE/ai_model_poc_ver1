import type { AppApi } from "../shared/ipc.js";

declare global {
  interface Window {
    app: AppApi;
  }
}
export {};
