"""pytest の fixture。中身は rag_testkit にある（全体で pytest を回すと conftest の名前が衝突するため、
試験からは conftest ではなく rag_testkit を import する）。"""

from rag_testkit import make_index  # noqa: F401
