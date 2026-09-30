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
