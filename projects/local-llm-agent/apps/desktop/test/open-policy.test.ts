// ファイルを開くときの決まり（PDF-07 と、AI が書いた実行ファイルを誤って実行しないこと）
import { describe, expect, it } from "vitest";
import { canOpen, citationPath, pageOf } from "../src/shared/open-policy.js";

describe("open policy", () => {
  it("opens documents but never executables, scripts, shortcuts or macro files", () => {
    for (const ok of ["C:\\ws\\a.pdf", "C:\\ws\\送金明細.xlsx", "C:\\ws\\報告.docx", "C:\\ws\\memo.txt", "C:\\ws\\data.csv"]) expect(canOpen(ok), ok).toBe(true);
    for (const ng of ["C:\\ws\\run.exe", "C:\\ws\\x.BAT", "C:\\ws\\a.ps1", "C:\\ws\\link.lnk", "C:\\ws\\m.xlsm", "C:\\ws\\v.vbs", "C:\\ws\\i.hta", "C:\\ws\\t.js", "C:\\ws\\evil.exe.", "C:\\ws\\evil.exe "])
      expect(canOpen(ng), ng).toBe(false);
  });

  it("finds the PDF page in a citation location", () => {
    expect(pageOf("p.12")).toBe(12);
    expect(pageOf("p12 / 第5条")).toBe(12);
    expect(pageOf("3ページ")).toBe(3);
    expect(pageOf("page 7")).toBe(7);
    expect(pageOf("第3条")).toBeNull();
    expect(pageOf(undefined)).toBeNull();
  });

  it("resolves citation paths against the workspace", () => {
    expect(citationPath("規程/a.pdf", "C:\\ws")).toBe("C:\\ws\\規程\\a.pdf");
    expect(citationPath("D:\\docs\\a.pdf", "C:\\ws")).toBe("D:\\docs\\a.pdf");
    expect(citationPath("a.pdf", null)).toBeNull();
  });
});
