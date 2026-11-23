#!/usr/bin/env python3
"""FANZA レビュー ページネーション実測（読み取り専用）。

- https://video.dmm.co.jp/av/content/?id=1dandy00914b を開く
- #review へスクロールし、ページ1のレビューカード・総評価数・ページネーションUIを調査
- ページ2・ページ9へ実際に移動し、GraphQL UserReviews リクエストの variables 変化を記録
- context.request.post で API 再送（offset 0,10,...,80）を検証
- ネタバレ開示「レビューを表示する」の有無を調査
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT / "src") not in sys.path:
  sys.path.insert(0, str(PROJECT_ROOT / "src"))

from utils import DEFAULT_USER_AGENT
from utils import PROJECT_ROOT as UTILS_PROJECT_ROOT

PAGE_WAIT_MS = 3000
SCROLL_WAIT_MS = 5000
CLICK_WAIT_MS = 3000
GOTO_TIMEOUT_MS = 30000

GRAPHQL_HOST = "api.video.dmm.co.jp/graphql"
GRAPHQL_URL = "https://api.video.dmm.co.jp/graphql"

CID = "1dandy00914b"
URL = f"https://video.dmm.co.jp/av/content/?id={CID}"

QUERY_OUT = SCRIPT_DIR / "fanza_reviews_query.graphql"
VARS_OUT = SCRIPT_DIR / "fanza_reviews_variables.json"

CARD_SEL = "div.border-t.border-gray-300.w-full.p-4"
PAGE_BTN_SEL = 'button:has-text("1"), button:has-text("2"), button:has-text("3"), button:has-text("4"), button:has-text("5"), button:has-text("6"), button:has-text("7"), button:has-text("8"), button:has-text("9")'


def filter_headers(headers: dict[str, str]) -> dict[str, str]:
  out: dict[str, str] = {}
  for k, v in headers.items():
    kl = k.lower()
    if kl in ("cookie",) or kl.startswith("set-cookie"):
      continue
    out[k] = v
  return out


def first_text(page, selectors: list[str]) -> str:
  for sel in selectors:
    try:
      el = page.query_selector(sel)
      if el:
        t = (el.inner_text() or "").strip()
        if t:
          return t
    except Exception:
      continue
  return ""


def find_page_button(page, label: str):
  """指定ラベルのページボタンを返す。見つからなければ None。"""
  try:
    btns = page.query_selector_all('button:has-text("' + label + '")')
  except Exception:
    return None
  for b in btns:
    t = (b.inner_text() or "").strip()
    if t == label:
      return b
  return None


def main() -> int:
  captured_requests: list[dict[str, Any]] = []

  with sync_playwright() as p:
    context = None
    try:
      context = p.chromium.launch_persistent_context(
        user_data_dir=".browser-data",
        headless=True,
        user_agent=DEFAULT_USER_AGENT,
      )
      try:
        context.add_cookies(
          [
            {
              "name": "age_check_done",
              "value": "1",
              "domain": ".dmm.co.jp",
              "path": "/",
              "secure": True,
              "sameSite": "Lax",
              "expires": 1893456000.0,
            }
          ]
        )
      except Exception as e:  # noqa: BLE001
        print(f"cookie error: {e}")

      page = context.new_page()

      def on_request(req) -> None:
        try:
          u = req.url
        except Exception:
          return
        if GRAPHQL_HOST not in u:
          return
        try:
          pd = json.loads(req.post_data) if req.post_data else {}
        except Exception:
          pd = {}
        captured_requests.append(
          {
            "url": u,
            "method": req.method,
            "headers": filter_headers(dict(req.headers)),
            "post_data": pd,
          }
        )

      page.on("request", on_request)

      response = page.goto(URL, timeout=GOTO_TIMEOUT_MS, wait_until="domcontentloaded")
      print(f"Status: {response.status if response else None}")
      print(f"Final URL: {page.url}")
      page.wait_for_timeout(PAGE_WAIT_MS)

      # #review へスクロール
      section = page.query_selector("#review")
      if section is not None:
        try:
          section.scroll_into_view_if_needed()
          print("scrolled to #review")
        except Exception:  # noqa: BLE001
          page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
          print("scrolled to bottom")
      else:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        print("scrolled to bottom (no #review)")
      page.wait_for_timeout(SCROLL_WAIT_MS)

      # ---------- ページ1 ----------
      print("\n=== Page 1 ===")
      total_text = first_text(
        page,
        [
          "#review ~ *:text(/レビュー/)",
          "text=/[0-9,]+件/",
          ".review-total",
          "#review + *",
        ],
      )
      print(f"総評価数テキスト: {total_text[:80]}")

      cards = page.query_selector_all(CARD_SEL)
      print(f"カード数: {len(cards)}")

      has_body = 0
      stars_only = 0
      service_video = 0
      service_goods = 0
      for c in cards:
        txt = c.inner_text() or ""
        if "コメント" in txt or len(txt) > 60:
          has_body += 1
        else:
          stars_only += 1
        if "ビデオ(動画)" in txt:
          service_video += 1
        elif "ビデオ(物販)" in txt:
          service_goods += 1
      print(f"本文あり: {has_body}, 星のみ: {stars_only}")
      print(f"サービス: ビデオ(動画)={service_video}, ビデオ(物販)={service_goods}")

      # ページネーションUI
      print("\n--- ページネーションUI ---")
      page_labels: list[str] = []
      btn_class = ""
      for label in [str(i) for i in range(1, 10)] + ["次へ", "前へ"]:
        b = find_page_button(page, label)
        if b:
          page_labels.append(label)
          if not btn_class:
            btn_class = b.get_attribute("class") or ""
      total_pages = max([int(x) for x in page_labels if x.isdigit()], default=0)
      print(f"ページ番号: {page_labels}")
      print(f"総ページ数(UI): {total_pages}")
      print(
        f"ボタンタグ: BUTTON, セレクタ: button:has-text(<n>), クラス例: {btn_class[:60]}"
      )

      # UserReviews クエリ・variables をページ1から取得
      user_reviews = None
      for r in captured_requests:
        pd = r["post_data"]
        if (
          isinstance(pd, dict)
          and pd.get("operationName") == "UserReviews"
          and pd.get("variables", {}).get("id") == CID
        ):
          user_reviews = r
          break

      page1_vars = None
      page1_query = None
      page1_op = None
      page1_headers = {}
      if user_reviews:
        page1_vars = user_reviews["post_data"].get("variables")
        page1_query = user_reviews["post_data"].get("query")
        page1_op = user_reviews["post_data"].get("operationName")
        page1_headers = user_reviews["headers"]
      print(f"page1 variables: {json.dumps(page1_vars, ensure_ascii=False)}")
      print(f"page1 operationName: {page1_op}")
      print(f"page1 headers keys: {list(page1_headers.keys())}")

      # ---------- ページ2 ----------
      print("\n=== Page 2 ===")
      b2 = find_page_button(page, "2")
      if b2:
        try:
          b2.click()
          print("clicked page 2")
        except Exception as e:  # noqa: BLE001
          print(f"click page2 failed: {e}")
      else:
        print("page 2 button not found")
      page.wait_for_timeout(CLICK_WAIT_MS)

      page2_vars = None
      for r in captured_requests:
        pd = r["post_data"]
        if (
          isinstance(pd, dict)
          and pd.get("operationName") == "UserReviews"
          and pd.get("variables", {}).get("id") == CID
        ):
          v = pd.get("variables")
          if v != page1_vars:
            page2_vars = v
            break
      print(f"page2 variables: {json.dumps(page2_vars, ensure_ascii=False)}")

      # ---------- ページ9 ----------
      print("\n=== Page 9 ===")
      b9 = find_page_button(page, "9")
      if b9:
        try:
          b9.click()
          print("clicked page 9")
        except Exception as e:  # noqa: BLE001
          print(f"click page9 failed: {e}")
      else:
        print("page 9 button not found")
      page.wait_for_timeout(CLICK_WAIT_MS)

      page9_vars = None
      page9_cards = 0
      for r in reversed(captured_requests):
        pd = r["post_data"]
        if (
          isinstance(pd, dict)
          and pd.get("operationName") == "UserReviews"
          and pd.get("variables", {}).get("id") == CID
        ):
          v = pd.get("variables")
          if v != page1_vars and v != page2_vars:
            page9_vars = v
            break
      print(f"page9 variables: {json.dumps(page9_vars, ensure_ascii=False)}")
      cards9 = page.query_selector_all(CARD_SEL)
      page9_cards = len(cards9)
      print(f"page9 カード数: {page9_cards} (page1={len(cards)})")

      # クエリ保存（UserReviews のみ）
      if page1_query:
        QUERY_OUT.write_text(page1_query, encoding="utf-8")
        print(f"\nquery saved -> {QUERY_OUT}")
      if page1_vars:
        VARS_OUT.write_text(
          json.dumps(page1_vars, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"variables saved -> {VARS_OUT}")

      # ---------- ネタバレ開示 ----------
      print("\n--- ネタバレ開示 ---")
      spoiler = find_page_button(page, "レビューを表示する")
      print(f"ネタバレ開示: {'あり' if spoiler else 'なし'}")
      spoiler_sel = None
      if spoiler:
        spoiler_sel = spoiler.get_attribute("class") or ""
        print(
          f"セレクタ: button:has-text('レビューを表示する') class={spoiler_sel[:60]}"
        )

      # ---------- API 再送 ----------
      print("\n=== API 再送 (offset 0,10,...,80) ===")
      base_headers = {
        "content-type": "application/json",
        "accept": "application/graphql-response+json, application/json",
        "origin": "https://video.dmm.co.jp",
        "referer": URL,
      }
      base_headers.update(page1_headers)
      # ブラウザ cookie を付与
      cookies = context.cookies()
      cookie_header = "; ".join(
        f"{c['name']}={c['value']}" for c in cookies if c["name"] == "age_check_done"
      )
      if cookie_header:
        base_headers["cookie"] = cookie_header

      query_text = page1_query or QUERY_OUT.read_text(encoding="utf-8")
      op_name = page1_op or "UserReviews"

      all_unique: set[str] = set()
      total_items = 0
      empty_pages = 0
      has_page_info = False
      has_total_count = False
      has_has_next = False

      for offset in range(0, 90, 10):
        payload = {
          "query": query_text,
          "variables": {"id": CID, "offset": offset, "sort": "HELPFUL_COUNT_DESC"},
          "operationName": op_name,
        }
        resp = None
        try:
          resp = context.request.post(
            GRAPHQL_URL, headers=base_headers, data=json.dumps(payload)
          )
        except Exception as e:  # noqa: BLE001
          print(f"  offset={offset}: request error {type(e).__name__}: {e}")
          continue
        status = resp.status
        try:
          data = resp.json()
        except Exception:
          print(f"  offset={offset}: status={status} non-JSON")
          continue
        if "errors" in data:
          print(
            f"  offset={offset}: status={status} GraphQL errors "
            f"{json.dumps(data['errors'], ensure_ascii=False)[:200]}"
          )
          continue
        reviews = data.get("data", {}).get("reviews", {})
        if isinstance(reviews, dict):
          if "pageInfo" in reviews:
            has_page_info = True
          if "totalCount" in reviews:
            has_total_count = True
          if "hasNextPage" in reviews:
            has_has_next = True
        items = reviews.get("items", []) if isinstance(reviews, dict) else []
        n = len(items)
        total_items += n
        ids = [it.get("id") for it in items if isinstance(it, dict) and it.get("id")]
        new_ids = [i for i in ids if i not in all_unique]
        for i in ids:
          all_unique.add(i)
        if n == 0:
          empty_pages += 1
        print(
          f"  offset={offset}: status={status} items={n} unique_new={len(new_ids)} "
          f"cumulative_unique={len(all_unique)}"
        )

      print(
        f"\n合計 items: {total_items}, ユニーク合計: {len(all_unique)}, 空ページ: {empty_pages}"
      )
      print(
        f"pageInfo: {has_page_info}, totalCount: {has_total_count}, hasNextPage: {has_has_next}"
      )
      can_reproduce = (len(all_unique) >= 90 and empty_pages == 0) and total_pages == 9
      print(f"APIだけで全9ページ再現: {'Yes' if can_reproduce else 'No'}")

      try:
        page.close()
      except Exception:  # noqa: BLE001
        pass
      return 0
    except Exception as e:  # noqa: BLE001
      print(f"Error: {type(e).__name__}: {e}")
      return 1
    finally:
      if context is not None:
        context.close()


if __name__ == "__main__":
  import sys as _sys

  _sys.exit(main())
