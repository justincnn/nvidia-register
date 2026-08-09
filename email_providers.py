from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from typing import Protocol

import requests

from config import AppConfig, CloudflareTempEmailConfig, DuckMailConfig, MoeMailConfig


@dataclass(frozen=True)
class TempEmailInbox:
    address: str
    token: str


class TempEmailProvider(Protocol):
    def create_inbox(self, name: str) -> TempEmailInbox:
        ...

    def poll_verification_code(self, inbox: TempEmailInbox, timeout_seconds: int = 180) -> str | None:
        ...


class CloudflareTempEmailProvider:
    def __init__(self, config: CloudflareTempEmailConfig):
        self.config = config

    def create_inbox(self, name: str) -> TempEmailInbox:
        response = requests.post(
            f"{self.config.api_url}/admin/new_address",
            headers={"x-admin-auth": self.config.admin_auth, "Content-Type": "application/json"},
            json={"name": name, "domain": self.config.domain, "enablePrefix": False},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        address = data.get("address", "")
        token = data.get("jwt", "")
        if not address or not token:
            raise RuntimeError(f"Email creation failed: {data}")
        return TempEmailInbox(address=address, token=token)

    def poll_verification_code(self, inbox: TempEmailInbox, timeout_seconds: int = 180) -> str | None:
        deadline = time.time() + timeout_seconds
        headers = {"Authorization": f"Bearer {inbox.token}"}
        while time.time() < deadline:
            try:
                response = requests.get(
                    f"{self.config.api_url}/api/mails?limit=5&offset=0",
                    headers=headers,
                    timeout=15,
                )
                data = response.json()
                mails = data.get("results") or data.get("data") or []
                for mail in mails:
                    mail_id = mail.get("id") or mail.get("_id")
                    if not mail_id:
                        continue
                    detail_response = requests.get(
                        f"{self.config.api_url}/api/mail/{mail_id}",
                        headers=headers,
                        timeout=15,
                    )
                    code = _extract_verification_code(detail_response.json().get("raw", ""))
                    if code:
                        return code
            except Exception as exc:
                print(f"  email poll: {exc}", flush=True)
            time.sleep(2)
        return None


class DuckMailProvider:
    def __init__(self, config: DuckMailConfig):
        self.config = config

    def create_inbox(self, name: str) -> TempEmailInbox:
        address = f"{name}@{self.config.domain}"
        password = f"dm_{secrets.token_hex(8)}"

        response = requests.post(
            f"{self.config.api_url}/accounts",
            headers=self._account_headers(),
            json={"address": address, "password": password},
            timeout=15,
        )
        response.raise_for_status()

        token_response = requests.post(
            f"{self.config.api_url}/token",
            headers={"Content-Type": "application/json"},
            json={"address": address, "password": password},
            timeout=15,
        )
        token_response.raise_for_status()
        data = token_response.json()
        token = data.get("token", "")
        if not token:
            raise RuntimeError(f"DuckMail token acquisition failed: {data}")
        return TempEmailInbox(address=address, token=token)

    def poll_verification_code(self, inbox: TempEmailInbox, timeout_seconds: int = 180) -> str | None:
        deadline = time.time() + timeout_seconds
        headers = {"Authorization": f"Bearer {inbox.token}"}
        while time.time() < deadline:
            try:
                response = requests.get(
                    f"{self.config.api_url}/messages?page=1",
                    headers=headers,
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()
                messages = data.get("hydra:member") or []
                for message in messages:
                    message_id = message.get("id")
                    if not message_id:
                        continue
                    detail_response = requests.get(
                        f"{self.config.api_url}/messages/{message_id}",
                        headers=headers,
                        timeout=15,
                    )
                    detail_response.raise_for_status()
                    detail = detail_response.json()
                    body = _duckmail_message_body(detail)
                    code = _extract_verification_code(body)
                    if code:
                        return code
            except Exception as exc:
                print(f"  email poll: {exc}", flush=True)
            time.sleep(2)
        return None

    def _account_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers


class MoeMailProvider:
    """moemail (https://github.com/beilunyang/moemail) 临时邮箱。

    API:
      GET  /api/config                  获取系统配置(含可用域名列表)
      POST /api/emails/generate         生成临时邮箱
      GET  /api/emails/{emailId}        获取邮件列表
      GET  /api/emails/{emailId}/{messageId}  获取单封邮件
    """

    def __init__(self, config: MoeMailConfig):
        self.config = config
        self._domains: list[str] | None = None

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.config.api_key,
            "Content-Type": "application/json",
        }

    def _get_domain(self) -> str:
        """返回邮箱域名: 优先配置值, 否则从上游 /api/config 获取。"""
        if self.config.domain:
            return self.config.domain
        if self._domains is None:
            resp = requests.get(f"{self.config.api_url}/api/config", headers=self._headers(), timeout=15)
            resp.raise_for_status()
            data = resp.json()
            # 结构可能是 {"domains": [...]} 或 {"data": {"domains": [...]}}
            domains = data.get("domains") or (data.get("data") or {}).get("domains") or []
            if not domains:
                raise RuntimeError(f"moemail /api/config 未返回域名列表: {data}")
            self._domains = [d for d in domains if isinstance(d, str)]
            print(f"  [moemail] 上游可用域名: {', '.join(self._domains)}", flush=True)
        if not self._domains:
            raise RuntimeError("moemail 无可用域名")
        return self._domains[0]

    def create_inbox(self, name: str) -> TempEmailInbox:
        domain = self._get_domain()
        resp = requests.post(
            f"{self.config.api_url}/api/emails/generate",
            headers=self._headers(),
            json={"name": name, "expiryTime": 3600000, "domain": domain},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        # 响应可能包在 data 里
        payload = data.get("data") or data
        email_id = payload.get("id") or payload.get("emailId") or payload.get("_id")
        address = payload.get("address") or payload.get("email")
        if not email_id or not address:
            raise RuntimeError(f"moemail 创建邮箱失败: {data}")
        return TempEmailInbox(address=address, token=str(email_id))

    def poll_verification_code(self, inbox: TempEmailInbox, timeout_seconds: int = 180) -> str | None:
        deadline = time.time() + timeout_seconds
        email_id = inbox.token
        while time.time() < deadline:
            try:
                resp = requests.get(
                    f"{self.config.api_url}/api/emails/{email_id}",
                    headers=self._headers(),
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                messages = data.get("messages") or (data.get("data") or {}).get("messages") or []
                for msg in messages:
                    message_id = msg.get("id") or msg.get("messageId") or msg.get("_id")
                    if not message_id:
                        continue
                    detail_resp = requests.get(
                        f"{self.config.api_url}/api/emails/{email_id}/{message_id}",
                        headers=self._headers(),
                        timeout=15,
                    )
                    detail_resp.raise_for_status()
                    detail = detail_resp.json()
                    body = detail.get("text") or detail.get("html") or detail.get("raw") or str(detail)
                    code = _extract_verification_code(str(body))
                    if code:
                        return code
            except Exception as exc:
                print(f"  email poll: {exc}", flush=True)
            time.sleep(2)
        return None


def _extract_verification_code(raw_message: str) -> str | None:
    clean = re.sub(r"=\r?\n", "", raw_message)
    index = clean.lower().find("verification code")
    if index >= 0:
        snippet = clean[index : index + 500]
        match = re.search(r"(\d{3})\s*[-–]\s*(\d{3})", snippet)
        if match:
            return match.group(1) + match.group(2)
    match = re.search(r"(?<!\d)(\d{3})[-–](\d{3})(?!\d)", clean)
    if match:
        return match.group(1) + match.group(2)
    return None


def _duckmail_message_body(detail: dict) -> str:
    parts: list[str] = []
    text = detail.get("text")
    if isinstance(text, str) and text.strip():
        parts.append(text)

    html = detail.get("html") or []
    if isinstance(html, list):
        for item in html:
            if isinstance(item, str) and item.strip():
                parts.append(item)
    elif isinstance(html, str) and html.strip():
        parts.append(html)

    return "\n".join(parts)


def build_email_provider(config: AppConfig) -> TempEmailProvider:
    if config.email_provider == "cloudflare_temp_email":
        return CloudflareTempEmailProvider(config.cloudflare_temp_email)
    if config.email_provider == "moemail":
        return MoeMailProvider(config.moemail)
    if config.email_provider == "duckmail":
        return DuckMailProvider(config.duckmail)
    raise ValueError(f"Unsupported email provider: {config.email_provider}")
