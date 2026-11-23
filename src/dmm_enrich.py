from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from utils import DATA_PATH, DEFAULT_USER_AGENT, write_json_atomic

API_BASE = "https://api.dmm.com/affiliate/v3/ItemList"
API_PARAMS = {
  "site": "FANZA",
  "service": "digital",
  "floor": "videoa",
  "output": "json",
}


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    description="DMM API で作品メタデータを補完し works.json / 受信箱を更新する"
  )
  parser.add_argument("--limit", type=int, default=0, help="処理件数上限(0=無制限)")
  parser.add_argument(
    "--cid", action="append", default=[], help="処理対象CID(繰り返し指定可)"
  )
  parser.add_argument("--refresh", action="store_true", help="既存doneでも実通信し直す")
  parser.add_argument("--timeout", type=int, default=30000, help="通信タイムアウト(ms)")
  parser.add_argument(
    "--api-delay", type=float, default=1.0, help="API呼び出し間のスリープ秒"
  )
  parser.add_argument(
    "--max-attempts",
    type=int,
    default=3,
    help="失敗時に受信箱をdeadにするまでの試行回数",
  )
  parser.add_argument(
    "--selftest", action="store_true", help="map_item の純粋関数テストを実行して終了"
  )
  return parser


def load_inbox() -> list[dict[str, Any]]:
  try:
    with DATA_PATH.open("r", encoding="utf-8") as f:
      data = json.load(f)
  except FileNotFoundError:
    return []
  if not isinstance(data, list):
    return []
  return [item for item in data if isinstance(item, dict)]


def select_targets(
  inbox: list[dict[str, Any]],
  extra_cids: list[str],
  limit: int,
) -> list[tuple[str, dict[str, Any] | None]]:
  targets: list[tuple[str, dict[str, Any] | None]] = []
  seen: set[str] = set()
  for item in inbox:
    if item.get("status") != "reviews_done":
      continue
    cid = item.get("cid")
    if not isinstance(cid, str):
      continue
    if cid in seen:
      continue
    seen.add(cid)
    targets.append((cid, item))
  for cid in extra_cids:
    if not isinstance(cid, str) or cid in seen:
      continue
    seen.add(cid)
    targets.append((cid, None))
  if limit > 0:
    targets = targets[:limit]
  return targets


def fetch_dmm(
  api_id: str, affiliate_id: str, cid: str, timeout_sec: float
) -> dict[str, Any]:
  params = dict(API_PARAMS)
  params.update({"api_id": api_id, "affiliate_id": affiliate_id, "cid": cid})
  url = API_BASE + "?" + urllib.parse.urlencode(params)
  req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
  with urllib.request.urlopen(req, timeout=timeout_sec) as response:
    body = response.read()
  return json.loads(body.decode("utf-8"))


def _first_name(values: Any) -> str | None:
  if not isinstance(values, list) or not values:
    return None
  first = values[0]
  if isinstance(first, dict):
    name = first.get("name")
    return name if isinstance(name, str) else None
  if isinstance(first, str):
    return first
  return None


def _names(values: Any) -> list[str]:
  if not values:
    return []
  if isinstance(values, str):
    return [values]
  if not isinstance(values, list):
    return []
  out: list[str] = []
  for value in values:
    if isinstance(value, dict):
      name = value.get("name")
      if isinstance(name, str):
        out.append(name)
    elif isinstance(value, str):
      out.append(value)
  return out


def _normalize_release_date(value: Any) -> str | None:
  """DMM API の日付文字列を YYYY-MM-DD に正規化する(純粋関数)。"""
  if not isinstance(value, str):
    return None
  stripped = value.strip()
  if (
    len(stripped) >= 10
    and stripped[:4].isdigit()
    and stripped[4] == "-"
    and stripped[5:7].isdigit()
    and stripped[7] == "-"
    and stripped[8:10].isdigit()
  ):
    return stripped[:10]
  return stripped


def map_item(item: dict[str, Any]) -> dict[str, Any]:
  """DMM API の item 1 件を works.json 用フィールドにマップする(純粋関数)。"""
  iteminfo = item.get("iteminfo")
  if not isinstance(iteminfo, dict):
    iteminfo = {}

  maker = _first_name(iteminfo.get("maker"))
  label = _first_name(iteminfo.get("label"))
  genres = _names(iteminfo.get("genre"))
  actresses = _names(iteminfo.get("actress"))

  # トップレベルに直接ある場合のフォールバック
  if maker is None and isinstance(item.get("maker"), str):
    maker = item.get("maker")
  if label is None and isinstance(item.get("label"), str):
    label = item.get("label")
  if not genres:
    genres = _names(item.get("genre"))
  if not actresses:
    actresses = _names(item.get("actress"))

  image_url = item.get("imageURL")
  package_image_url: str | None = None
  if isinstance(image_url, dict):
    large = image_url.get("large")
    if isinstance(large, str):
      package_image_url = large
    else:
      image_list = image_url.get("list")
      if isinstance(image_list, list) and image_list:
        first = image_list[0]
        if isinstance(first, str):
          package_image_url = first

  review = item.get("review")
  if not isinstance(review, dict):
    review = {}

  product_url = item.get("URL")
  if not isinstance(product_url, str):
    product_url = item.get("product_url")
    if not isinstance(product_url, str):
      product_url = None

  return {
    "content_id": item.get("content_id")
    if isinstance(item.get("content_id"), str)
    else None,
    "title": item.get("title") if isinstance(item.get("title"), str) else None,
    "maker": maker,
    "label": label,
    "release_date": _normalize_release_date(
      item.get("date")
      if isinstance(item.get("date"), str)
      else item.get("release_date")
    ),
    "genres": genres,
    "actresses": actresses,
    "package_image_url": package_image_url,
    "product_url": product_url,
    "api_review_average": review.get("average"),
    "api_review_count": review.get("count"),
  }


def mark_inbox_enriched(item: dict[str, Any], now: str) -> None:
  item["status"] = "enriched"
  item["attempts"] = 0
  item.pop("last_error", None)
  item.pop("stage", None)
  item["updated_at"] = now


def mark_inbox_failed(item: dict[str, Any], max_attempts: int, now: str) -> None:
  item["attempts"] = int(item.get("attempts") or 0) + 1
  item["stage"] = "s4"
  item["status"] = "dead" if item["attempts"] >= max_attempts else "error"
  item["updated_at"] = now


def run_selftest() -> int:
  cases: list[dict[str, Any]] = [
    {
      "name": "トップレベルdate優先",
      "item": {
        "content_id": "cid1",
        "title": "タイトル",
        "iteminfo": {
          "maker": [{"name": "メーカーA"}],
          "label": [{"name": "レーベルB"}],
          "genre": [{"name": "ジャンル1"}, {"name": "ジャンル2"}],
          "actress": [{"name": "女優1"}, {"name": "女優2"}],
        },
        "imageURL": {
          "large": "https://example.com/large.jpg",
          "list": ["https://example.com/list.jpg"],
        },
        "review": {"average": "4.5", "count": "100"},
        "URL": "https://www.dmm.co.jp/product/cid1/",
        "date": "2026-01-09 00:00:00",
      },
      "expected": {
        "content_id": "cid1",
        "title": "タイトル",
        "maker": "メーカーA",
        "label": "レーベルB",
        "release_date": "2026-01-09",
        "genres": ["ジャンル1", "ジャンル2"],
        "actresses": ["女優1", "女優2"],
        "package_image_url": "https://example.com/large.jpg",
        "product_url": "https://www.dmm.co.jp/product/cid1/",
        "api_review_average": "4.5",
        "api_review_count": "100",
      },
    },
    {
      "name": "release_dateフォールバック",
      "item": {
        "content_id": "cid2",
        "title": "タイトル2",
        "release_date": "  2025-05-05 ",
      },
      "expected": {
        "content_id": "cid2",
        "title": "タイトル2",
        "release_date": "2025-05-05",
      },
    },
    {
      "name": "imageURL.listフォールバック",
      "item": {"imageURL": {"list": ["https://example.com/list.jpg"]}},
      "expected": {"package_image_url": "https://example.com/list.jpg"},
    },
    {
      "name": "トップレベルフォールバック",
      "item": {
        "maker": "トップメーカー",
        "label": "トップレーベル",
        "genre": ["トップジャンル"],
        "actress": [{"name": "トップ女優"}],
        "review": {"average": 5, "count": 1},
      },
      "expected": {
        "maker": "トップメーカー",
        "label": "トップレーベル",
        "genres": ["トップジャンル"],
        "actresses": ["トップ女優"],
        "api_review_average": 5,
        "api_review_count": 1,
      },
    },
  ]
  all_pass = True
  for case in cases:
    actual = map_item(case["item"])
    expected = case["expected"]
    ok = all(actual.get(key) == value for key, value in expected.items())
    status = "Pass" if ok else "Fail"
    if not ok:
      all_pass = False
    print(f"[{status}] {case['name']}")
    if not ok:
      print(f"  expected={expected}")
      print("  actual={key: actual.get(key) for key in expected}")
  print("セルフテスト: " + ("全件Pass" if all_pass else "失敗あり"))
  return 0 if all_pass else 1


def main(argv: list[str] | None = None) -> int:
  parser = build_parser()
  args = parser.parse_args(argv)

  if args.selftest:
    return run_selftest()

  api_id = os.environ.get("DMM_API_ID", "")
  affiliate_id = os.environ.get("DMM_AFFILIATE_ID", "")
  if not api_id or not affiliate_id:
    print(".env に DMM_API_ID と DMM_AFFILIATE_ID を追加する必要があります。")
    print(".env はコミットしないでください。")
    return 1

  # works_store は別エージェント作成中のため、ここで遅延インポートする
  from works_store import ensure_work, load_works, now_iso, save_works

  works: dict[str, dict[str, Any]] = load_works()
  inbox = load_inbox()
  targets = select_targets(inbox, args.cid, args.limit)
  timeout_sec = args.timeout / 1000.0

  for cid, inbox_item in targets:
    existing = works.get(cid)
    if (
      not args.refresh
      and existing is not None
      and existing.get("dmm_status") == "done"
      and existing.get("dmm_raw")
    ):
      if inbox_item is not None:
        mark_inbox_enriched(inbox_item, now_iso())
      continue

    work = ensure_work(
      works,
      cid,
      work_url=inbox_item.get("work_url") if inbox_item else None,
    )

    try:
      data = fetch_dmm(api_id, affiliate_id, cid, timeout_sec)
    except Exception as exc:  # noqa: BLE001 - 通信/HTTP/JSONエラーは全て失敗扱い
      error_msg = str(exc) or exc.__class__.__name__
      work["dmm_status"] = "error"
      work["dmm_last_error"] = error_msg
      work["updated_at"] = now_iso()
      save_works(works)
      if inbox_item is not None:
        mark_inbox_failed(inbox_item, args.max_attempts, now_iso())
      time.sleep(args.api_delay)
      continue

    result = data.get("result") if isinstance(data, dict) else None
    items = result.get("items") if isinstance(result, dict) else None
    if not isinstance(items, list) or not items:
      work["dmm_status"] = "not_found"
      work["dmm_fetched_at"] = now_iso()
      work["dmm_last_error"] = "no items"
      work["updated_at"] = now_iso()
      save_works(works)
      if inbox_item is not None:
        mark_inbox_enriched(inbox_item, now_iso())
      print(f"Warning: {cid}: DMM API に項目なし(not_found)")
      time.sleep(args.api_delay)
      continue

    item = items[0]
    mapped = map_item(item)
    work["dmm_raw"] = item
    work["dmm_status"] = "done"
    work["dmm_fetched_at"] = now_iso()
    work["updated_at"] = now_iso()
    work.update(mapped)
    save_works(works)
    if inbox_item is not None:
      mark_inbox_enriched(inbox_item, now_iso())
    time.sleep(args.api_delay)

  write_json_atomic(DATA_PATH, inbox)
  save_works(works)
  print(f"完了: {len(targets)} 件処理")
  return 0


if __name__ == "__main__":
  sys.exit(main())
