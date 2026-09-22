from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


def main():
  with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    # 一般的なブラウザに見せるためのUser-Agentを設定
    context = browser.new_context(
      user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    try:
      response = page.goto(
        "https://movie.eroterest.net/popular/",
        timeout=30000,
        wait_until="domcontentloaded",
      )
      print(f"Status: {response.status if response else 'No response'}")
      print(f"URL: {page.url}")
      page.screenshot(path="screenshot.png", full_page=True)
      print("Screenshot saved to screenshot.png")
    except PlaywrightError as e:
      print(f"Failed to open page: {e}")
      page.screenshot(path="error_screenshot.png", full_page=True)
      print("Error screenshot saved to error_screenshot.png")
    finally:
      browser.close()


if __name__ == "__main__":
  main()
