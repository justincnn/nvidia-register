from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse, parse_qs

import requests
from playwright.async_api import Page

from config import CaptchaConfig


class CaptchaSolver(Protocol):
    async def solve(self, page: Page) -> bool:
        ...


class ManualCaptchaSolver:
    async def solve(self, page: Page) -> bool:
        print("\n[2/4] Please solve the hCaptcha manually...")
        for i in range(120):
            if await _is_register_button_enabled(page):
                print(f"  hCaptcha solved ({i}s)")
                return True
            await asyncio.sleep(1)
        print("  hCaptcha timeout")
        return False


class FreeClickSolver:
    """零成本方案: 直接点击 hCaptcha checkbox。

    原理: hCaptcha 在指纹/IP 干净时, checkbox 点击即过(不弹图片九宫格)。
    失败表现: 点击后弹出图片挑战 -> 判定失败, 由上层换代理后缀重试。
    """

    async def solve(self, page: Page) -> bool:
        print("\n[2/4] Solving hCaptcha by clicking checkbox (free)...")
        # 1. 等待 hCaptcha iframe 出现
        frame = None
        for i in range(15):
            for f in page.frames:
                if "hcaptcha" in f.url:
                    frame = f
                    print(f"  found hcaptcha frame: {f.url[:100]}")
                    break
            if frame:
                break
            await asyncio.sleep(1)
        if not frame:
            # 可能验证码未加载或已自动通过 -> 直接检查按钮
            if await _is_register_button_enabled(page):
                print("  register button already enabled (no captcha needed)")
                return True
            # 诊断: 打印所有 frame + 当前页 URL
            print("  hCaptcha iframe not found. All frames:")
            for f in page.frames:
                print(f"    - {f.url[:100]}")
            return False

        # 诊断: dump iframe 关键内容 (checkbox vs challenge)
        try:
            info = await frame.evaluate("""() => {
                const els = document.querySelectorAll('div,span,button,input,iframe');
                const out = [];
                for (const el of els) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 30 && r.height > 15 && r.width < 600) {
                        out.push({tag: el.tagName, id: el.id, cls: (el.className||'').toString().slice(0,45),
                                  role: el.getAttribute('role'), text: (el.textContent||'').trim().slice(0,30)});
                    }
                }
                return out.slice(0, 30);
            }""")
            print("  iframe 元素:")
            for el in info:
                print("    ", el)
        except Exception as e:
            print("  iframe dump err:", e)

        # 2. 点击 checkbox (两种常见选择器)
        clicked = False
        for selector in ["#checkbox", ".checkbox", "div[role='checkbox']"]:
            try:
                el = await frame.query_selector(selector)
                if el:
                    await el.click()
                    clicked = True
                    print(f"  clicked checkbox ({selector})")
                    break
            except Exception:
                continue
        if not clicked:
            print("  checkbox element not found in iframe")
            return False

        # 3. 等待注册按钮亮起(最多 60s; 若弹图片挑战则按钮不会亮)
        for i in range(60):
            if await _is_register_button_enabled(page):
                print(f"  hCaptcha checkbox passed ({i}s) — FREE")
                return True
            await asyncio.sleep(1)
        print("  checkbox clicked but challenge appeared / button stayed disabled")
        return False


@dataclass(frozen=True)
class YesCaptchaSolver:
    client_key: str
    api_url: str
    poll_interval_seconds: int
    timeout_seconds: int

    async def solve(self, page: Page) -> bool:
        print("\n[2/4] Solving hCaptcha with YesCaptcha...")
        site_key = await _get_site_key(page)
        if not site_key:
            print("  hCaptcha sitekey not found")
            return False

        task_id = self._create_task(page.url, site_key)
        token = self._poll_task_result(task_id)
        if not token:
            return False

        await _inject_hcaptcha_token(page, token)
        for i in range(20):
            if await _is_register_button_enabled(page):
                print(f"  hCaptcha solved by YesCaptcha ({i}s)")
                return True
            await asyncio.sleep(1)
        print("  hCaptcha token injected, but #register_button stayed disabled")
        return False

    def _create_task(self, website_url: str, website_key: str) -> str:
        response = requests.post(
            f"{self.api_url}/createTask",
            json={
                "clientKey": self.client_key,
                "task": {
                    "type": "HCaptchaTaskProxyless",
                    "websiteURL": website_url,
                    "websiteKey": website_key,
                },
            },
            timeout=30,
        )
        data = response.json()
        if data.get("errorId"):
            raise RuntimeError(f"YesCaptcha createTask failed: {data}")
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(f"YesCaptcha createTask missing taskId: {data}")
        return str(task_id)

    def _poll_task_result(self, task_id: str) -> str | None:
        deadline = time.time() + self.timeout_seconds
        while time.time() < deadline:
            response = requests.post(
                f"{self.api_url}/getTaskResult",
                json={"clientKey": self.client_key, "taskId": task_id},
                timeout=30,
            )
            data = response.json()
            if data.get("errorId"):
                print(f"  YesCaptcha getTaskResult failed: {data}")
                return None
            if data.get("status") == "ready":
                solution = data.get("solution") or {}
                return solution.get("gRecaptchaResponse") or solution.get("token")
            time.sleep(self.poll_interval_seconds)
        print("  YesCaptcha timeout")
        return None


@dataclass(frozen=True)
class CaptchaRunSolver:
    token: str
    api_url: str
    poll_interval_seconds: int
    timeout_seconds: int

    async def solve(self, page: Page) -> bool:
        print("\n[2/4] Solving hCaptcha with CaptchaRun...")
        site_key = await _get_site_key(page)
        if not site_key:
            print("  hCaptcha sitekey not found")
            return False

        user_agent = await page.evaluate("() => navigator.userAgent")
        task_id, token = self._create_task(page.url, site_key, user_agent)
        if task_id and not token:
            token = self._poll_task_result(task_id)
        if not token:
            return False

        await _inject_hcaptcha_token(page, token)
        for i in range(20):
            if await _is_register_button_enabled(page):
                print(f"  hCaptcha solved by CaptchaRun ({i}s)")
                return True
            await asyncio.sleep(1)
        print("  hCaptcha token injected, but #register_button stayed disabled")
        return False

    def _create_task(self, website_url: str, website_key: str, user_agent: str) -> tuple[str | None, str | None]:
        response = requests.post(
            f"{self.api_url}/v2/tasks",
            headers=self._headers(),
            json={
                "captchaType": "HCaptcha",
                "siteKey": website_key,
                "siteReferer": _site_referer(website_url),
                "userAgent": user_agent,
                "fallbackToActualUA": True,
            },
            timeout=30,
        )
        data = _response_json(response)
        if not response.ok:
            raise RuntimeError(f"CaptchaRun create task failed: {data}")
        task_id = data.get("taskId")
        result = data.get("result") or {}
        token = _extract_hcaptcha_token(result)
        if not task_id and not token:
            raise RuntimeError(f"CaptchaRun create task missing taskId/result: {data}")
        return str(task_id) if task_id else None, token

    def _poll_task_result(self, task_id: str) -> str | None:
        deadline = time.time() + self.timeout_seconds
        while time.time() < deadline:
            response = requests.get(
                f"{self.api_url}/v2/tasks/{task_id}",
                headers=self._headers(content_type=False),
                timeout=30,
            )
            data = _response_json(response)
            if not response.ok:
                print(f"  CaptchaRun get task result failed: {data}")
                return None

            status = str(data.get("status", "")).lower()
            if status == "success":
                return _extract_hcaptcha_token(data.get("response") or data.get("result") or {})
            if status == "fail":
                print(f"  CaptchaRun failed: {data.get('reason') or data}")
                return None
            time.sleep(self.poll_interval_seconds)
        print("  CaptchaRun timeout")
        return None

    def _headers(self, content_type: bool = True) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if content_type:
            headers["Content-Type"] = "application/json"
        return headers


@dataclass(frozen=True)
class LocalVlmSolver:
    """免费自建：在注册页自身解 hCaptcha 图片挑战，用本地 VLM(SiliconFlow Qwen-VL) 分类瓦格。

    零外部付费验证码服务；页面已带 create-account 会话，不受 UNAUTHORIZED 墙限制。
    挑战结构动态探测：不硬编码 hCaptcha 内部 class，失败&重试为主流程兜底。
    """
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int

    async def solve(self, page: Page) -> bool:
        print("\n[2/4] Solving hCaptcha with local VLM (free, self-hosted)...")
        await self._maybe_checkbox_pass(page)
        for rnd in range(6):
            if await _is_register_button_enabled(page):
                print(f"  register enabled after {rnd} round(s)")
                return True
            done = await _solve_challenge_page(page, self)
            if not done:
                await asyncio.sleep(2)
        print("  local VLM failed to finish challenge")
        return False

    async def _maybe_checkbox_pass(self, page: Page) -> bool:
        for frame in _hcaptcha_frames(page):
            if await _click_hcaptcha_checkbox(frame):
                for _ in range(8):
                    if await _is_register_button_enabled(page):
                        print("  register enabled (checkbox pass)")
                        return True
                    await asyncio.sleep(1)
                break
        return False


HCAPTCHA_VLM_SYSTEM = (
    "You are an hCaptcha image classifier. "
    "You are given a question and several images, one image per tile. "
    "Return the 0-indexed positions of the images that match the question. "
    'Reply with STRICT JSON only, e.g. {"answer": [0, 2, 5]}. '
    "If none match, reply {\"answer\": []}."
)


def _vlm_classify(solver, b64_images: list[str], prompt: str) -> list[int]:
    content: list[dict] = []
    for b64 in b64_images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}})
    content.append({"type": "text", "text": f"Question: {prompt}\nReturn JSON of matching tile indices."})
    payload = {
        "model": solver.model,
        "temperature": 0.05,
        "max_tokens": 200,
        "messages": [
            {"role": "system", "content": HCAPTCHA_VLM_SYSTEM},
            {"role": "user", "content": content},
        ],
    }
    resp = requests.post(
        f"{solver.base_url}/chat/completions",
        headers={"Authorization": f"Bearer {solver.api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    import re, json as _json
    m = re.search(r"\{.*?\}", raw, re.S)
    if not m:
        raise RuntimeError(f"VLM returned no JSON: {raw!r}")
    data = _json.loads(m.group(0))
    ans = data.get("answer", [])
    return [int(x) for x in ans]


_HCAPTCHA_BBOX_SYSTEM = (
    "You are solving a web drag-puzzle captcha. Look at the image carefully. "
    "Identify the PART THAT MUST BE DRAGGED (the movable piece) and the DESTINATION where it goes (the gap). "
    "Describe briefly, then END your reply EXACTLY with a line:\n"
    'SRC <src_x>,<src_y> DST <dst_x>,<dst_y>\n'
    "where all four are NORMALIZED decimals in 0.0-1.0 relative to image size. "
    "No other trailing text after that line."
)


def _vlm_locate(solver, b64_image: str, prompt: str) -> dict:
    """VLM 定位 canvas 图中目标。返回 {boxes:[{x1,y1,x2,y2}], target_box:{...}}，drag 用 boxes[0]+target 中心。"""
    content = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}", "detail": "high"}},
        {"type": "text", "text": f"Task: {prompt or '找出要拖动的物块与缺口的归一化位置'}.\n给出 SRC 与 DST。"},
    ]
    payload = {
        "model": solver.model,
        "temperature": 0.0,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": _HCAPTCHA_BBOX_SYSTEM},
            {"role": "user", "content": content},
        ],
    }
    resp = requests.post(
        f"{solver.base_url}/chat/completions",
        headers={"Authorization": f"Bearer {solver.api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    import re
    # 优先: 描述+行内 SRC x,y DST x,y (离线验证这个最稳)
    m = re.search(r"SRC\s+([0-9.]+)\s*,\s*([0-9.]+)\s+DST\s+([0-9.]+)\s*,\s*([0-9.]+)", raw)
    if m:
        sx, sy, dx, dy = (float(m.group(i)) for i in range(1, 5))
        # 归一化到 0-1, 若 >1 按百分比/近 0-100 处理
        def _norm(v):
            return v / 100.0 if v > 1.0 else v
        sx, sy, dx, dy = map(_norm, (sx, sy, dx, dy))
        return {"boxes": [{"x1": sx, "y1": sy, "x2": sx, "y2": sy}],
                "target": {"x1": dx, "y1": dy, "x2": dx, "y2": dy}}
    # 兜底: JSON
    import json as _json
    raw2 = re.sub(r"```(?:json)?", "", raw)
    mj = re.search(r"\{[^{}]*(\{[^{}]*\}[^{}]*)*\}", raw2, re.S)
    if mj:
        try:
            d = _json.loads(mj.group(0))
            return d
        except Exception:
            pass
    raise RuntimeError(f"VLM locate could not parse: {raw!r}")


async def _find_canvas(page: Page):
    """返回 hCaptcha 内可见的 canvas 元素。"""
    for f in _hcaptcha_frames(page):
        try:
            el = await f.query_selector("canvas")
            if el:
                box = await el.bounding_box()
                if box and box["width"] >= 100 and box["height"] >= 100:
                    return el, box
        except Exception:
            continue
    return None, None


async def _solve_canvas(page, solver, prompt: str, max_attrs: int = 4) -> bool:
    """在 canvas 上用 VLM 定位并操作：drag_drop=从 source 拖到 target_box；area_select=对多 boxes 点选。"""
    canvas, cbox = await _find_canvas(page)
    if not canvas:
        print("  [canvas] no canvas found (tile/non-canvas challenge?)")
        return False
    print(f"  [canvas] found {cbox['width']:.0f}x{cbox['height']:.0f} type={_current_challenge_type}")
    w, h = cbox["width"], cbox["height"]
    png = await canvas.screenshot()
    b64 = base64.b64encode(png).decode()
    try:
        loc = _vlm_locate(solver, b64, prompt)
    except Exception as ex:
        print(f"  [canvas] VLM locate err: {ex}")
        return False
    print(f"  [canvas] VLM loc=boxes:{loc.get('boxes')} target={loc.get('target_box') or loc.get('target')}")
    boxes = loc.get("boxes", [])
    target = loc.get("target_box") or loc.get("target")
    if not boxes and not target:
        print("  [canvas] VLM returned no boxes")
        return False

    def norm_to_page(b):
        return {
            "x1": cbox["x"] + b["x1"] * w,
            "y1": cbox["y"] + b["y1"] * h,
            "x2": cbox["x"] + b["x2"] * w,
            "y2": cbox["y"] + b["y2"] * h,
        }

    if _current_challenge_type == "image_drag_drop" and target and boxes:
        src = norm_to_page(boxes[0])
        dst = norm_to_page(target)
        sx, sy = (src["x1"] + src["x2"]) / 2, (src["y1"] + src["y2"]) / 2
        dx, dy = (dst["x1"] + dst["x2"]) / 2, (dst["y1"] + dst["y2"]) / 2
        print(f"  [canvas] drag {sx:.0f},{sy:.0f} -> {dx:.0f},{dy:.0f}")
        try:
            mouse = page.mouse
            await mouse.move(sx, sy)
            await mouse.down()
            for i in range(12):
                f = (i + 1) / 12
                await mouse.move(sx + (dx - sx) * f, sy + (dy - sy) * f, steps=3)
                await asyncio.sleep(0.01)
            await mouse.up()
            return True
        except Exception as ex:
            print(f"  [canvas] drag err {ex}")
            return False
    else:
        clicked = 0
        for b in boxes[:max_attrs]:
            n = norm_to_page(b)
            cx, cy = (n["x1"] + n["x2"]) / 2, (n["y1"] + n["y2"]) / 2
            try:
                await page.mouse.click(cx, cy)
                await asyncio.sleep(0.12)
                clicked += 1
            except Exception as ex:
                print(f"  [canvas] click err {ex}")
        return clicked > 0


def _hcaptcha_frames(page: Page):
    return [f for f in page.frames if "hcaptcha.com" in f.url]

# --- canvas 挑战: VLM bbox 定位 + 拖拽/框选 / 点选 ---------------------------
_current_challenge_type: str | None = None  # "image_drag_drop" / "image_label_area_select" / ...

def _listen_challenge_type(page) -> None:
    """记录 hCaptcha 挑战类型(从加载的 challenge.js 路径识别), 供 canvas 求解分流。"""
    def _on_request(req):
        global _current_challenge_type
        if "/challenge/" in req.url and req.url.endswith("/challenge.js") and "hcaptcha.com" in req.url:
            import re as _re
            m = _re.search(r"/challenge/([^/]+)/challenge\.js", req.url)
            if m:
                _current_challenge_type = m.group(1)
                print(f"  [challenge] type = {_current_challenge_type}")
    page.on("request", _on_request)

TILE_SELECTS = [".option", ".option img", "[class*=task] img", "[class*=image] img", "img"]


async def _click_hcaptcha_checkbox(frame) -> bool:
    for sel in ["#checkbox", ".checkbox", "div[role=checkbox]"]:
        try:
            el = await frame.query_selector(sel)
            if el:
                await el.click()
                return True
        except Exception:
            continue
    return False


async def _tile_elements(frame):
    """返回 frame 内瓦格元素列表（支持 div/背景图/嵌套 img，动态适配）。"""
    for sel in TILE_SELECTS:
        try:
            cand = await frame.query_selector_all(sel)
        except Exception:
            continue
        good = []
        for el in cand:
            try:
                box = await el.bounding_box()
            except Exception:
                continue
            if box and 24 <= box["width"] <= 480 and 24 <= box["height"] <= 480:
                good.append(el)
        if good:
            return good
    return []


async def _find_challenge(page: Page):
    """在所有 hcaptcha frame 找真正含格子挑战的帧。"""
    for frame in _hcaptcha_frames(page):
        els = await _tile_elements(frame)
        if len(els) >= 2:
            return frame, els
    return None, []


async def _extract_prompt(frame) -> str:
    for sel in [".challenge-prompt", "[class*=prompt]", ".task-description",
                "[class*=question]", "h1, h2, .challenge-title"]:
        try:
            el = await frame.query_selector(sel)
            if el:
                t = (await el.inner_text()).strip()
                if t and "Please try again" not in t and len(t) < 300:
                    return t[:200]
        except Exception:
            continue
    # 兜底：扫正文，剔除错误/无关短语，取第一句"像题目"的文本
    try:
        txt = (await frame.evaluate("document.body.innerText || ''")).strip()
        skip = ("Please try again", "Verify Answers", "⌃", "hCaptcha")
        for line in (l.strip() for l in txt.splitlines()):
            if line and not any(s in line for s in skip) and 8 <= len(line) <= 300:
                return line[:200]
    except Exception:
        pass
    return ""


async def _solve_challenge_page(page, solver) -> bool:
    """挑帧→取 prompt+瓦格截图→VLM→点格→提交验证。

    当前阶段(canvas框选自研): 遇 canvas 挑战时只做「全图+prompt 采集」落盘，
    用于离线校验 VLM 的 bbox 定位能力; 待验证通过再替换为 拖框+Verify。
    """
    frame, tile_els = await _find_challenge(page)
    if not tile_els or frame is None:
        print("  [challenge] no tiles found yet")
        # canvas 挑战: 用 VLM 定框后拖拽/点选 (推荐路径; 采集仅首次调试用)
        if frame is None and _hcaptcha_frames(page):
            frame = _hcaptcha_frames(page)[0]
        prompt = await _extract_prompt(frame) if frame else ""
        solved = await _solve_canvas(page, solver, prompt)
        if solved:
            clicked = await _click_verify_button(page)
            print(f"  [canvas] verify click: {clicked}")
            for _ in range(10):
                if await _is_register_button_enabled(page):
                    return True
                await asyncio.sleep(1)
        return False

    prompt = await _extract_prompt(frame)
    print(f"  [challenge] prompt: {prompt!r}")

    b64s: list[str] = []
    for i, loc in enumerate(tile_els):
        try:
            b64s.append(base64.b64encode(await loc.screenshot(type="jpeg")).decode())
        except Exception as ex:
            print(f"  [challenge] tile {i} shot err: {ex}")
    if not b64s:
        return False

    try:
        indices = _vlm_classify(solver, b64s, prompt)
    except Exception as ex:
        print(f"  [challenge] VLM classify err: {ex}")
        return False
    print(f"  [challenge] VLM matches: {indices}")

    for i in indices:
        if 0 <= i < len(tile_els):
            try:
                await tile_els[i].click()
            except Exception as ex:
                print(f"  [challenge] click {i} err: {ex}")

    clicked = await _click_verify_button(page)
    print(f"  [challenge] verify click: {clicked}")

    for _ in range(10):
        if await _is_register_button_enabled(page):
            return True
        await asyncio.sleep(1)
    return False


async def _verify_button(frame) -> bool:
    for sel in ["[class*='button-submit']", "div[title*='Verify']", "#button-submit"]:
        try:
            el = await frame.query_selector(sel)
            if el:
                await el.click()
                return True
        except Exception:
            continue
    return False


async def _click_verify_button(page) -> bool:
    for f in _hcaptcha_frames(page):
        if await _verify_button(f):
            return True
    return False


_captured_sitekey: str | None = None


def reset_captcha_state() -> None:
    """重置模块级缓存，供批量注册时每个新账号使用。"""
    global _captured_sitekey, _current_challenge_type
    _captured_sitekey = None
    _current_challenge_type = None


def start_capturing_sitekey(page: Page) -> None:
    """注册网络请求监听器，从 checksiteconfig 请求中捕获 hCaptcha sitekey。

    必须在 create-account 页加载前调用。
    """
    def _on_request(req):
        global _captured_sitekey
        url = req.url
        # 调试: 打印所有验证码相关请求
        if any(k in url for k in ["captcha", "checksiteconfig", "hsw", "getcaptcha"]):
            print(f"  [req] {url[:160]}", flush=True)
        if _captured_sitekey:
            return
        if "checksiteconfig" in url and "sitekey=" in url:
            try:
                sk = parse_qs(urlparse(url).query).get("sitekey", [None])[0]
                if sk:
                    _captured_sitekey = sk
                    print(f"  sitekey captured: {sk}")
            except Exception:
                pass

    # 捕获 getcaptcha 响应体 → 落盘挑战结构与任务图（离线复现用）
    async def _on_response(resp):
        url = resp.url
        if "getcaptcha" in url and resp.status == 200:
            try:
                b = await resp.body()
                open("/tmp/hc_getcaptcha.json", "wb").write(b)
                import json as _j
                try:
                    gj = _j.loads(b[:4000])
                    print(f"  [cam] getcaptcha body saved keys={list(gj.keys())[:8]} "
                          f"len={len(b)}", flush=True)
                except Exception:
                    pass
            except Exception as ex:
                print(f"  [cam] resp body err {ex}", flush=True)
    page.on("response", _on_response)

    page.on("request", _on_request)
    _listen_challenge_type(page)


async def _get_site_key(page: Page) -> str | None:
    """获取 sitekey：先读网络捕获缓存，再兜底从 DOM/hCaptcha iframe 读取。"""
    global _captured_sitekey
    if _captured_sitekey:
        return _captured_sitekey
    # 等待网络请求捕获（hCaptcha iframe 可能还在加载）
    for _ in range(15):
        if _captured_sitekey:
            return _captured_sitekey
        # 兜底1: 从 iframe src 或页面 data attr 读 sitekey
        try:
            sk = await page.evaluate("""() => {
                const f = document.querySelector('iframe[src*="hcaptcha.com"]');
                const src = f && f.getAttribute('src') || '';
                const m = src.match(/sitekey=([a-f0-9-]{20,})/);
                if (m) return m[1];
                const el = document.querySelector('[data-sitekey]');
                if (el && el.getAttribute('data-sitekey')) return el.getAttribute('data-sitekey');
                return null;
            }""")
            if sk:
                _captured_sitekey = sk
                print(f"  sitekey captured (DOM): {sk}")
                return sk
        except Exception:
            pass
        await asyncio.sleep(1)
    return None


# ---------------------------------------------------------------------------
#  token 注入（通过拦截的 Angular 回调直接触发 onSuccess）
# ---------------------------------------------------------------------------


async def _inject_hcaptcha_token(page: Page, token: str) -> None:
    """调用拦截的 __hCaptchaCallback 触发 Angular onSuccess，使 #register_button enable。

    回调由 main.py 的 _ensure_hcaptcha_hook 通过 addInitScript 在
    hcaptcha.render 调用时捕获到 window.__hCaptchaCallback。
    """
    result = await page.evaluate(
        r"""(token) => {
            if (typeof window.__hCaptchaCallback === 'function') {
                window.__hCaptchaCallback(token);
                return true;
            }
            return false;
        }""",
        token,
    )
    print(f"  callback called: {result}")


# ---------------------------------------------------------------------------
#  辅助
# ---------------------------------------------------------------------------


async def _is_register_button_enabled(page: Page) -> bool:
    """检查 #register_button 是否 enabled（hCaptcha 通过后按钮才会 enable）。"""
    result = await page.evaluate(
        """() => {
            const btn = document.querySelector('#register_button');
            return btn ? !btn.disabled : false;
        }"""
    )
    return bool(result)


def _site_referer(website_url: str) -> str:
    parsed = urlparse(website_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/"
    return website_url


def _response_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {"status_code": response.status_code, "text": response.text}
    return data if isinstance(data, dict) else {"data": data}


def _extract_hcaptcha_token(data: dict[str, Any]) -> str | None:
    token = data.get("gRecaptchaResponse") or data.get("token")
    return str(token) if token else None


def build_captcha_solver(config: CaptchaConfig) -> CaptchaSolver:
    if config.mode == "manual":
        return ManualCaptchaSolver()
    if config.mode == "free":
        return FreeClickSolver()
    if config.mode == "yescaptcha":
        if not config.yescaptcha_client_key:
            raise ValueError("yescaptcha_client_key is required")
        return YesCaptchaSolver(
            client_key=config.yescaptcha_client_key,
            api_url=config.yescaptcha_api_url,
            poll_interval_seconds=config.poll_interval_seconds,
            timeout_seconds=config.timeout_seconds,
        )
    if config.mode == "captcharun":
        if not config.captcharun_token:
            raise ValueError("captcharun_token is required")
        return CaptchaRunSolver(
            token=config.captcharun_token,
            api_url=config.captcharun_api_url,
            poll_interval_seconds=config.poll_interval_seconds,
            timeout_seconds=config.timeout_seconds,
        )
    if config.mode == "local-vlm":
        if not config.local_vlm_api_key:
            raise ValueError("captcha.local_vlm_api_key is required when captcha.mode = 'local-vlm'")
        return LocalVlmSolver(
            base_url=config.local_vlm_base_url,
            api_key=config.local_vlm_api_key,
            model=config.local_vlm_model,
            timeout_seconds=config.timeout_seconds,
        )
    raise ValueError(f"Unsupported captcha mode: {config.mode}")
