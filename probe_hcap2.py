#!/usr/bin/env python3
"""Probe hCaptcha challenge structure via hCaptcha demo page (forces real image challenge)."""
import asyncio, json, tomllib, re
from pathlib import Path
from playwright.async_api import async_playwright

_cfg = tomllib.loads(Path("/home/ubuntu/nvidia-register/config.toml").read_text())
_pu = _cfg["browser"]["proxy_url"]  # http://user:pass@host:port
_m = re.match(r"http://([^:]+):([^@]+)@([^/]+)", _pu)
PROXY = {"server":"http://"+_m.group(3),"username":_m.group(1),"password":_m.group(2)}
# key '00000000-0000-0000-0000-000000000000' forces an image checkpoint challenge
DEMO = "https://accounts.hcaptcha.com/demo?sitekey=00000000-0000-0000-0000-000000000000"

async def main():
    pw = await async_playwright().start()
    b = await pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled","--no-sandbox","--disable-http2"])
    ctx = await b.new_context(proxy=PROXY, viewport={"width":1280,"height":900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36")
    page = await ctx.new_page()
    await page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    print("[1] goto demo", flush=True)
    await page.goto(DEMO, wait_until="commit", timeout=60000)
    await asyncio.sleep(5)

    # find + click widget checkbox
    frame=None
    for f in page.frames:
        if "hcaptcha.com" in f.url: frame=f
    if not frame:
        print("no hcaptcha frame:", [f.url[:70] for f in page.frames], flush=True); await b.close(); return
    print("[2] frame:", frame.url[:70], flush=True)
    # screenshot the widget area before clicking
    try:
        await frame.locator("#checkbox, [role=checkbox], .checkbox").first.click(timeout=15000)
        print("[3] clicked checkbox", flush=True)
    except Exception as e:
        print("[3] click err", e, flush=True)

    # poll for a challenge frame (distinct from the small checkbox frame)
    chall_changed = False
    for i in range(12):
        await asyncio.sleep(2)
        # dump frames again; a challenge often opens a larger frame or expands same
        haz = []
        for f in page.frames:
            if "hcaptcha" in f.url:
                try: inner = await f.evaluate("document.body.innerHTML.length")
                except: inner=-1
                try: hasT = await f.evaluate("!!document.querySelector('.task-images, .task, [class*=task], .prompt, .prompt-text')")
                except: hasT=False
                haz.append((f.url[:45],inner,hasT))
                if hasT: chall=f; chall_changed=True
        if chall_changed: break
        if i%2==0: print("  wait",i,"frames:",[(u,l) for u,l,t in haz], flush=True)

    if not chall_changed:
        print("no challenge triggered (checkbox passed?) token:", flush=True)
        print(await page.evaluate("()=>{const e=document.querySelector('[name*=h-captcha]');return e?e.value.slice(0,30):'none'}"),flush=True)
        await b.close(); return

    f = chall
    print("[4] challenge DOM dump:", flush=True)
    try:
        dom = await f.evaluate("document.body.innerHTML")
        # find the tile area html
        import re
        m = re.search(r'<div[^>]*class="[^"]*task[^"]*"[^>]*>.*?</div>', dom, re.S)
        print("[DOM][taskblock]", (m.group(0) if m else dom[:1500])[:2000], flush=True)
        # find prompt
        pr = await f.evaluate("()=>{const e=document.querySelector('.prompt-text,.prompt, .challenge-question,[class*=prompt]');return e?e.innerText:''}")
        print("[PROMPT]", repr(pr[:150]), flush=True)
    except Exception as e:
        print("dom err", e, flush=True)

    # tile bboxes + image srcs
    tl = await f.evaluate("""()=>{
      const out=[];
      for(const s of ['.task-image img','[class*=task] img','.challenge-content .task img','img']){
        const n=[...document.querySelectorAll(s)];
        if(n.length){ for(let i=0;i<n.length;i++){const r=n[i].getBoundingClientRect();
          out.push({i,cls:n[i].parentElement?.className?.slice(0,30),src:(n[i].src||n[i].getAttribute('data-src')||'').slice(0,70),x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)});
        } break; }
      }
      return out.slice(0,20);
    }""")
    print("[TILES]", tl, flush=True)
    # buttons
    btns = await f.evaluate("[...document.querySelectorAll('button,[role=button],.button')].map(b=>b.innerText||b.className).slice(0,8)")
    print("[BUTTONS]", btns, flush=True)
    try:
        await f.screenshot(path="/tmp/hc_challenge.png")
        print("[SAVED]", flush=True)
    except Exception as e: print("shot err",e,flush=True)
    await b.close()

asyncio.run(main())