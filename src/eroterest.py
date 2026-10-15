import json
import os

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


def main():
  with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
      user_data_dir=".browser-data",
      headless=True,
      user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    page = context.new_page()
    try:
      response = page.goto(
        "https://movie.eroterest.net/?word=%E7%86%9F%E5%A5%B3&c=&page=1",
        timeout=30000,
        wait_until="domcontentloaded",
      )
      print(f"Status: {response.status if response else 'No response'}")
      print(f"URL: {page.url}")

      # .listWrapper 内の /page/ リンクを重複排除して収集
      urls = page.evaluate("""() => {
        const links = Array.from(document.querySelectorAll('.listWrapper a[href*="/page/"]'));
        return Array.from(new Set(links.map(a => a.href)));
      }""")
      print(f"Extracted URLs count: {len(urls)}")
      for u in urls[:5]:
        print(f"  {u}")

      # データ保存（JSON形式、重複排除済み、scraped: false 付与）
      os.makedirs("data", exist_ok=True)
      data_path = "data/eroterest-page.json"
      existing = {}
      if os.path.exists(data_path):
        try:
          with open(data_path, "r", encoding="utf-8") as f:
            existing_data = json.load(f)
            existing = {
              item.get("url"): item.get("scraped", False)
              for item in existing_data
              if isinstance(item, dict)
            }
        except json.JSONDecodeError, OSError:
          existing = {}
      result = [{"url": u, "scraped": existing.get(u, False)} for u in urls]
      with open(data_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
      print(f"Saved {len(result)} URLs to {data_path}")

      page.screenshot(path="screenshot/screenshot.png", full_page=True)
      print("Screenshot saved to screenshot/screenshot.png")
    except PlaywrightError as e:
      print(f"Failed to open page: {e}")
      page.screenshot(path="screenshot/error_screenshot.png", full_page=True)
      print("Error screenshot saved to screenshot/error_screenshot.png")
    finally:
      context.close()


if __name__ == "__main__":
  main()
