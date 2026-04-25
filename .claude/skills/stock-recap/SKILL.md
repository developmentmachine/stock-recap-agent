---
name: A股复盘
description: 生成今日A股日终复盘或次日策略报告，调用 stock-recap-agent 服务
when-to-use: 当用户想要生成A股复盘、次日策略、查看历史复盘记录时
argument-hint: "[daily|strategy] [mock|live] [YYYY-MM-DD]"
arguments: [mode, provider, date]
allowed-tools: [Bash, Read]
user-invocable: true
---

# A股复盘 Skill

根据参数生成 A 股复盘报告。

## 参数说明

- `mode`：`daily`（日终复盘，默认）或 `strategy`（次日策略）
- `provider`：`mock`（测试数据，默认）或 `live`（真实行情，需网络）
- `date`：指定日期 `YYYY-MM-DD`，不传则用今天

## 执行步骤

1. 解析用户传入的参数（mode / provider / date），缺省值：mode=daily, provider=mock
2. 进入项目目录并运行 stock-recap-agent
3. 输出复盘报告

## 运行命令

```bash
cd /Users/zhaichuancheng/DevelopSpace/stock-recap-agent

# 构建参数
MODE=${mode:-daily}
PROVIDER=${provider:-mock}
DATE_ARG=""
if [ -n "${date}" ]; then
  DATE_ARG="--date ${date}"
fi

uv run -m agent_platform --mode $MODE --provider $PROVIDER $DATE_ARG --dry-run 2>/dev/null || \
uv run -m agent_platform --mode $MODE --provider $PROVIDER $DATE_ARG
```

执行上述命令后，将输出结果以清晰的格式展示给用户：
- 如果是 `daily` 模式，展示三大板块分析
- 如果是 `strategy` 模式，展示主线方向和交易逻辑
- 同时显示本次运行的 request_id 和耗时

## 快捷用法示例

用户可以这样调用：
- `/stock-recap` — 今日日终复盘（mock 数据）
- `/stock-recap daily live` — 今日日终复盘（真实数据）
- `/stock-recap strategy live` — 次日策略（真实数据）
- `/stock-recap daily live 2024-01-02` — 指定日期复盘
