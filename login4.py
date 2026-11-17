import asyncio, getpass, json, sys
from playwright.async_api import async_playwright

HANDLE = sys.argv[1] if len(sys.argv) > 1 else "iurelb"
STAGES = []

def stage(name):
    STAGES.append(name)
    print("STAGE:", name, flush=True)

async def body_text(page):
    try:
        return await page.inner_text("body")
    except Exception:
        return ""

async def pw_editable(page):
    try:
        return await page.evaluate("""() => { const el = document.querySelector('input[name="password"]'); return !!el && !el.hasAttribute('inert') && !el.disabled; }""")
    except Exception:
        return False

async def main():
    raw = getpass.getpass("X password (input is hidden, nothing is saved): ")
    password = "".join(c for c in raw if 32 <= ord(c) < 127)
    raw = ""
    result = {"classification": "UNKNOWN", "handle": HANDLE, "stages": STAGES}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
        ctx = await browser.new_context(locale="en-US")
        page = await ctx.new_page()
        try:
            await page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=60000)
            stage("goto_done")
            await page.wait_for_timeout(6000)
            user_inp = page.locator('input[name="username_or_email"]').first
            await user_inp.wait_for(state="visible", timeout=30000)
            stage("username_visible")
            await user_inp.click()
            await page.keyboard.type(HANDLE, delay=60)
            stage("username_typed")
            await page.wait_for_timeout(3000)
            body = await body_text(page)
            if "temporarily limited" in body.lower():
                result["classification"] = "TEMPORARILY_LIMITED"
            elif await pw_editable(page):
                stage("password_editable")
            else:
                await page.get_by_role("button", name="Continue", exact=True).click(timeout=10000)
                stage("continue_clicked_for_password")
                await page.wait_for_timeout(6000)
                body = await body_text(page)
                if "temporarily limited" in body.lower():
                    result["classification"] = "TEMPORARILY_LIMITED"
            if result["classification"] == "UNKNOWN" and await pw_editable(page):
                pw_inp = page.locator('input[name="password"]').first
                await pw_inp.click()
                await page.keyboard.type(password, delay=40)
                password = ""
                stage("password_typed")
                await page.get_by_role("button", name="Continue", exact=True).click(timeout=10000)
                stage("submitted")
                await page.wait_for_timeout(9000)
                body = await body_text(page)
                url = page.url
                result["url_after_submit"] = url
                low = body.lower()
                if "verify you are human" in low or "arkose" in low or "captcha" in low:
                    result["classification"] = "HUMAN_CHECK"
                elif "temporarily limited" in low:
                    result["classification"] = "TEMPORARILY_LIMITED"
                elif "wrong password" in low or "incorrect password" in low or "didn't match" in low:
                    result["classification"] = "WRONG_PASSWORD"
                elif "問題が発生" in body or "something went wrong" in low:
                    result["classification"] = "GENERIC_ERROR"
                elif "/home" in url or await page.locator('[data-testid="SideNav_NewTweet_Button"], a[href="/compose/post"]').count() > 0:
                    result["classification"] = "SUCCESS"
                else:
                    code_inp = page.locator('input[autocomplete="one-time-code"]')
                    if await code_inp.count() > 0 and await code_inp.first.is_visible():
                        result["classification"] = "TWO_FACTOR_REQUIRED"
                    else:
                        result["classification"] = "UNCERTAIN"
                        result["body_excerpt"] = body[:400].replace("\n", " | ")
                if result["classification"] == "SUCCESS":
                    await ctx.storage_state(path="session_%s.json" % HANDLE)
                    result["session_saved"] = "session_%s.json" % HANDLE
            elif result["classification"] == "UNKNOWN":
                result["classification"] = "PASSWORD_NEVER_EDITABLE"
                result["body_excerpt"] = (await body_text(page))[:400].replace("\n", " | ")
            try:
                await page.screenshot(path="login4_final.png")
            except Exception:
                pass
        except Exception as e:
            result["error"] = type(e).__name__
            result["failed_at_stage"] = STAGES[-1] if STAGES else "start"
            try:
                await page.screenshot(path="login4_fail.png")
            except Exception:
                pass
        await browser.close()
    with open("login4_result.json", "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(main())
