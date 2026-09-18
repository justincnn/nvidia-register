#!/usr/bin/env python3
"""Probe NVIDIA hCaptcha challenge structure (feasibility check).
Clicks checkbox via proxy → dumps challenge-frame DOM + tile bboxes + saves 2 tiles.
"""
import asyncio, sys, base64, time
from playwright.async_api import async_playwright

PROXY = {"server":"http://129.146.176.221:21978","username":"287fb178.a","password":"27950b91d0229166"}
NV_LOGIN = "https://login.nvgs.nvidia.com/v1/login?preferred_nvidia=true"

async def main():
    pw = await async_playwright().start()
    b = await pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled","--no-sandbox","--disable-http2"])
    ctx = await b.new_context(proxy=PROXY, viewport={"width":1280,"height":900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36")
    page = await ctx.new_page()
    await page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    print("[1] goto", flush=True)
    await page.goto(NV_LOGIN, wait_until="commit", timeout=60000)
    await asyncio.sleep(4)
    print("[2] frames:", [f.url[:70] for f in page.frames], flush=True)

    # try to find + click hcaptcha checkbox widget
    widget = None
    for f in page.frames:
        if "hcaptcha" in f.url and "checkbox" in f.url.lower():
            widget = f; break
    if widget is None:
        for f in page.frames:
            if "hcaptcha" in f.url:
                widget = f; break
    if not widget:
        print("!! no hcaptcha frame found — dump all urls:", flush=True)
        for f in page.frames: print("   ", f.url[:90], flush=True)
        await b.close(); return
    print("[2] widget frame:", widget.url[:80], flush=True)
    # click checkbox
    try:
        await widget.locator("#checkbox, div[role=checkbox], .checkbox").first.click(timeout=15000)
        print("[3] checkbox clicked", flush=True)
    except Exception as e:
        print("[3] checkbox click err:", type(e).__name__, flush=True)
    # wait for challenge to possibly appear
    for i in range(10):
        await asyncio.sleep(2)
        chall = None
        for f in page.frames:
            if "hcaptcha" in f.url:
                try:
                    t = await f.evaluate("document.title || ''")
                    inner = await f.evaluate("document.body ? document.body.innerHTML.length : 0")
                except Exception:
                    continue
                # challenge radio: has prompt text area / task images
                has_prompt = False
                try:
                    has_prompt = await f.evaluate("!!(document.querySelector('.prompt, [class*=prompt], #task, .task-image'))")
                except Exception: pass
                print(f"  frame {f.url[:40]:42} innerLen={inner} hasPrompt={has_prompt}", flush=True)
                if has_prompt:
                    chall = f
        if chall: break
    if not chall:
        print("[X] no challenge frame appeared; token area?", flush=True)
        await b.close(); return
    print("\n[4] === CHALLENGE DOM ===", flush=True)
    dom = await chall.evaluate("""() => {
        const c = document.querySelector('.challenge-container, #challenge, .task-images, body');
        return c ? c.innerHTML : document.body.innerHTML;
    }""")
    print(dom[:2500], flush=True)
    # tiles: find imgs / bounding boxes
    tiles = await chall.evaluate("""() => {
        const out = [];
        const sel = ['.task-image img', '#task img', '.task img', '[class*=task] img', 'img'];
        let nodes = [];
        for (const s of sel){ nodes = [...document.querySelectorAll(s)]; if(nodes.length){break;} }
        nodes.forEach((im,i)=>{ const r=im.getBoundingClientRect();
            out.push({i,tag:im.tagName,cls:im.className,src:im.src.slice(0,60),x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)});});
        return out.slice(0,20);
    }""")
    print("[5] tiles:", tiles, flush=True)
    # prompt text
    prompt = await chall.evaluate("""() => {
        const el=document.querySelector('.prompt, [class*=prompt], .hc_rep-fwd, .challenge-question, h2, p');
        return el?el.innerText:'';
    }""")
    print("[6] prompt:", repr(prompt[:200]), flush=True)
    # screenshot challenge frame region
    await chall.screenshot(path="/tmp/hc_challenge.png")
    print("[7] saved /tmp/hc_challenge.png", flush=True)
    await b.close()

asyncio.run(main())