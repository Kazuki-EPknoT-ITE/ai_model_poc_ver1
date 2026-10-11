// 障害注入試験（T-51、試験仕様 §4「トリガー」）の試験フック。環境変数 AGENT_TEST_HOOKS=1 のときだけ働く。
//
// - 通過するたびに標準エラーへ `@@hook {"point":...}` を1行出す（試験側がこれを見て注入する）。
// - AGENT_TEST_HOOK_FREEZE="<point>:<n>[:<ms>]" なら、その point の n 回目で `@@frozen <point>` を出して止まる。
//   ms を省くと止まったまま（試験側がプロセスを強制終了する）。ms があればその時間だけ止まって続ける。
//
// 配布物のビルドでは process.env.AGENT_TEST_HOOKS を "0" に置き換え、分岐ごと取り除く
// （apps/desktop/scripts/build.mjs。ビルド後に AGENT_TEST_HOOK の文字列が残っていないことを検査する）。
import { writeSync } from "node:fs";

const counts = new Map<string, number>();
const loadedAt = Date.now();

/**
 * 経過時間の判定に使う「今」。AGENT_TEST_CLOCK_SPEED=N なら、起動からの経過を N 倍に早送りする
 * （FI-01: 2時間を超える実行の模擬）。配布物では常に Date.now()。
 */
export function testNow(): number {
  if (process.env.AGENT_TEST_HOOKS === "1") {
    const speed = Number(process.env.AGENT_TEST_CLOCK_SPEED ?? 1) || 1;
    return loadedAt + (Date.now() - loadedAt) * speed;
  }
  return Date.now();
}

export function testHook(point: string, info: Record<string, unknown> = {}): void {
  if (process.env.AGENT_TEST_HOOKS === "1") {
    const n = (counts.get(point) ?? 0) + 1;
    counts.set(point, n);
    writeSync(2, `@@hook ${JSON.stringify({ point, n, ...info })}\n`);
    const [p, at, ms] = (process.env.AGENT_TEST_HOOK_FREEZE ?? "").split(":");
    if (p === point && Number(at ?? 1) === n) {
      writeSync(2, `@@frozen ${point}\n`);
      // 同期的に止める（イベントループも止まるので、この間に DB への書き込みは起きない）
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms ? Number(ms) : undefined);
    }
  }
}
