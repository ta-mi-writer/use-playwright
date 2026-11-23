"""作品単位のローカルストア（CIDで重複排除）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from utils import DATA_DIR, write_json_atomic

WORKS_PATH = DATA_DIR / "works.json"


def now_iso() -> str:
  return datetime.now(UTC).isoformat()


def load_works(path: Path = WORKS_PATH) -> dict[str, dict[str, Any]]:
  """works.json を {cid: 作品dict} で読み込む。

  ファイル無しは空 dict、トップレベルが dict 以外は ValueError。
  """
  if not path.exists():
    return {}
  try:
    with path.open("r", encoding="utf-8") as f:
      data = json.load(f)
  except json.JSONDecodeError, OSError:
    return {}
  if not isinstance(data, dict):
    raise ValueError(  # noqa: TRY004 - 仕様で ValueError と定義
      f"works.json のトップレベルは dict である必要があります: {path}"
    )
  return data


def save_works(works: dict[str, dict[str, Any]], path: Path = WORKS_PATH) -> None:
  """works.json を原子的に保存する。"""
  write_json_atomic(path, works)


def ensure_work(
  works: dict[str, dict[str, Any]], cid: str, work_url: str | None = None
) -> dict[str, Any]:
  """cid の作品エントリを取得し、無ければ作成して返す。"""
  work = works.get(cid)
  if work is None:
    now = now_iso()
    work = {"cid": cid, "created_at": now, "updated_at": now}
    works[cid] = work
  if work_url is not None:
    work["work_url"] = work_url
  return work
