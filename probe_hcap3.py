#!/usr/bin/env python3
"""Probe REAL NVIDIA create-account hCaptcha challenge. Fill email/pass, click checkbox, dump challenge."""
import asyncio, tomllib, re
from pathlib import Path
from playwright.async_api import async_playwright

_cfg=tomllib.loads(Path("/home/ubuntu/nvidia-register/config.toml").read_text())
_m=re.match(r"http://([^:]+):([^@]+)@([^/]+)",_cfg["browser"]["proxy_url"])
PROXY={"server":"http://"+_m.group(3),"username":_m.group(1),"password":_m.group(2)}

URL="https://login.nvgs.nvidia.com/v1/create-account"
EMAIL="nvprobe2333@mail.expressai.eu.org"; PW="Abxy12345678"

async def main():
    pw=await async_playwright().start()
    b=await pw.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled","--no-sandbox","--disable-http2"])
    ctx=await b.new_context(proxy=PROXY,viewport={"width":1280,"height":900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36")
    page=await ctx.new_page()
    await page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    print("[1] goto create-account",flush=True)
    await page.goto(URL,wait_until="commit",timeout=60000)
    await asyncio.sleep(4)
    print("[2] url:",page.url[:90],flush=True)
    # fill what's there
    for sel in ["#registration_email","#email","input[type=email]"]:
        try:
            e=page.locator(sel).first; await e.wait_for(state="visible",timeout=6000)
            await e.fill(EMAIL); print("filled email",sel,flush=True); break
        except Exception: pass
    for sel in ["#registration_password","#password","input[type=password]"]:
        try:
            e=page.locator(sel).first; await e.wait_for(state="visible",timeout=6000); await e.fill(PW); print("filled pw",sel,flush=True); break
        except Exception: pass
    await asyncio.sleep(2)
    # enumerate hcaptcha frames
    def frames():
        return [(f.url[:80], f) for f in page.frames if "hcaptcha" in f.url]
    print("[3] hcaptcha frames:",[u for u,_ in frames()],flush=True)
    wf=None
    for u,f in frames():
        if "checkbox" in u or "hc_rep" in u: wf=f
    if wf is None and frames(): wf=frames()[0][1]
    if not wf:
        print("[X] no hcaptcha frame; all frames:",flush=True)
        for ff in page.frames: print("   ",ff.url[:90],flush=True)
        await b.close(); return
    # click checkbox
    try:
        await wf.locator("#checkbox, [role=checkbox], .checkbox").first.click(timeout=15000)
        print("[4] clicked checkbox",flush=True)
    except Exception as e:
        print("[4] checkbox err",type(e).__name__,flush=True)
    # wait for challenge (task) to mount inside any hcaptcha frame
    chall=None
    for i in range(12):
        await asyncio.sleep(2)
        for u,f in frames():
            try: ht=await f.evaluate("!!document.querySelector('.task, [class*=task], [class*=challenge], .prompt')")
            except: ht=False
            try: ln=await f.evaluate("document.body.innerHTML.length")
            except: ln=-1
            if ht or (ln>15000): chall=f; break
        if chall: break
    if not chall:
        print("[X] no challenge mounted; token?",flush=True)
        tok=await page.evaluate("()=>{const e=document.querySelector('[name*=h-captcha-hcaptcha]');return e?e.value.slice(0,25):'none'}"); print(" token:",tok,flush=True); await b.close(); return
    print("[5] challenge frame:",chall.url[:80],"len≈",ln,flush=True)
    dom=await chall.evaluate("document.body.innerHTML")
    # locate task image wrappers + tiles
    t=await chall.evaluate("""()=>{
      const out={tiles:[],prompt:'',buttons:[],verifysel:[]};
      const p=document.querySelector('.challenge-question, .prompt, [class*=prompt], .hc-* :not(*)');
      out.prompt=(p?p.innerText:'').slice(0,160);
      const imgs=[...document.querySelectorAll('img')];
      imgs.forEach((im,i)=>{const r=im.getBoundingClientRect(); if(r.width>20&&r.height>20) out.tiles.push({i,w:Math.round(r.width),h:Math.round(r.height),cls:(im.parentElement.className||'').slice(0,24)});});
      out.btn=[...document.querySelectorAll('button,[role=button]')].map(b=>(b.innerText||b.className).slice(0,30));
      return out;
    }""")
    print("[6] prompt:",repr(t["prompt"]),flush=True)
    print("[7] imgs:",t["tiles"],flush=True)
    print("[8] buttons:",t["btn"],flush=True)
    await chall.screenshot(path="/tmp/hc_real.png")
    print("[9] shot /tmp/hc_real.png",flush=True)
    await b.close()

asyncio.run(main())