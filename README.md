# nvidia-register

自动注册 **NVIDIA BUILD 账号**并创建 **AI_PLAYGROUNDS API Key** 的小工具。

> 用大白话说：你提供一个临时邮箱，它帮你自动完成"注册 NVIDIA 账号 → 建组织 → 拿 API Key"的全流程，最后把 `邮箱、密码、Key` 记到 CSV 里。

---

## 它能干什么

- ✅ 自动注册 build.nvidia.com 账号（邮箱验证码全自动）
- ✅ 自动创建组织（跳过手机号验证）
- ✅ 自动创建 **AI_PLAYGROUNDS_KEY**（有效期到 2126 年，可调）
- ✅ 批量注册（`-n 5` 一次 5 个）
- ✅ 账号间隔随机 20-60 秒，降低风控
- ✅ 每个账号独立随机密码（12 位，大小写+数字）
- ✅ 临时邮箱域名随机（不固定一个，防封）
- ✅ 支持代理池（可选，Resin/GoProxy 等 HTTP 代理）

**拿到 API Key 后**，可以当 OpenAI 兼容接口用：

```
Base URL: https://integrate.api.nvidia.com/v1
API Key:  nvapi-xxxxxxxxxxxxxxxx
模型:     nvidia/llama-3.1-nemotron-70b-instruct 等
```

---

## 需要准备什么

| 项目 | 说明 | 必须？ |
|---|---|---|
| **moemail 服务** | 临时邮箱 API（自部署），提供 `API URL + API Key` | ✅ |
| **YesCaptcha 或 CaptchaRun** | 自动过 hCaptcha 验证码服务，提供 client key | ✅ |
| **Linux 服务器** | 跑脚本用（有显示器也行）| ✅ |
| Resin/代理池 | 可选，批量时换 IP 防风控 | 可选 |

> moemail 自部署参考：https://github.com/beilunyang/moemail
> YesCaptcha 注册：https://yescaptcha.com（充几块钱就够跑很久）

---

## 安装

### 1. 拉代码

```bash
git clone https://github.com/justincnn/nvidia-register.git
cd nvidia-register
```

### 2. 装依赖

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python3 -m playwright install chromium
```

### 3. 装 xvfb（服务器无显示器必须）

> **为什么必须**：NVIDIA 的 cloudaccounts 页面（创建组织那步）会拦截无头浏览器（headless），页面空白导致注册卡住。用 xvfb 模拟显示器就能正常跑。

```bash
# Ubuntu/Debian
sudo apt install -y xvfb
```

---

## 配置

```bash
.venv/bin/python3 main.py --init    # 生成配置模板
```

编辑 `config.toml`：

```toml
email_provider = "moemail"

[moemail]
api_url = "https://你的moemail域名"
api_key = "你的moemail管理Key"
# domain 留空 = 自动从上游获取并随机选择

[captcha]
mode = "yescaptcha"        # yescaptcha | captcharun | manual
yescaptcha_client_key = "你的YesCaptchaKey"
# captcharun_token = "你的CaptchaRunToken"   # 如果用 captcharun 就填这个

[nvidia]
output_csv = "accounts.csv"
key_name = "api"
account_name = "NVIDIA Build"
key_expiry_date = "2126-05-08T08:00:00Z"

[browser]
headless = false           # 必须 false(xvfb 跑), true 会被拦截
close_delay_seconds = 5
proxy_enabled = false      # 可选: true 则走代理
# proxy_url = "http://用户:密码@127.0.0.1:21978"
# proxy_accounts = ["a", "b", "c"]   # 每账号换 IP 的账号后缀
interval_min = 20
interval_max = 60
```

---

## 运行

```bash
# 服务器(无显示器) - 必须用 xvfb-run
xvfb-run -a .venv/bin/python3 main.py -n 1

# 有显示器的电脑
.venv/bin/python3 main.py -n 1

# 批量 5 个
xvfb-run -a .venv/bin/python3 main.py -n 5
```

**参数**：`-n 数量` 或 `--count 数量`；不加则交互询问。

**输出**：每个成功账号追加一行到 `accounts.csv`：

```csv
email,password,apikey
nv12345678@xxx.expressai.eu.org,Ab12Cd34Ef56,nvapi-xxxxx
```

---

## 常见问题 FAQ

### Q1: 注册卡在"创建组织"页（select-account 空白）？
**原因**：headless 模式被 NVIDIA 拦截。
**解决**：确认 `config.toml` 里 `headless = false`，并且用 `xvfb-run` 启动。

### Q2: 提示 hCaptcha sitekey 未找到 / 验证码超时？
检查 `captcha.mode` 和对应 key 是否填对（yescaptcha 填 `yescaptcha_client_key`，captcharun 填 `captcharun_token`）。

### Q3: 邮箱域名一直是一个？
旧版 bug（固定第一个域名），已修复为随机。确认代码是最新（`git pull`）。

### Q4: 代理开了反而失败/慢？
NVIDIA 对数据中心 IP 风控较严，代理出口质量差时建议 `proxy_enabled = false` 直连。

### Q5: 批量中途失败了怎么办？
失败账号自动跳过继续下一个，最终显示"成功 X, 失败 Y"。失败的可以重跑（新邮箱）。

### Q6: 账号密码忘了在哪看？
CSV 文件里每一行都有 `email,password,apikey`。

---

## 技术原理（简述）

```
1. moemail 创建临时邮箱
2. build.nvidia.com 填邮箱 → 跳转注册页
3. 填密码 + YesCaptcha 自动过 hCaptcha
4. moemail 收验证码 → 自动输入
5. 同意页(consent) → 创建组织(select-account, 跳过手机验证)
6. 调 NGC API 建 AI_PLAYGROUNDS_KEY
7. 写入 CSV
```

> 关键点：第 5 步的 select-account 页面必须**非 headless** 才能渲染，这就是为什么用 xvfb。

---

## 免责声明

本项目仅供学习交流。使用前请确认符合 NVIDIA 服务条款，作者不对因使用本工具导致的账号限制或封禁负责。
