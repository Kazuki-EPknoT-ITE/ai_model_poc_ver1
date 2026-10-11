// 一時的な失敗の再試行（RUN-03）。回数は無制限で、間隔を1秒から最大60秒まで広げる（ジッター付き）。
import { sleep } from "../util.js";

export interface RetryOptions {
  baseMs?: number;
  maxMs?: number;
  signal?: AbortSignal;
  onRetry?: (attempt: number, delayMs: number, cause: string) => void;
}

export function backoffDelay(attempt: number, baseMs = 1000, maxMs = 60_000, hintMs?: number): number {
  const exp = Math.min(maxMs, baseMs * 2 ** Math.max(0, attempt - 1));
  const jitter = exp * (0.8 + Math.random() * 0.4);
  return Math.round(Math.max(hintMs ?? 0, Math.min(maxMs, jitter)));
}

/**
 * fn が投げた例外のうち isTransient が true のものは再試行し続ける。中止されたら投げ直す。
 * cause は利用者に見せる失敗の種類（ErrorCode）。
 */
export async function retryForever<T>(
  fn: () => Promise<T>,
  classify: (e: unknown) => { transient: boolean; cause: string; hintMs?: number },
  opts: RetryOptions = {},
): Promise<T> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await fn();
    } catch (e) {
      if (opts.signal?.aborted) throw e;
      const c = classify(e);
      if (!c.transient) throw e;
      const delay = backoffDelay(attempt, opts.baseMs, opts.maxMs, c.hintMs);
      opts.onRetry?.(attempt, delay, c.cause);
      await sleep(delay, opts.signal);
    }
  }
}
