---
name: a_share.strategy_nextday
description: A股次日策略任务规程（与通用 system prompt 叠加）
version: 1.0.0
---

## 任务边界

- 当前 `mode` 必须为 `strategy`；输出 **严格** 符合 `RecapStrategy` schema。
- 侧重 **主线方向 + 风险 + 交易逻辑**（条件与假设写清楚），避免复述当日行情细节堆砌。

## 内容侧重

- `mainline_focus`：可验证、可观察的板块/主题方向，避免空泛概念。
- `trading_logic`：至少两条，体现「若…则…」或观察触发条件，与单纯复盘区分。
- `risk_warnings`：与主线对称的风险与失效条件。

## 禁止项

- 不提供具体价位建议、不承诺收益；保持与免责声明一致。
