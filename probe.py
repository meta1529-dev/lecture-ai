import asyncio, json, urllib.request
from playwright.async_api import async_playwright

HANDLE = "iurelb"

def egress_ip():
    try:
        return urllib.request.urlopen("https://ifconfig.me/ip", timeout=10).read().decode().strip()
    except Exception as e:
        return "unknown (%s)" % e

async def main():
    result = {"egress_ip": egress_ip(), "classification": "UNKNOWN", "url": "", "excerpt": ""}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(locale="en-US")
        try:
            await page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)
            inp = page.locator('input[autocomplete="username"]').first
            await inp.wait_for(state="visible", timeout=30000)
            await inp.fill(HANDLE)
            await page.wait_for_timeout(1000)
            clicked = False
            for name in ("Next", "次へ"):
                try:
                    await page.get_by_role("button", name=name).click(timeout=3000)
                    clicked = True
                    break
                except Exception:
                    pass
            result["clicked_next"] = clicked
            await page.wait_for_timeout(7000)
            result["url"] = page.url
            body = ""
            try:
                body = await page.inner_text("body")
            except Exception:
                pass
            result["excerpt"] = body[:600].replace("\n", " | ")
            pw = page.locator('input[type="password"]')
            if await pw.count() > 0 and await pw.first.is_visible():
                result["classification"] = "PASS_USERNAME_STEP"
            elif "問題が発生しました" in body or "Something went wrong" in body:
                result["classification"] = "GENERIC_ERROR"
            elif "captcha" in body.lower() or "arkose" in (await page.content()).lower():
                result["classification"] = "HUMAN_CHECK"
            await page.screenshot(path="screenshot.png")
        except Exception as e:
            result["error"] = ("%s: %s" % (type(e).__name__, e))[:300]
            try:
                await page.screenshot(path="screenshot.png")
            except Exception:
                pass
        await browser.close()
    with open("result.json", "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(main())
