# Stock Recap Agent — 架构与业务说明

本文面向需要在一段时间内熟悉本仓库的开发者：说明**业务目标**、**端到端流程**、**代码分层与模块职责**，并总结**设计取舍的收益**与**已知局限**，便于后续升级迭代。

建议阅读顺序：**业务背景 → 核心流程 → 分层架构 → 数据与闭环 → LLM/工具 → 收益与缺点**。

---

## 1. 项目定位与业务背景

### 1.1 要解决什么问题

本系统面向 **A 股市场**，在交易日结束后或盘前，自动完成两类文案型产出：

| 模式 | 业务含义 | 典型使用时机 |
|------|----------|----------------|
| **daily（日终复盘）** | 基于当日行情与情绪等数据，生成结构化「复盘」报告（多段论述 + 风险 + 收束） | 收盘后 |
| **strategy（次日策略）** | 基于同一套市场输入，生成「主线、风险、交易逻辑」等策略取向文案 | 收盘后或次日盘前 |

产出需满足：**可追溯（数据与版本）**、**可评测（自动规则 + 回测）**、**可演进（用户反馈驱动 prompt 版本）**，并支持 **HTTP API / CLI / 定时任务** 多种触发方式。

### 1.2 非目标（边界）

- 不提供实盘下单、券商接口或实时交易执行。
- 默认定位是「研究/运营辅助文案」：输出中的免责声明在领域模型与护栏中强调**不构成投资建议**。
- 不是通用多轮对话 Agent：主路径是**单次请求 → 单次完整生成管线**；会话 ID 主要用于遥测关联，而非完整对话状态机。

---

## 2. 核心业务概念

### 2.1 市场数据（Provider）

- **live**：真实行情与衍生数据；内部可对多数据源做 fallback（如腾讯/新浪/AkShare 等，实现细节见 `infrastructure/data`）。
- **mock**：确定性随机数据，用于无网络/无密钥环境下的 CI、本地联调。

业务上：**live 保证「与真实市场一致」**；**mock 保证「可重复、可测」**。

### 2.2 市场快照与特征

- **MarketSnapshot**（`domain/models.py`）：某一「业务日」下的原始采集结果（指数、情绪、板块、外盘、商品等），带 `date`、`provider`、`sources` 等元数据。
- **Features**（`domain/models.py`）：在快照之上的数值/文本摘要，用于压缩进 Prompt、降低模型胡编空间。

设计意图：**快照可追溯、特征可评测**（`auto_eval` 会对照快照/特征做检查）。

### 2.3 结构化输出（Recap）

LLM 被要求输出 **JSON**，并校验为 **Pydantic 模型**：

- **RecapDaily**：日终复盘（固定三段结构 + `risks` + `closing_summary` + `disclaimer`）。
- **RecapStrategy**：次日策略（主线、风险、交易逻辑列表 + `disclaimer`）。

业务收益：**下游渲染（Markdown / 企微）稳定**、**便于落库与 diff**、**评测规则可写在代码里**。

### 2.4 Prompt 版本与 Skill

- **prompt_version**：由 manifest 基础版本 + 进化模块可能 bump 的 `vN` 组成；用于追溯「当时用的是哪套指令」。
- **Skill**（`skills/` + `resources/prompts/`）：按 `mode` 叠加的额外指令文本，支持 `RECAP_SKILL_ID` 覆盖映射，便于 A/B。

---

## 3. 端到端业务流程（Agent 管线）

主业务入口是 **`generate_once`**（`application/recap.py`），实际阶段逻辑在 **`execute_recap_pipeline`** / **`iter_recap_agent_ndjson`**（`application/orchestration/pipeline.py`）。

### 3.1 阶段一览（与设计意图）

| 阶段名（遥测/NDJSON） | 职责 | 业务含义 |
|------------------------|------|----------|
| **perceive** | `collect_snapshot` + `build_features` | 「感知」市场环境 |
| **recall** | 历史 run 摘要、进化指导、反馈摘要、可选模式提炼、回测摘要、当前 prompt 版本 | 「记忆 + 元学习上下文」 |
| **plan** | `build_messages` + `clamp_llm_messages` | 「规划」本次发给模型的指令包 |
| **act** | `call_llm`（可选工具循环/预取）+ 输出护栏 + 渲染 | 「行动」生成结构化 Recap |
| **critique** | `auto_eval` | 「批判」自动检查与当日数据的一致性等 |
| **persist** | `insert_run` | 「持久化」审计与后续记忆来源 |
| **reflect** | 进化检查、推送、（非 defer 时）回测触发 | 「副作用」运营闭环 |

**HTTP `/v1/recap`**：`reflect` 中的进化与回测可 **defer** 到 `BackgroundTasks`，缩短 JSON 响应尾部延迟；**推送仍在请求内完成**，以便响应里仍有 `push_result`。

**HTTP `/v1/recap/stream`**：NDJSON 流式输出各阶段；**流成功结束**后才执行与 JSON 路径等价的延后进化/回测；**阶段失败**时输出 `event: error` 并不再触发延后任务。

### 3.2 触发方式差异（业务同一、运维不同）

| 入口 | 典型场景 | 备注 |
|------|----------|------|
| **CLI**（`interfaces/cli.py` → `generate_once`） | 本地运维、脚本、写文件 | 默认 **不 defer** 进化/回测（与线上一致性简单） |
| **API POST `/v1/recap`** | 系统集成 | defer + `BackgroundTasks` |
| **API POST `/v1/recap/stream`** | 需要进度展示的前端 | NDJSON；无 `ContextVar` 父 span（线程池限制） |
| **调度器**（`interfaces/scheduler/jobs.py`） | 固定时刻日终/策略 | 交易日检查；`generate_once` 全链路 |

---

## 4. 技术架构（分层）

整体是 **六边形/整洁架构的变体**：领域与用例在内，基础设施与接口在外。

```
                    ┌─────────────────────────────────────┐
                    │  interfaces/                        │
                    │  CLI · FastAPI · MCP stdio · 调度    │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │  application/                       │
                    │  recap · orchestration · memory     │
                    │  （编排、Agent 阶段、记忆/进化）      │
                    └──────────────┬──────────────────────┘
           ┌───────────────────────┼───────────────────────┐
           │                       │                       │
┌──────────▼─────────┐  ┌──────────▼─────────┐  ┌─────────▼──────────┐
│ domain/            │  │ policy/            │  │ presentation/    │
│ 模型 · RunContext   │  │ 输入/输出护栏       │  │ Markdown/企微渲染 │
└──────────┬─────────┘  └────────────────────┘  └────────────────────┘
           │
┌──────────▼─────────────────────────────────────────────────────────┐
│ infrastructure/                                                   │
│  data · llm · tools · persistence · push · resources/skills     │
└───────────────────────────────────────────────────────────────────┘
           ┌──────────────────┐
           │ observability/   │
           │ OTEL · ContextVar │
           └──────────────────┘
```

### 4.1 包级索引（按职责）

| 路径 | 职责 |
|------|------|
| `domain/` | 与框架无关的类型：`GenerateRequest`、`GenerateResponse`、`MarketSnapshot`、`Features`、`Recap*`、`RunContext` 等 |
| `application/recap.py` | `generate_once`、`iter_generate_ndjson`；外层 OTEL span +（非流）`RunContext` |
| `application/orchestration/` | `RecapAgentRunState`、分阶段函数、`execute_recap_pipeline`、`iter_recap_agent_ndjson` |
| `application/memory/manager.py` | 历史记忆加载、模式提炼、**进化周期**、`prompt_version` 进程内缓存 |
| `application/side_effects/` | 副作用（侧效）层：`backtest` / `evolution` / `push` / `deferred`（供 API `BackgroundTasks`、调度器、CLI 共用）；`application/recap_support.py` 为兼容 shim |
| `application/agent.py` | 薄封装 `RecapAgent.run` → `generate_once` |
| `policy/guardrails.py` | 请求校验、消息截断、`coerce_recap_output`（免责回填） |
| `infrastructure/data/` | 采集、特征、日历、各数据源 |
| `infrastructure/llm/` | 多后端 `call_llm`、prompt 组装、自动评测 |
| `infrastructure/tools/` | `RecapToolRunner`、registry、各 tool handler |
| `infrastructure/persistence/db.py` | SQLite：runs、feedback、evolution、backtest、指标 |
| `infrastructure/push/` | 企微等推送 |
| `interfaces/api/routes.py` | FastAPI：鉴权、限流、CORS、JSON/流式 recap、反馈、历史 |
| `interfaces/cli.py` | 命令行入口 |
| `interfaces/mcp_stdio.py` | MCP 工具进程（与进程内 tools 语义对齐） |
| `interfaces/scheduler/jobs.py` | APScheduler 与交易日逻辑 |
| `config/settings.py` | Pydantic Settings / 环境变量 |
| `observability/` | OpenTelemetry 与 `ContextVar` 运行上下文 |
| `resources/prompts/`、`skills/` | 版本化 system prompt 与 skill 叠加 |

---

## 5. 数据与持久化

### 5.1 核心表（概念）

- **recap_runs**：每次生成的完整记录（快照、特征、recap JSON、渲染结果、评测、错误、延迟、token、prompt_version、model…）。
- **recap_feedback**：用户对某次 `request_id` 的评分与评论。
- **evolution_notes**：进化分析的结构化笔记 + 建议的 prompt 版本 bump。
- **backtest 相关**：策略日与实际日的命中等（见 `BacktestResult` 与 db 模块）。

### 5.2 业务价值

- **审计**：任意一次输出可还原「当时数据 + 当时模型 + 当时 prompt 版本」。
- **记忆**：后续 run 的 `recall` 阶段会读近期摘要，形成「纵向一致」的叙述习惯（在数据允许范围内）。
- **进化**：低分反馈或累计条件可触发 LLM 自我诊断，推动 **prompt 版本** 与 system 文案迭代。

---

## 6. LLM 与工具设计

### 6.1 后端抽象

`LlmBackend` 支持 `openai`、`ollama`、`cursor-cli`、`gemini-cli`（见 `domain/models.py` 与 `infrastructure/llm/backends.py`）。统一入口 **`call_llm`**，对上层屏蔽协议差异。

### 6.2 工具（Agent 能力边界）

- **OpenAI / Ollama**：原生 function calling 循环（有轮次上限），工具策略集中在 **`RecapToolRunner`**（schema 过滤 + 执行 + 预取）。
- **Cursor CLI / Gemini CLI**：无原生 tool 循环时，用 **预执行结果注入** prompt，与「工具语义」对齐但**非按需调用**。
- **独立 MCP**：`interfaces/mcp_stdio.py` 暴露同名工具，供 Cursor Desktop 等宿主调用。

**业务收益**：同一套工具能力在「进程内 LLM」与「外部 MCP 宿主」间可对照。

**业务代价**：不同 LLM 后端在「工具是否按需」上不完全一致，文档与测试需区分说明。

---

## 7. 这样设计的主要好处

1. **业务与技术解耦**：领域模型稳定，换采集源、换 LLM、换存储实现，主要动 `infrastructure` 与配置。
2. **单次管线清晰**：适合「日更报告类」强编排场景，排障路径短（按阶段打日志/span）。
3. **结构化输出 + 自动评测**：比纯文本更利于合规检查、渲染与回测对齐。
4. **闭环运营**：反馈 → 进化 → prompt 版本；策略 vs 次日实际 → 回测命中率。
5. **多入口一致**：`generate_once` 聚合 CLI/API/调度，减少「各写一套」的漂移。
6. **渐进增强**：从 JSON API → NDJSON 流 →（未来）队列/多轮，可在接口层逐步加。

---

## 8. 已知缺点与建议迭代方向

下列条目**不是实现错误**，而是架构级取舍；便于你后续排期。

| 局限 | 说明 | 可能演进 |
|------|------|----------|
| **非通用对话 Agent** | 无多轮会话存储与状态机；`X-Session-Id` 偏遥测 | 引入会话表 + 消息历史 API；或明确保持「无状态报告服务」 |
| **流式路径的上下文** | NDJSON 迭代在线程池中执行，未挂 `ContextVar`/父 span，避免跨线程 detach 失败 | 专用 async 流 + 单线程迭代；或接受「流式仅业务元数据 trace」 |
| **延后任务仍在进程内** | `BackgroundTasks` / 流结束同步调用，进程崩溃可能丢失尾部任务 | Redis/RQ、Outbox 表 + worker |
| **工具语义后端不一致** | CLI 类后端为预取，OpenAI 类为按需 tool | 统一走 MCP 或统一「预取策略」配置 |
| **评测以规则为主** | `auto_eval` 偏启发式，非人类偏好全集 | 引入 golden case、LLM-as-judge（需成本与治理） |
| **单 SQLite** | 水平扩展与多副本写入需外置 DB 或分片策略 | Postgres + 迁移脚本 |
| **限流在内存** | 多实例部署时节流不共享 | Redis 令牌桶或网关限流 |
| **CORS 静态配置** | 启动时读取 `RECAP_CORS_ORIGINS` | 热更新或按租户配置 |

---

## 9. 建议你本地跟读代码的路径

1. `domain/models.py`：搞清请求/响应与 Recap 形状。  
2. `application/orchestration/pipeline.py`：对照本文「阶段一览」逐函数阅读。  
3. `application/recap.py`：`generate_once` 与 `iter_generate_ndjson` 的外层差异。  
4. `infrastructure/llm/prompts.py` + `resources/prompts/`：最终发给模型的信息如何拼装。  
5. `infrastructure/persistence/db.py`：表结构与 `insert_run` / `load_recent_runs`。  
6. `application/memory/manager.py`：进化与 `prompt_version`。  
7. `interfaces/api/routes.py`：对外契约与安全策略。

---

## 10. 文档维护说明

- 本文描述的是**仓库当前设计意图**；若你改动阶段名、API 路径或表结构，请同步更新本节与「包级索引」。  
- 用户可见的运行说明仍以仓库根目录 **`README.md`** 为准；本文侧重**架构与业务设计**。

---

*文档生成自当前代码结构；若你发现与代码不一致之处，以代码与测试为准并欢迎修正本文。*
