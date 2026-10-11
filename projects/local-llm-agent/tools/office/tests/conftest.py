"""pytest の fixture。中身は office_testkit にある（全体で pytest を回すと conftest の名前が衝突するため、
試験からは conftest ではなく office_testkit を import する）。"""

from office_testkit import run, ws  # noqa: F401
