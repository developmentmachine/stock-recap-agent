# Agent Platform 使用文档

## 一、本地运行

### 环境准备

```bash
# 克隆项目
git clone <repo-url>
cd stock-recap-agent

# 安装依赖（需要 Python 3.11+）
pip install uv
uv sync
```

复制配置文件：

```bash
cp .env.example .env
```

编辑 `.env`，至少配置 LLM 后端（见第三节）。

---

### 命令格式

本项目是多智能体平台，通过子命令指定要运行的 agent：

```
uv run agent_platform <agent-name> [参数]
```

目前内置 agent：

| agent | 说明 |
|-------|------|
| `stock-recap` | A股日终复盘 / 次日策略智能体 |

查看帮助：

```bash
uv run agent_platform --help
uv run agent_platform stock-recap --help
```

> **免 `uv run` 前缀**：执行 `uv tool install --editable .` 将平台安装为全局命令，
> 之后可直接使用 `agent_platform stock-recap ...`（代码修改实时生效）。

---

### 快速测试（无需 API Key）

```bash
# mock 数据，不调用 LLM，验证环境是否正常
uv run agent_platform stock-recap --mode daily --provider mock --no-llm

# mock 数据 + 查看将发给 LLM 的 payload
uv run agent_platform stock-recap --mode daily --provider mock --dry-run
```

---

### 生成复盘

```bash
# 日终复盘（真实行情）
uv run agent_platform stock-recap --mode daily --provider live --model cursor-cli

# 次日策略
uv run agent_platform stock-recap --mode strategy --provider live --model cursor-cli

# 指定日期
uv run agent_platform stock-recap --mode daily --provider live --model cursor-cli --date 2024-01-02

# 不写文件，仅输出到 stdout
uv run agent_platform stock-recap --mode daily --provider mock --no-write-files
```

provider 只有两个选项：
- `live` — 真实行情，内部自动 fallback（腾讯/新浪/AkShare）
- `mock` — 确定性随机数据，用于测试/离线

---

### 启动 API 服务

```bash
# 基础启动
uv run agent_platform stock-recap --serve --host 0.0.0.0 --port 8000

# 带调度器（每天 15:30 自动触发）
RECAP_SCHEDULER_ENABLED=true uv run agent_platform stock-recap --serve
```

API 文档访问：http://localhost:8000/docs

---

### 其他命令

```bash
# 查看历史记录
uv run agent_platform stock-recap --history --limit 20

# 手动触发进化分析
uv run agent_platform stock-recap --evolve

# 手动回测昨日策略
uv run agent_platform stock-recap --backtest

# 测试企业微信推送
RECAP_WXWORK_WEBHOOK_URL=https://... uv run agent_platform stock-recap --push-test
```

---

### 数据库

默认持久化到工作目录下的 `recap_system.db`（WAL 模式、跨进程安全）。

```bash
# 自定义路径
RECAP_DB_PATH=./data/recap.db uv run agent_platform stock-recap --mode daily --provider mock

# 仅单进程测试用：重启后数据清空，且多线程/多 worker 下不安全
RECAP_DB_PATH=:memory: uv run agent_platform stock-recap --mode daily --provider mock
```

---

## 二、容器部署

### 单容器运行（容器内持久化）

```bash
# 构建镜像
docker build -t stock-recap .

# 运行（默认写入容器内 recap_system.db；容器销毁即丢失，生产请挂卷——见下一小节）
docker run -d \
  -p 8000:8000 \
  -e RECAP_LLM_BACKEND=gemini-cli \
  -e GEMINI_API_KEY=your-key \
  --name recap \
  stock-recap
```

---

### 持久化数据库

```bash
mkdir -p ./data

docker run -d \
  -p 8000:8000 \
  -e RECAP_DB_PATH=/data/recap.db \
  -e RECAP_LLM_BACKEND=openai \
  -e OPENAI_API_KEY=sk-... \
  -v $(pwd)/data:/data \
  --name recap \
  stock-recap
```

---

### docker-compose（推荐）

创建 `.env` 文件：

```bash
RECAP_LLM_BACKEND=openai
OPENAI_API_KEY=sk-...
RECAP_DB_PATH=/data/recap.db
RECAP_SCHEDULER_ENABLED=true
RECAP_PUSH_ENABLED=false
```

启动：

```bash
# 取消 docker-compose.yml 中 volumes 的注释，然后：
docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

---

### 健康检查

```bash
curl http://localhost:8000/healthz
# {"ok": true, "time": "...", "prompt_version": "..."}
```

---

## 三、集成到 AI 大模型

系统通过 `RECAP_LLM_BACKEND` 和 `RECAP_MODEL` 两个环境变量控制使用哪个模型。

---

### OpenAI / 兼容接口

```bash
OPENAI_API_KEY=sk-...
RECAP_LLM_BACKEND=openai
RECAP_MODEL=gpt-4.1-mini        # 或 gpt-4o、gpt-4.1 等
```

兼容 OpenAI 接口的服务（如 DeepSeek、Moonshot）：

```bash
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.deepseek.com/v1
RECAP_LLM_BACKEND=openai
RECAP_MODEL=deepseek-chat
```

---

### Gemini CLI（本地）

需先安装 [gemini-cli](https://github.com/google-gemini/gemini-cli)：

```bash
RECAP_LLM_BACKEND=gemini-cli
GEMINI_API_KEY=your-key          # 可选，gemini-cli 已登录时不需要
RECAP_GEMINI_CLI_CMD=gemini      # gemini-cli 的命令名
RECAP_GEMINI_TIMEOUT_S=300
```

运行：

```bash
uv run agent_platform stock-recap --mode daily --provider mock
# 等价于：
uv run agent_platform stock-recap --mode daily --provider mock --model gemini-cli
```

---

### Cursor CLI（本地）

官方说明见 [Cursor 命令列介面（CLI）](https://cursor.com/zh-Hant/docs/cli/overview)。终端中可执行的是 **`agent`**，本仓库配置里将该后端命名为 **`cursor-cli`**。

需先安装 Cursor CLI 并完成登录：

```bash
RECAP_LLM_BACKEND=cursor-cli
RECAP_CURSOR_CLI_CMD=agent
RECAP_CURSOR_TIMEOUT_S=300
```

（仍支持旧环境变量名 `RECAP_CURSOR_AGENT_CMD`、`RECAP_LLM_BACKEND=cursor-agent` 与 `--model cursor-agent`。）

或在命令行临时指定：

```bash
uv run agent_platform stock-recap --mode daily --provider mock --model cursor-cli
```

---

### Ollama（本地模型）

需先启动 Ollama 并拉取模型：

```bash
ollama pull qwen2.5:14b
```

配置：

```bash
RECAP_LLM_BACKEND=ollama
RECAP_MODEL=qwen2.5:14b
RECAP_OLLAMA_BASE_URL=http://127.0.0.1:11434
```

或命令行指定：

```bash
uv run agent_platform stock-recap --mode daily --provider mock --model ollama:qwen2.5:14b
```

---

### 模型表达式速查

`--model` 参数支持以下格式：

| 表达式 | 后端 | 说明 |
|--------|------|------|
| `openai:gpt-4o` | OpenAI | 指定模型名 |
| `ollama:qwen2.5:14b` | Ollama | 指定本地模型 |
| `cursor-cli` | Cursor CLI | 使用官方 `agent` 命令（`cursor-agent` 为兼容别名） |
| `gemini-cli` | Gemini | 使用 Gemini CLI |
| `gemini:gemini-2.0-flash` | Gemini | 指定 Gemini 模型 |

---

### MCP 工具（联网增强）

启用后 LLM 可主动查询实时行情、历史记录：

```bash
RECAP_TOOLS_ENABLED=true
RECAP_TOOLS_WEB_SEARCH=true      # 联网搜索（duckduckgo，免费）
RECAP_TOOLS_MARKET_DATA=true     # akshare 行情查询
RECAP_TOOLS_HISTORY=true         # 内部历史复盘查询
```

OpenAI / Ollama 后端使用原生 function calling；Cursor CLI / Gemini CLI 后端自动预执行工具并注入 prompt。

---

### API 调用示例

服务启动后，可通过 HTTP 接口集成到任意系统：

```bash
# 生成复盘
curl -X POST http://localhost:8000/v1/recap \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-recap-api-key" \
  -d '{"mode": "daily", "provider": "live"}'

# 查看历史
curl http://localhost:8000/v1/history \
  -H "X-API-Key: your-recap-api-key"

# 提交反馈（触发进化）
curl -X POST http://localhost:8000/v1/feedback \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-recap-api-key" \
  -d '{"request_id": "...", "rating": 4, "comment": "分析到位"}'
```

**流式复盘（NDJSON）**：`POST /v1/recap/stream`，`Content-Type: application/x-ndjson`。首行为 `event: meta`，随后 7 行 `event: phase`（`perceive` … `reflect`），末行为 `event: result`（`body` 为与 `/v1/recap` 相同的 JSON 结构；`http_status` 可为 503 表示强制 LLM 但未产出 recap）。任一步骤失败时输出 `event: error` 后结束，且不会触发延后的进化/回测。

```bash
curl -N -X POST http://localhost:8000/v1/recap/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-recap-api-key" \
  -H "X-Session-Id: optional-client-session" \
  -d '{"mode": "daily", "provider": "mock", "force_llm": false}'
```

可选请求头 **`X-Session-Id`**：写入遥测与 `meta`，便于多轮场景关联（当前仍以单次生成为主）。

前端跨域：设置环境变量 **`RECAP_CORS_ORIGINS`**（逗号分隔，如 `http://localhost:5173`）后，服务启动时会自动挂载 CORS 中间件。

API Key 通过 `RECAP_API_KEY` 环境变量设置，不设置则不鉴权（本地开发用）。
