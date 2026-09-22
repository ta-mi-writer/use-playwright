import os

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", ".browsers")


def main():
  # Headless for VPS; persistent context saves cookies/session.
  with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
      user_data_dir=".browser-data",
      headless=True,
      args=["--disable-blink-features=AutomationControlled"],
    )
    page = context.new_page()
    page.set_extra_http_headers(
      {
        "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
          " like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
      }
    )
    page.goto("https://ooxxx.com/")

    # Age gate / cookies handled
    enter_button = page.get_by_role("button", name="Enter")
    if enter_button.is_visible(timeout=1000):
      enter_button.click()
      print("Clicked age gate 'Enter' button")
    else:
      print("Age gate already passed or not present")

    # Click 'Sign in' link
    try:
      page.get_by_role("link", name="Sign in").click(timeout=5000)
      print("Clicked 'Sign in' link")
    except PlaywrightError as e:
      print(f"Failed to click 'Sign in': {e}")

    username = os.environ.get("OOXXX_USERNAME")
    password = os.environ.get("OOXXX_PASSWORD")
    assert username is not None, "OOXXX_USERNAME not set"
    assert password is not None, "OOXXX_PASSWORD not set"
    print(f"DEBUG: username={username!r}, password={'***' if password else None!r}")

    username_box = page.get_by_role("textbox", name="Username or Email")
    print(f"DEBUG: username_box count={username_box.count()}")
    username_box.click()
    username_box.type(username, delay=100)
    print(f"DEBUG: typed username={username!r}")

    password_box = page.get_by_role("textbox", name="Password")
    print(f"DEBUG: password_box count={password_box.count()}")
    password_box.click()
    password_box.type(password, delay=100)
    print(f"DEBUG: typed password={'***' if password else None!r}")
    username_val = username_box.evaluate("el => el.value")
    password_val = password_box.evaluate("el => el.value")
    print(
      f"DEBUG: DOM value username={username_val!r}, password={'***' if password_val else None!r}"
    )
    print("Filled username and password (click + type with delay).")

    def handle_request(req):
      url = req.url
      print(f"REQ: {req.method} {url}")
      if "api/login.php" in url and req.method == "POST" and req.post_data:
        print(f"LOGIN POST DATA: {req.post_data}")

    page.on("request", handle_request)

    def handle_response(res):
      url = res.url
      print(f"RES: {res.status} {url}")
      if "api/login.php" in url:
        try:
          text = res.text()
          print(f"LOGIN RESPONSE BODY: {text}")
        except Exception as e:  # noqa: BLE001
          print(f"Failed to read login response body: {e}")

    page.on("response", handle_response)

    page.get_by_role("button", name="Sign in").click()
    print("Clicked 'Sign in' submit button.")

    try:
      page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:  # noqa: BLE001
      print("Load state timeout exceeded, continuing.")

    page.screenshot(path="screenshot_post_login.png")
    context.close()
    print("Screenshot saved. Login handled (headless=True).")


if __name__ == "__main__":
  main()
