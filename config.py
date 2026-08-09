from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.toml"


@dataclass(frozen=True)
class CloudflareTempEmailConfig:
    api_url: str
    admin_auth: str
    domain: str


@dataclass(frozen=True)
class MoeMailConfig:
    api_url: str
    api_key: str
    domain: str | None  # None = 自动从上游 /api/config 获取


@dataclass(frozen=True)
class DuckMailConfig:
    api_url: str
    domain: str
    api_key: str | None


@dataclass(frozen=True)
class CaptchaConfig:
    mode: str
    yescaptcha_client_key: str | None
    yescaptcha_api_url: str
    captcharun_token: str | None
    captcharun_api_url: str
    poll_interval_seconds: int
    timeout_seconds: int


@dataclass(frozen=True)
class NvidiaConfig:
    output_csv: Path
    key_name: str
    account_name: str
    key_expiry_date: str


@dataclass(frozen=True)
class BrowserConfig:
    headless: bool
    close_delay_seconds: int
    proxy_enabled: bool
    proxy_url: str | None
    # 代理账号后缀列表(每账号换 IP): 如 ["nv.a","nv.b","nv.c"] -> 自动轮换
    proxy_accounts: list[str] | None
    # 账号间隔随机范围(秒)
    interval_min: int
    interval_max: int


@dataclass(frozen=True)
class AppConfig:
    email_provider: str
    cloudflare_temp_email: CloudflareTempEmailConfig
    moemail: MoeMailConfig
    duckmail: DuckMailConfig
    captcha: CaptchaConfig
    nvidia: NvidiaConfig
    browser: BrowserConfig


def _require_str(data: dict[str, Any], path: str) -> str:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"Missing required config: {path}")
        current = current[part]
    if not isinstance(current, str) or not current.strip():
        raise ValueError(f"Missing required config: {path}")
    return current.strip()


def _get_str(data: dict[str, Any], path: str, default: str) -> str:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current if isinstance(current, str) and current.strip() else default


def _get_int(data: dict[str, Any], path: str, default: int) -> int:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current if isinstance(current, int) else default


def _get_bool(data: dict[str, Any], path: str, default: bool) -> bool:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current if isinstance(current, bool) else default


def _get_list(data: dict[str, Any], path: str) -> list[str] | None:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    if isinstance(current, list) and all(isinstance(x, str) and x.strip() for x in current):
        return [x.strip() for x in current]
    return None


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else SCRIPT_DIR / path


def init_config() -> None:
    if CONFIG_FILE.exists():
        print(f"Config already exists: {CONFIG_FILE}")
        return
    template = """
email_provider = "moemail"

[moemail]
api_url = "https://zml.newbiz.eu.org"
api_key = "YOUR_API_KEY"
# 邮箱域名(可选, 留空则自动从上游 /api/config 获取)
domain = ""

[cloudflare_temp_email]
api_url = ""
admin_auth = ""
domain = ""

[duckmail]
api_url = "https://api.duckmail.sbs"
domain = "duckmail.sbs"
api_key = ""

[captcha]
mode = "manual" # manual | yescaptcha | captcharun
yescaptcha_client_key = ""
yescaptcha_api_url = "https://api.yescaptcha.com"
captcharun_token = ""
captcharun_api_url = "https://api.captcha-run.com"
poll_interval_seconds = 3
timeout_seconds = 180

[nvidia]
output_csv = "accounts.csv"
key_name = "api"
account_name = "NVIDIA Build"
key_expiry_date = "2126-05-08T08:00:00Z"

[browser]
headless = false
close_delay_seconds = 5
# 代理池(可选, 支持 Resin/GoProxy 等 HTTP 代理)
proxy_enabled = false
proxy_url = "http://user:pass@127.0.0.1:21978"
# 每账号换 IP: Resin 账号后缀列表, 如 ["a","b","c"] -> 生成 user.a/user.b/user.c
# 需在 Resin 平台下配置多个账号(不同后缀=不同出口 IP)
proxy_accounts = []
# 账号间隔随机范围(秒), 默认 20-60
interval_min = 20
interval_max = 60
"""
    CONFIG_FILE.write_text(template, encoding="utf-8")
    print(f"Created {CONFIG_FILE}")


def load_config() -> AppConfig:
    if not CONFIG_FILE.exists():
        print(f"Missing config file: {CONFIG_FILE}")
        print("Run: python main.py --init")
        sys.exit(1)

    with CONFIG_FILE.open("rb") as file:
        data = tomllib.load(file)

    email_provider = _get_str(data, "email_provider", "cloudflare_temp_email").lower()
    if email_provider not in {"cloudflare_temp_email", "moemail", "duckmail"}:
        raise ValueError(f"Unsupported email_provider: {email_provider}")

    use_cloudflare_temp_email = email_provider == "cloudflare_temp_email"
    use_moemail = email_provider == "moemail"
    use_duckmail = email_provider == "duckmail"

    cloudflare_api_url = (
        _require_str(data, "cloudflare_temp_email.api_url")
        if use_cloudflare_temp_email
        else _get_str(data, "cloudflare_temp_email.api_url", "")
    ).rstrip("/")
    cloudflare_admin_auth = (
        _require_str(data, "cloudflare_temp_email.admin_auth")
        if use_cloudflare_temp_email
        else _get_str(data, "cloudflare_temp_email.admin_auth", "")
    )
    cloudflare_domain = (
        _require_str(data, "cloudflare_temp_email.domain")
        if use_cloudflare_temp_email
        else _get_str(data, "cloudflare_temp_email.domain", "")
    )

    moemail_api_url = (
        _require_str(data, "moemail.api_url")
        if use_moemail
        else _get_str(data, "moemail.api_url", "https://zml.newbiz.eu.org")
    ).rstrip("/")
    moemail_api_key = (
        _require_str(data, "moemail.api_key")
        if use_moemail
        else _get_str(data, "moemail.api_key", "")
    )
    # 域名可选: 留空则运行时从上游 /api/config 自动获取
    moemail_domain = _get_str(data, "moemail.domain", "") or None

    duckmail_domain = (
        _require_str(data, "duckmail.domain")
        if use_duckmail
        else _get_str(data, "duckmail.domain", "")
    )
    duckmail_api_key = _get_str(data, "duckmail.api_key", "") or None

    captcha_mode = _get_str(data, "captcha.mode", "manual").lower()
    if captcha_mode not in {"manual", "yescaptcha", "captcharun"}:
        raise ValueError("captcha.mode must be 'manual', 'yescaptcha' or 'captcharun'")
    yescaptcha_client_key = _get_str(data, "captcha.yescaptcha_client_key", "") or None
    if captcha_mode == "yescaptcha" and not yescaptcha_client_key:
        raise ValueError("captcha.yescaptcha_client_key is required when captcha.mode = 'yescaptcha'")
    captcharun_token = _get_str(data, "captcha.captcharun_token", "") or None
    if captcha_mode == "captcharun" and not captcharun_token:
        raise ValueError("captcha.captcharun_token is required when captcha.mode = 'captcharun'")

    return AppConfig(
        email_provider=email_provider,
        cloudflare_temp_email=CloudflareTempEmailConfig(
            api_url=cloudflare_api_url,
            admin_auth=cloudflare_admin_auth,
            domain=cloudflare_domain,
        ),
        moemail=MoeMailConfig(
            api_url=moemail_api_url,
            api_key=moemail_api_key,
            domain=moemail_domain,
        ),
        duckmail=DuckMailConfig(
            api_url=_get_str(data, "duckmail.api_url", "https://api.duckmail.sbs").rstrip("/"),
            domain=duckmail_domain,
            api_key=duckmail_api_key,
        ),
        captcha=CaptchaConfig(
            mode=captcha_mode,
            yescaptcha_client_key=yescaptcha_client_key,
            yescaptcha_api_url=_get_str(data, "captcha.yescaptcha_api_url", "https://api.yescaptcha.com").rstrip("/"),
            captcharun_token=captcharun_token,
            captcharun_api_url=_get_str(data, "captcha.captcharun_api_url", "https://api.captcha-run.com").rstrip("/"),
            poll_interval_seconds=_get_int(data, "captcha.poll_interval_seconds", 3),
            timeout_seconds=_get_int(data, "captcha.timeout_seconds", 180),
        ),
        nvidia=NvidiaConfig(
            output_csv=_resolve_path(_get_str(data, "nvidia.output_csv", "accounts.csv")),
            key_name=_get_str(data, "nvidia.key_name", "api"),
            account_name=_get_str(data, "nvidia.account_name", "NVIDIA Build"),
            key_expiry_date=_get_str(data, "nvidia.key_expiry_date", "2126-05-08T08:00:00Z"),
        ),
        browser=BrowserConfig(
            headless=_get_bool(data, "browser.headless", False),
            close_delay_seconds=_get_int(data, "browser.close_delay_seconds", 10),
            proxy_enabled=_get_bool(data, "browser.proxy_enabled", False),
            proxy_url=_get_str(data, "browser.proxy_url", "") or None,
            proxy_accounts=_get_list(data, "browser.proxy_accounts"),
            interval_min=_get_int(data, "browser.interval_min", 20),
            interval_max=_get_int(data, "browser.interval_max", 60),
        ),
    )


def describe_config(config: AppConfig) -> None:
    if config.email_provider == "cloudflare_temp_email":
        email_api = config.cloudflare_temp_email.api_url
        email_domain = config.cloudflare_temp_email.domain
    elif config.email_provider == "moemail":
        email_api = config.moemail.api_url
        email_domain = config.moemail.domain or "(auto)"
    else:
        email_api = config.duckmail.api_url
        email_domain = config.duckmail.domain

    print(f"  EMAIL_PROVIDER: {config.email_provider}")
    print(f"  EMAIL_API:      {email_api}")
    print(f"  EMAIL_DOMAIN:   {email_domain}")
    print(f"  CAPTCHA_MODE:   {config.captcha.mode}")
    if config.browser.proxy_enabled:
        print(f"  PROXY:          {config.browser.proxy_url}")
        if config.browser.proxy_accounts:
            print(f"  PROXY_ACCOUNTS: {len(config.browser.proxy_accounts)} 个(每账号轮换)")
    print(f"  OUTPUT_CSV:     {config.nvidia.output_csv}")
    print(f"  CONFIG_FILE:    {CONFIG_FILE}")
