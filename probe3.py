import asyncio, json
from playwright.async_api import async_playwright

async def check(page, url):
    d = {"url": url}
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        d["http_status"] = resp.status if resp else None
    except Exception as e:
        d["goto_error"] = ("%s: %s" % (type(e).__name__, e))[:200]
    await page.wait_for_timeout(12000)
    d["final_url"] = page.url
    try:
        d["title"] = await page.title()
    except Exception:
        d["title"] = None
    try:
        body = await page.inner_text("body")
    except Exception:
        body = ""
    d["body_excerpt"] = body[:500].replace("\n", " | ")
    try:
        d["inputs"] = await page.evaluate("""() => Array.from(document.querySelectorAll('input')).map(i => ({type: i.type, autocomplete: i.autocomplete, name: i.name, visible: !!(i.offsetWidth || i.offsetHeight)}))""")
    except Exception as e:
        d["inputs"] = "eval failed: %s" % e
    return d

async def main():
    out = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
        page = await browser.new_page(locale="en-US")
        out["login"] = await check(page, "https://x.com/i/flow/login")
        try:
            await page.screenshot(path="shot3_login.png")
        except Exception:
            pass
        out["public_profile"] = await check(page, "https://x.com/iurelb")
        try:
            await page.screenshot(path="shot3_profile.png")
        except Exception:
            pass
        await browser.close()
    with open("result3.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False))

asyncio.run(main())
