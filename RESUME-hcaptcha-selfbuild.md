# hCaptcha 自研破解 (local-vlm) — 暂停/重启存档 (2026-08-22)

## 状态
**暂停，晚点续。config 已还原 production（captcharun + proxy=true）。**

## 已达成（git 已提交）
- `b29c09c` 付费 captcharun 基线 9/10（生产兜底）
- `cbfb2e6` local-vlm: canvas drag/area solver（Qwen-VL + SRC/DST 提示词）
- `ee70c6c` local-vlm: label-td tile 检测 + "I am human" checkbox 处理

## 已确证事实（续跑直接可用）
1. hCaptcha 题型轮换：image_drag_drop / image_label_area_select / image_label_binary(label-td)
2. tile grid = 自定义元素 `label-td`/`label-tc`（非 .option），已在 TILE_SELECTS
3. canvas 链路通：VLM(canvas 截图)→定位→verify 被 hCaptcha 接受（会下发新挑战）
4. VLM = SiliconFlow `Qwen/Qwen3-VL-8B-Instruct`（key 在 /tmp/vlm_creds）
   - 坑：Qwen 原生 JSON bbox 是坏的（`{"x1":812,567}`）；用「描述 + 行尾 `SRC x,y DST x,y`」+ 正则最稳
5. 终极墙（暂缓主因）：**无 stealth 的 headless Chromium 被 hCaptcha 指纹** → checkbox/任务全过但仍无限重发新题，register 永不启用。**要真正注册成功须加 stealth 浏览器层**。

## 续跑清单（晚点）
- 给 nvidia-register 启 noise 用 sneak: playwright-stealth / undetected-chromedriver 提供原生指纹
- 先离线验 sneak 能过 hCaptcha demo checkbox 再上全链路
- 每 e2e 烧 1 探针账号+~3min；hCaptcha 持续换题型对抗，成功率天生不确保

## 恢复
```
cd /home/ubuntu/nvidia-register   # config.toml mode:captcharun(生产) ; 续自研改 local-vlm
```