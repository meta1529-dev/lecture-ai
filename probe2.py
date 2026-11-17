import asyncio, json
from playwright.async_api import async_playwright

async def main():
    out = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(locale="en-US")
        try:
            resp = await page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=60000)
            out["http_status"] = resp.status if resp else None
        except Exception as e:
            out["goto_error"] = ("%s: %s" % (type(e).__name__, e))[:200]
        await page.wait_for_timeout(15000)
        out["url"] = page.url
        try:
            out["title"] = await page.title()
        except Exception:
            out["title"] = None
        try:
            body = await page.inner_text("body")
        except Exception:
            body = ""
        out["body_excerpt"] = body[:800].replace("\n", " | ")
        try:
            inputs = await page.evaluate("""() => Array.from(document.querySelectorAll('input')).map(i => ({type: i.type, autocomplete: i.autocomplete, name: i.name, visible: !!(i.offsetWidth || i.offsetHeight)}))""")
        except Exception as e:
            inputs = "eval failed: %s" % e
        out["inputs"] = inputs
        try:
            await page.screenshot(path="shot2.png")
        except Exception:
            pass
        await browser.close()
    with open("result2.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False))

asyncio.run(main())
