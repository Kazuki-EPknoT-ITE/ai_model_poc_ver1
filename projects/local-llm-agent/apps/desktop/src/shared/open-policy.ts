// ファイルを既定のアプリで開くときの決まり（PDF-07 の該当箇所の表示、ファイルツリー・成果物）。
// AI が作業フォルダに書いたファイルを誤って実行しないよう、実行ファイル・スクリプト・ショートカットは開かない。

const BLOCKED = /\.(exe|com|bat|cmd|ps1|psm1|psd1|vbs|vbe|js|jse|wsf|wsh|msi|msp|mst|scr|hta|cpl|lnk|reg|jar|pif|application|appref-ms|url|scf|inf|sys|dll|ocx|chm|settingcontent-ms|library-ms|search-ms|xll|xlam|ppam|docm|dotm|xlsm|pptm)$/i;

/** 開いてよいファイルか（拡張子で判定）。マクロ付きの Office 文書も開かない（マクロの実行を避ける）。 */
export function canOpen(path: string): boolean {
  const name = path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? "";
  // 末尾のドット・空白は Windows が無視するので、取り除いてから見る
  return !BLOCKED.test(name.replace(/[. ]+$/, ""));
}

/** 出典の該当箇所（"p.12" / "12ページ" / "page 3"）から PDF のページ番号を取り出す。無ければ null。 */
export function pageOf(location: string | undefined): number | null {
  if (!location) return null;
  const m = /(?:p\.?\s*|page\s*)(\d{1,5})|(\d{1,5})\s*(?:ページ|頁)/i.exec(location);
  const n = Number(m?.[1] ?? m?.[2]);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** 出典のファイル（作業フォルダからの相対・絶対）を、開くための絶対パスにする。 */
export function citationPath(ref: string, workspace: string | null): string | null {
  if (/^[A-Za-z]:[\\/]|^\\\\/.test(ref)) return ref;
  if (!workspace) return null;
  return `${workspace.replace(/[\\/]+$/, "")}\\${ref.replace(/\//g, "\\")}`;
}
