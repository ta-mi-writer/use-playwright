"""FANZA 作品ページからレビューを取得し works.json に保存する。

受信箱 data/eroterest-page.json の status=="links_done" 項目を処理し、
FANZA の GraphQL UserReviews API から全ページのレビューを回収して
works.json に保存する。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from typing import Any, cast

from playwright.sync_api import sync_playwright

from utils import (
  DATA_DIR,
  DEFAULT_USER_AGENT,
  PROJECT_ROOT,
  SCREENSHOT_DIR,
  write_json_atomic,
)
from works_store import ensure_work, load_works, now_iso, save_works

DATA_PATH = DATA_DIR / "eroterest-page.json"

PAGE_DELAY_JITTER = 0.5
GRAPHQL_URL = "https://api.video.dmm.co.jp/graphql"
REVIEW_SORT = "HELPFUL_COUNT_DESC"

REVIEW_QUERY = """query UserReviews($id: ID!, $sort: ReviewSort!, $offset: Int!) {
  reviews(contentId: $id, sort: $sort, limit: 100, offset: $offset) {
    items {
      id
      title
      rating
      reviewerId
      nickname
      isPurchased
      comment
      helpfulCount
      service
      isExposure
      publishDate
      __typename
    }
    __typename
  }
}"""

BASE_HEADERS = {
  "content-type": "application/json",
  "accept": "application/graphql-response+json, application/graphql+json, application/json, text/event-stream, multipart/mixed",
  "accept-language": "en-US",
  "fanza-device": "BROWSER",
  "origin": "https://video.dmm.co.jp",
}

SERVICE_LABEL_MAP = {
  "VIDEO": "ビデオ(動画)",
  "MONO": "ビデオ(物販)",
}

AGE_COOKIE = {
  "name": "age_check_done",
  "value": "1",
  "domain": ".dmm.co.jp",
  "path": "/",
  "secure": True,
  "sameSite": "Lax",
  "expires": 1893456000.0,  # 2030-01-01
}

# #review 内から総評価数・平均点を抽出するためのJS
EXTRACT_META_JS = r"""() => {
  const section = document.querySelector('#review');
  if (!section) {
    return { total: null, average: null };
  }
  const text = section.textContent || "";
  let total = null;
  const mTotal = text.match(/総評価数\s*(\d{1,6})/);
  if (mTotal) total = parseInt(mTotal[1], 10);
  if (total === null) {
    const a = document.querySelector('a[href="#review"]');
    if (a) {
      const m = a.textContent.match(/\((\d{1,6})\)/);
      if (m) total = parseInt(m[1], 10);
    }
  }
  let average = null;
  const mAvg = text.match(/(\d+\.\d+)\s*点/);
  if (mAvg) average = parseFloat(mAvg[1]);
  return { total, average };
}"""


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description="FANZA 作品ページからレビューを取得し works.json に保存する"
  )
  parser.add_argument(
    "--timeout", type=int, default=30000, help="ページ読み込みタイムアウト(ms)"
  )
  parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="User-Agent")
  parser.add_argument(
    "--limit", type=int, default=0, help="処理する最大件数（0以下で無制限）"
  )
  parser.add_argument(
    "--cid", action="append", default=None, help="処理対象CIDを絞り込む（複数指定可）"
  )
  parser.add_argument(
    "--work-url", default=None, help="作品URL（--cidと併用、単体検証用）"
  )
  parser.add_argument(
    "--refresh", action="store_true", help="reviews_status=done でも再取得する"
  )
  parser.add_argument(
    "--page-delay", type=float, default=2.0, help="アイテム間待機秒（0以上）"
  )
  parser.add_argument(
    "--page-size",
    type=int,
    default=100,
    help="GraphQL 1ページあたり取得件数（既定100）",
  )
  parser.add_argument(
    "--api-delay", type=float, default=0.3, help="GraphQL ページ間待機秒（既定0.3）"
  )
  parser.add_argument(
    "--max-review-pages",
    type=int,
    default=50,
    help="レビュー取得の最大ページ数（既定50）",
  )
  parser.add_argument(
    "--review-wait", type=int, default=10000, help="レビュー星画像出現待ち(ms)"
  )
  parser.add_argument(
    "--max-attempts", type=int, default=3, help="1アイテムあたりの最大試行回数"
  )
  args = parser.parse_args()
  if args.page_delay < 0:
    parser.error("--page-delay は 0 以上を指定してください")
  if args.review_wait < 0:
    parser.error("--review-wait は 0 以上を指定してください")
  if args.work_url is not None and not args.cid:
    parser.error("--work-url は --cid と併用してください")
  if args.page_size <= 0:
    parser.error("--page-size は 1 以上を指定してください")
  if args.api_delay < 0:
    parser.error("--api-delay は 0 以上を指定してください")
  if args.max_review_pages <= 0:
    parser.error("--max-review-pages は 1 以上を指定してください")
  return args


def work_url_for(cid: str, item: dict[str, Any] | None) -> str:
  if item:
    url = item.get("work_url")
    if isinstance(url, str) and url:
      return url
  return f"https://www.dmm.co.jp/digital/videoa/-/detail/=/cid={cid}/"


def normalize_posted_at(raw: Any) -> str:
  """publishDate を YYYY-MM-DD に正規化（ISO/スラッシュ両対応）。"""
  if not isinstance(raw, str) or not raw:
    return ""
  m = re.match(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})", raw)
  if m:
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
  m = re.match(r"\s*(\d{4})/(\d{1,2})/(\d{1,2})", raw)
  if m:
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
  return ""


def service_label(service: Any) -> str | None:
  if service in SERVICE_LABEL_MAP:
    return SERVICE_LABEL_MAP[service]
  return service


def map_review(it: dict[str, Any], cid: str) -> dict[str, Any]:
  """GraphQL の reviews.items 要素をレビュー辞書にマッピングする。"""
  body = (it.get("comment") or "").strip()
  rating = it.get("rating")
  if isinstance(rating, bool) or not isinstance(rating, (int, float)):
    rating = None
  reviewer_id = it.get("reviewerId") or ""
  rid = it.get("id") or ""
  if not rid:
    rid = hashlib.sha1(
      f"{cid}|{it.get('publishDate') or ''}|{it.get('nickname') or ''}|{body}".encode()
    ).hexdigest()
  return {
    "id": rid,
    "cid": cid,
    "title": (it.get("title") or "").strip(),
    "rating": rating,
    "body": body,
    "has_comment": bool(body),
    "reviewer": (it.get("nickname") or "").strip(),
    "reviewer_id": reviewer_id,
    "posted_at": normalize_posted_at(it.get("publishDate")),
    "service": it.get("service"),
    "service_label": service_label(it.get("service")),
    "is_purchased": it.get("isPurchased"),
    "is_exposure": it.get("isExposure"),
    "helpful_count": it.get("helpfulCount"),
    "source_url": f"https://review.dmm.co.jp/review-front/reviewer/list/{reviewer_id}"
    if reviewer_id
    else "",
  }


def fetch_reviews(
  page, context, work_url: str, cid: str, args: argparse.Namespace
) -> dict[str, Any]:
  """1 作品のレビューを GraphQL UserReviews で全ページ取得する。"""
  referer = f"https://video.dmm.co.jp/av/content/?id={cid}"
  headers = dict(BASE_HEADERS)
  headers["referer"] = referer

  # 年齢確認クッキーをヘッダにも付与（context の jar と合わせて送信）
  cookies = context.cookies()
  cookie_header = "; ".join(
    f"{c['name']}={c['value']}" for c in cookies if c["name"] == "age_check_done"
  )
  if cookie_header:
    headers["cookie"] = cookie_header

  # 1. 作品ページを読み込み、DOM から総評価数・平均点を抽出
  page.goto(work_url, timeout=args.timeout, wait_until="domcontentloaded")

  try:
    title = page.title()
  except Exception:  # noqa: BLE001
    title = ""
  if "age_check" in page.url.lower() or "年齢認証" in title:
    for sel in ['a:has-text("はい")', 'input[type="image"][name="commit"]']:
      loc = page.query_selector(sel)
      if loc is not None:
        try:
          loc.click()
        except Exception:  # noqa: BLE001, S112
          continue
        break
    page.wait_for_timeout(2000)

  # #review へスクロールしてDOMを描画させる
  try:
    page.wait_for_selector("#review", timeout=args.timeout)
  except Exception:  # noqa: BLE001, S110
    pass
  try:
    page.evaluate(
      "() => { const el = document.querySelector('#review'); if (el) el.scrollIntoView(); }"
    )
  except Exception:  # noqa: BLE001, S110
    pass
  page.wait_for_timeout(1000)

  meta = page.evaluate(EXTRACT_META_JS) or {}
  total = meta.get("total")
  average = meta.get("average")
  source_url = page.url

  # 2. GraphQL ページング（offset を 100 ずつ増やして全件回収）
  collected: list[dict[str, Any]] = []
  seen_ids: set[str] = set()
  offset = 0
  pages_fetched = 0
  complete = False

  for page_idx in range(args.max_review_pages):
    payload = {
      "query": REVIEW_QUERY,
      "variables": {"id": cid, "sort": REVIEW_SORT, "offset": offset},
      "operationName": "UserReviews",
    }
    try:
      resp = context.request.post(
        GRAPHQL_URL, headers=headers, data=json.dumps(payload)
      )
    except Exception as e:
      raise RuntimeError(f"GraphQL request failed: {e}") from e
    try:
      data = resp.json()
    except Exception as e:
      raise RuntimeError(
        f"GraphQL non-JSON response (status={getattr(resp, 'status', '?')}): {e}"
      ) from e
    pages_fetched += 1

    if "errors" in data:
      raise RuntimeError(
        "GraphQL errors: " + json.dumps(data["errors"], ensure_ascii=False)[:300]
      )

    reviews_data = data.get("data", {}).get("reviews")
    if not isinstance(reviews_data, dict):
      break
    items = reviews_data.get("items")
    if not isinstance(items, list):
      break

    for it in items:
      if not isinstance(it, dict):
        continue
      r = map_review(it, cid)
      rid = r["id"]
      if rid not in seen_ids:
        seen_ids.add(rid)
        collected.append(r)

    if not items or len(items) < args.page_size:
      complete = True
      break
    offset += args.page_size
    if page_idx < args.max_review_pages - 1:
      page.wait_for_timeout(int(args.api_delay * 1000))

  with_comment = sum(1 for r in collected if r.get("has_comment"))
  if total is None:
    rating_only = None
  else:
    rating_only = max(0, int(total) - with_comment)

  return {
    "reviews": collected,
    "total": total,
    "average": average,
    "with_comment": with_comment,
    "rating_only": rating_only,
    "complete": complete,
    "source_url": source_url,
    "pages_fetched": pages_fetched,
  }


def _select_targets(
  items: list[dict[str, Any]], cids: list[str] | None, work_url_override: str | None
) -> list[tuple[str, dict[str, Any] | None, str]]:
  targets: list[tuple[str, dict[str, Any] | None, str]] = []
  seen_cids: set[str] = set()
  for item in items:
    if not isinstance(item, dict):
      continue
    if item.get("status") != "links_done":
      continue
    cid = item.get("cid")
    if not isinstance(cid, str):
      continue
    if cids and cid not in cids:
      continue
    if cid in seen_cids:
      continue
    seen_cids.add(cid)
    work_url = work_url_override or work_url_for(cid, item)
    targets.append((cid, item, work_url))
  return targets


def _page_delay(page, args: argparse.Namespace) -> None:
  delay_sec = max(
    0.0, args.page_delay + random.uniform(-PAGE_DELAY_JITTER, PAGE_DELAY_JITTER)
  )
  page.wait_for_timeout(int(delay_sec * 1000))
  print(f"wait {delay_sec:.1f}s")


def main() -> int:
  args = parse_args()
  SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

  if not DATA_PATH.exists():
    print(f"Error: 受信箱が見つかりません: {DATA_PATH}")
    return 1
  try:
    with DATA_PATH.open("r", encoding="utf-8") as f:
      items = json.load(f)
  except (json.JSONDecodeError, OSError) as e:
    print(f"Error: 受信箱を読み込めませんでした: {e}")
    return 1
  if not isinstance(items, list):
    print("Error: 受信箱の形式が不正です（リストである必要があります）。")
    return 1

  # 受信箱から対象を選択（--cid で絞り込み）
  targets = _select_targets(items, args.cid, args.work_url)

  # --work-url で受信箱に無い CID を単体検証対象に追加
  if args.work_url and args.cid:
    in_cids = {t[0] for t in targets}
    for cid in args.cid:
      if cid not in in_cids:
        targets.append((cid, None, args.work_url))

  if not targets:
    print("処理対象の作品は存在しません。")
    return 0

  print(f"処理対象の作品件数: {len(targets)}")

  works = load_works()
  status_counter: dict[str, int] = {}
  processed = 0

  try:
    with sync_playwright() as p:
      context = None
      page = None
      try:
        context = p.chromium.launch_persistent_context(
          user_data_dir=str(PROJECT_ROOT / ".browser-data"),
          headless=True,
          user_agent=args.user_agent,
        )
        try:
          context.add_cookies(cast(list[Any], [AGE_COOKIE]))
        except Exception as e:  # noqa: BLE001
          print(f"Warning: 年齢確認クッキーの設定に失敗しました: {e}")
        page = context.new_page()

        for pos, (cid, item, work_url) in enumerate(targets):
          if args.limit > 0 and processed >= args.limit:
            print(f"指定された制限件数 ({args.limit}件) に達したため終了します。")
            break

          now = now_iso()

          # すでに完了済みならネットワークせず受信箱だけ更新
          existing = works.get(cid)
          if (
            existing
            and existing.get("reviews_status") == "done"
            and existing.get("reviews")
            and not args.refresh
          ):
            if item is not None:
              item["status"] = "reviews_done"
              item["attempts"] = 0
              item["reviews_count"] = len(existing.get("reviews", []))
              item.pop("last_error", None)
              item.pop("stage", None)
              item["updated_at"] = now
              write_json_atomic(DATA_PATH, items)
            processed += 1
            status_counter["reviews_done"] = status_counter.get("reviews_done", 0) + 1
            existing_reviews = len(existing.get("reviews", []))
            print(
              f"{cid}: skip(already done) reviews={existing_reviews} status=reviews_done"
            )
            is_last = pos == len(targets) - 1
            limit_reached = args.limit > 0 and processed >= args.limit
            if not is_last and not limit_reached:
              _page_delay(page, args)
            continue

          work = ensure_work(works, cid, work_url)
          try:
            result = fetch_reviews(page, context, work_url, cid, args)
            reviews = result["reviews"]
            with_comment = result["with_comment"]
            rating_only = result["rating_only"]
            total = result["total"]

            work["reviews"] = reviews
            work["reviews_total"] = total
            work["reviews_average"] = result["average"]
            work["reviews_fetched"] = len(reviews)
            work["reviews_with_comment"] = with_comment
            work["reviews_rating_only"] = rating_only
            work["reviews_complete"] = result["complete"]
            work["reviews_pages_fetched"] = result["pages_fetched"]
            work["reviews_status"] = "done"
            work["reviews_fetched_at"] = now
            work["reviews_source_url"] = result["source_url"]
            work["work_url"] = work_url
            work["updated_at"] = now
            save_works(works)

            if item is not None:
              item["status"] = "reviews_done"
              item["attempts"] = 0
              item["reviews_count"] = len(reviews)
              item["reviews_with_comment"] = with_comment
              item["reviews_rating_only"] = rating_only
              item.pop("last_error", None)
              item.pop("stage", None)
              item["updated_at"] = now
              write_json_atomic(DATA_PATH, items)

            processed += 1
            status_counter["reviews_done"] = status_counter.get("reviews_done", 0) + 1
            print(
              f"{cid}: reviews={len(reviews)} total={total} "
              f"with_comment={with_comment} rating_only={rating_only} "
              f"complete={result['complete']} pages={result['pages_fetched']} status=reviews_done"
            )
          except Exception as e:  # noqa: BLE001 - 想定外例外もステータス化して継続
            if item is not None:
              attempts = int(item.get("attempts", 0)) + 1
              item["attempts"] = attempts
              item["status"] = "dead" if attempts >= args.max_attempts else "error"
              item["last_error"] = str(e)
              item["stage"] = "s3"
              item["updated_at"] = now
              write_json_atomic(DATA_PATH, items)

            if work is not None:
              work["reviews_status"] = "error"
              work["reviews_last_error"] = str(e)
              work["updated_at"] = now
              save_works(works)

            try:
              page.screenshot(
                path=str(SCREENSHOT_DIR / f"fanza_{cid}_error.png"), full_page=True
              )
            except Exception:  # noqa: BLE001, S110
              pass

            processed += 1
            fail_status = item["status"] if item is not None else "error"
            status_counter[fail_status] = status_counter.get(fail_status, 0) + 1
            print(
              f"{cid}: status={fail_status} error={e} stage=s3 "
              f"attempts={item.get('attempts') if item is not None else 'n/a'}"
            )

          is_last = pos == len(targets) - 1
          limit_reached = args.limit > 0 and processed >= args.limit
          if not is_last and not limit_reached:
            _page_delay(page, args)

        print("\n=== 結果サマリー ===")
        for status, count in sorted(status_counter.items()):
          print(f"{status}: {count}")
        print(f"処理件数: {processed}")

        if page is not None:
          try:
            page.close()
          except Exception:  # noqa: BLE001, S110
            pass
        return 0
      finally:
        if context is not None:
          context.close()
  except Exception as e:  # noqa: BLE001
    print(f"Failed to run browser: {e}")
    return 1


if __name__ == "__main__":
  sys.exit(main())
