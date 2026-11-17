import asyncio, getpass, json, sys
from playwright.async_api import async_playwright

HANDLE = "iurelb"

async def body_text(page):
    try:
        return await page.inner_text("body")
    except Exception:
        return ""

async def main():
    password = getpass.getpass("X password (input is hidden, nothing is saved): ")
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
                # possible 2FA / confirmation-code step: an extra code input appears
                code_inp = page.locator('input[name="text"], input[inputmode="numeric"], input[autocomplete="one-time-code"]')
                needs_code = False
                try:
                    for i in range(await code_inp.count()):
                        el = code_inp.nth(i)
                        if await el.is_visible():
                            nm = await el.get_attribute("name")
                            ac = await el.get_attribute("autocomplete")
                            if ac == "one-time-code" or (nm == "text"):
                                needs_code = True
                                result["code_field"] = {"name": nm, "autocomplete": ac}
                                break
                except Exception:
                    pass
                if needs_code:
                    print("A confirmation code is required. Check the account email / authenticator app.")
                    code = input("Enter the code (leave blank to stop): ").strip()
                    if code:
                        # fill the same element we detected
                        for i in range(await code_inp.count()):
                            el = code_inp.nth(i)
                            if await el.is_visible():
                                await el.fill(code)
                                break
                        # submit: try a Continue/Next/Verify button
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
                    if "/home" in url or "home" in low[:200] or "/i/jf/onboarding" not in url:
                        # logged-in UI markers
                        if await page.locator('a[href="/compose/post"], [data-testid="SideNav_NewTweet_Button"], a[href="/iurelb"]').count() > 0:
                            result["classification"] = "SUCCESS"
                        elif "/home" in url:
                            result["classification"] = "SUCCESS"
                        else:
                            result["classification"] = "UNCERTAIN"
                            result["body_excerpt"] = body[:400].replace("\n", " | ")
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
            result["error"] = ("%s: %s" % (type(e).__name__, e))[:300]
        await browser.close()
    with open("login_result.json", "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(main())
