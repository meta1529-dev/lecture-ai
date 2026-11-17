import asyncio, getpass, json
from playwright.async_api import async_playwright

HANDLE = "iurelb"

async def body_text(page):
    try:
        return await page.inner_text("body")
    except Exception:
        return ""

async def wait_password_editable(page, timeout_ms=20000):
    await page.wait_for_function(
        """() => { const el = document.querySelector('input[name="password"]');
                   return el && !el.hasAttribute('inert') && !el.disabled; }""",
        timeout=timeout_ms)

async def main():
    raw = getpass.getpass("X password (input is hidden, nothing is saved): ")
    password = "".join(c for c in raw if 32 <= ord(c) < 127)
    raw = ""
    result = {"classification": "UNKNOWN"}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
        ctx = await browser.new_context(locale="en-US")
        page = await ctx.new_page()
        try:
            await page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(6000)
            user_inp = page.locator('input[name="username_or_email"]').first
            await user_inp.wait_for(state="visible", timeout=30000)
            await user_inp.fill(HANDLE)
            await page.wait_for_timeout(1500)
            # The password field starts out inert; it activates after the username step.
            try:
                await wait_password_editable(page, 15000)
            except Exception:
                await page.get_by_role("button", name="Continue", exact=True).click(timeout=10000)
                await page.wait_for_timeout(5000)
                await wait_password_editable(page, 20000)
            pw_inp = page.locator('input[name="password"]').first
            await pw_inp.fill(password)
            password = ""
            await page.get_by_role("button", name="Continue", exact=True).click(timeout=10000)
            await page.wait_for_timeout(9000)
            body = await body_text(page)
            url = page.url
            result["url_after_submit"] = url
            low = body.lower()
            if "verify you are human" in low or "arkose" in low or "captcha" in low:
                result["classification"] = "HUMAN_CHECK"
            elif "wrong password" in low or "incorrect password" in low or "didn't match" in low:
                result["classification"] = "WRONG_PASSWORD"
            elif "問題が発生" in body or "something went wrong" in low:
                result["classification"] = "GENERIC_ERROR"
            else:
                code_inp = page.locator('input[autocomplete="one-time-code"]')
                needs_code = False
                try:
                    if await code_inp.count() > 0 and await code_inp.first.is_visible():
                        needs_code = True
                except Exception:
                    pass
                if needs_code:
                    print("A confirmation code is required. Check the account email / authenticator app.")
                    code = input("Enter the code (leave blank to stop): ").strip()
                    if code:
                        await code_inp.first.fill(code)
                        for name in ("Continue", "Next", "Verify", "次へ", "確認"):
                            try:
                                await page.get_by_role("button", name=name, exact=True).click(timeout=3000)
                                break
                            except Exception:
                                pass
                        await page.wait_for_timeout(9000)
                        body = await body_text(page)
                        url = page.url
                        result["url_after_code"] = url
                        low = body.lower()
                    else:
                        result["classification"] = "TWO_FACTOR_SKIPPED"
                if result["classification"] == "UNKNOWN":
                    if "/home" in url or await page.locator('[data-testid="SideNav_NewTweet_Button"], a[href="/compose/post"]').count() > 0:
                        result["classification"] = "SUCCESS"
                    else:
                        result["classification"] = "UNCERTAIN"
                        result["body_excerpt"] = body[:400].replace("\n", " | ")
            if result["classification"] == "SUCCESS":
                await ctx.storage_state(path="session.json")
                result["session_saved"] = "session.json"
            try:
                await page.screenshot(path="login_final.png")
            except Exception:
                pass
        except Exception as e:
            # Never record the exception message: Playwright errors can embed typed values.
            result["error"] = type(e).__name__
        await browser.close()
    with open("login_result.json", "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(main())
