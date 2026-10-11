"""評価（試験仕様 rag_qa: 上位5件に根拠を含む割合）。合成データで回す。"""

from __future__ import annotations

import json
from pathlib import Path

from rag_testkit import IndexFactory, make_settings, write_docx, write_pdf, write_text

from llm_rag.evaluate import EvalItem, Evidence, is_evidence_hit, load_items, load_users, recall_at_k
from llm_rag.ingest import run_ingest
from llm_rag.search import Hit, Searcher

# (ファイル名, 本文, 質問, 根拠の location の一部)
TOPICS: list[tuple[str, str, str]] = [
    ("経費精算.md", "交通費の精算は月末締めで、領収書を添付して申請する。", "交通費の精算の締め日は？"),
    ("休暇.md", "有給休暇は入社半年後に10日付与される。", "有給休暇は何日付与される？"),
    ("出張.md", "海外出張の日当は1日5000円とする。", "海外出張の日当はいくら？"),
    ("情報セキュリティ.md", "USBメモリの持ち出しは情報システム部の許可が必要。", "USBメモリを持ち出すには？"),
    ("防災.md", "地震発生時は机の下に避難し、揺れが収まったら階段で屋外へ出る。", "地震のときの避難方法"),
    ("採用.md", "中途採用の面接は二回実施し、最終面接は役員が担当する。", "中途採用の面接回数"),
    ("備品.md", "ノートパソコンの貸与期間は四年で、期間満了後に返却する。", "ノートパソコンの貸与期間"),
    ("社宅.md", "社宅の家賃は給与から天引きされ、上限は月三万円。", "社宅の家賃の上限"),
    ("研修.md", "新入社員研修は四月に二週間、本社で行う。", "新入社員研修の期間と場所"),
    ("評価.md", "人事評価は年二回、上期と下期に面談を行う。", "人事評価の面談は年何回？"),
    ("安全.md", "荷役作業ではヘルメットと安全靴の着用を義務とする。", "荷役作業で着用が必要な保護具"),
    ("燃料.md", "バンカー燃料の発注は寄港の七日前までに代理店へ依頼する。", "バンカー燃料の発注期限"),
    ("入港.md", "入港届は到着の二十四時間前までに港長へ提出する。", "入港届の提出期限"),
    ("保険.md", "船体保険の更新は毎年四月一日に行う。", "船体保険の更新日"),
    ("文書管理.md", "契約書の原本は法務部の金庫で十年間保管する。", "契約書原本の保管期間"),
]


def _build(tmp_path: Path, make_index: IndexFactory) -> tuple[Searcher, Path]:
    docs = tmp_path / "docs"
    for i, (name, body, _) in enumerate(TOPICS):
        filler = "\n".join(
            f"関連する一般的な注意事項その{j}。詳細は担当部署へ確認する。" for j in range(i % 4)
        )
        write_text(docs / "規程" / name, f"# {name[:-3]}\n\n{body}\n\n{filler}\n")
    write_docx(
        docs / "契約" / "傭船契約.docx",
        [("h1", "第5条 支払期日"), ("p", "傭船料は毎月末日までに前払いする。")],
    )
    write_pdf(
        docs / "契約" / "運送約款.pdf", [["第1条 適用"], ["第8条 運賃", "運賃は貨物の重量に応じて算定する。"]]
    )
    s = make_settings(tmp_path, [(str(docs / "規程"), ["all"]), (str(docs / "契約"), ["chartering"])])
    idx = make_index(s)
    run_ingest(idx)
    return Searcher(idx), docs


def test_recall_at_5_on_synthetic_set(tmp_path: Path, make_index: IndexFactory) -> None:
    searcher, docs = _build(tmp_path, make_index)
    items = [
        EvalItem(f"q{i:02d}", q, ["all"], [Evidence(str(docs / "規程" / name))])
        for i, (name, _, q) in enumerate(TOPICS)
    ]
    items.append(
        EvalItem(
            "q-docx",
            "傭船料はいつまでに払う？",
            ["chartering"],
            [Evidence(str(docs / "契約" / "傭船契約.docx"), "第5条")],
        )
    )
    items.append(
        EvalItem(
            "q-pdf", "運賃の算定方法", ["chartering"], [Evidence(str(docs / "契約" / "運送約款.pdf"), "p.2")]
        )
    )
    res = recall_at_k(items, lambda q, g, k: searcher.search(q, g, top_k=k), k=5)
    assert res.total == len(TOPICS) + 2
    assert res.recall >= 0.9, res.as_dict()
    d = res.as_dict()
    assert d["metric"] == "recall@5" and d["value"] == round(res.recall, 4)


def test_acl_affects_evaluation(tmp_path: Path, make_index: IndexFactory) -> None:
    searcher, docs = _build(tmp_path, make_index)
    item = EvalItem(
        "q-acl", "傭船料はいつまでに払う？", ["all"], [Evidence(str(docs / "契約" / "傭船契約.docx"))]
    )
    res = recall_at_k([item], lambda q, g, k: searcher.search(q, g, top_k=k))
    assert res.recall == 0.0 and res.misses == ["q-acl"]


def test_recall_function_and_evidence_matching() -> None:
    def fake(q: str, groups: list[str], k: int) -> list[Hit]:
        return [Hit(f"r{i}", f"\\\\NAS\\docs\\{q}{i}.pdf", f"p.{i} / 第{i}条", 1.0, "") for i in range(1, 8)]

    items = [
        EvalItem("a", "x", ["g"], [Evidence("//nas/docs/x3.pdf")]),  # 区切り・大文字小文字の違いは同一視
        EvalItem("b", "x", ["g"], [Evidence("//nas/docs/x6.pdf")]),  # 6位なので外れ
        EvalItem("c", "x", ["g"], [Evidence("//nas/docs/x2.pdf", "第2条")]),
        EvalItem("d", "x", ["g"], [Evidence("//nas/docs/x2.pdf", "第3条")]),  # 場所が違う
    ]
    res = recall_at_k(items, fake, k=5)
    assert (res.found, res.total, res.misses) == (2, 4, ["b", "d"])
    assert res.recall == 0.5
    assert is_evidence_hit(Hit("r1", "C:/a/b.txt", "行1-5", 1.0, ""), Evidence("c:\\A\\B.txt"))
    assert recall_at_k([], fake).recall == 0.0


def test_load_items_with_acl_users(tmp_path: Path) -> None:
    users = tmp_path / "users.yaml"
    users.write_text("u-exec: [executive, all]\nu-staff: [all]\n", encoding="utf-8")
    items_path = tmp_path / "items.jsonl"
    rows = [
        {
            "id": "1",
            "question": "q1",
            "acl_user": "u-exec",
            "evidence": [{"path": "a.pdf", "location": "p.1"}],
        },
        {"id": "2", "question": "q2", "acl_groups": ["x"], "evidence": [{"path": "b.pdf"}]},
    ]
    items_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n\n", encoding="utf-8"
    )
    items = load_items(items_path, load_users(users))
    assert items[0].acl_groups == ["executive", "all"]
    assert items[0].evidence == [Evidence("a.pdf", "p.1")]
    assert items[1].acl_groups == ["x"] and items[1].evidence[0].location is None
