from __future__ import annotations

import asyncio
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


# ---------------------------------------------------------------------------
#  sitekey 捕获（render=explicit 模式下 DOM 无 sitekey，只能从网络请求获取）
# ---------------------------------------------------------------------------

_captured_sitekey: str | None = None


def reset_captcha_state() -> None:
    """重置模块级缓存，供批量注册时每个新账号使用。"""
    global _captured_sitekey
    _captured_sitekey = None


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

    page.on("request", _on_request)


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
    raise ValueError(f"Unsupported captcha mode: {config.mode}")
