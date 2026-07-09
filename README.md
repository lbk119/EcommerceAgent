# EcommerceAgent

EcommerceAgent 是一个面向电商经营分析和数字员工场景的多 Agent 项目。它不是单纯的聊天机器人，而是一套由 Go Gateway、Python FastAPI Brain、DeepAgents 多 Agent 运行时、业务工具箱、长期记忆、可观测链路和 Vue 前端组成的电商运营智能体系统。

项目当前的核心目标是：

- 让用户可以通过 AI Chat 快速提问，也可以创建后台数字员工任务。
- 让 PlannerAgent 负责理解需求、拆分任务、分派业务 subagent。
- 让业务 subagent 在 DeepAgents 框架内自主选择授权工具完成分析。
- 让系统具备租户隔离、权限控制、长期记忆、trace、质量评估和非功能测试能力。
- 删除旧的固定 DAG 兼容层，收敛到 DeepAgents-centered 架构。

架构图：

![EcommerceAgent Architecture](docs/ecommerce_agent_architecture.png)

## 目录

- [整体架构](#整体架构)
- [核心链路](#核心链路)
- [Agent 模块详解](#agent-模块详解)
- [运行说明](#运行说明)
- [测试与验收](#测试与验收)
- [项目目录说明](#项目目录说明)
- [常见配置](#常见配置)
- [开发约定](#开发约定)

## 整体架构

```mermaid
flowchart LR
  UI["Vue UI<br/>127.0.0.1:5173"] --> GW["Go Gateway<br/>Auth / Tenant / Casbin / Proxy<br/>127.0.0.1:9090"]
  GW --> Brain["Python FastAPI Brain<br/>Business API / Agent Runtime<br/>127.0.0.1:9000"]
  Brain --> Runtime["AgentRuntime"]
  Runtime --> Planner["PlannerAgent<br/>agent/plan"]
  Planner --> Main["DeepAgents Main Agent<br/>agent/subagent"]
  Main --> Subs["Business Subagents"]
  Subs --> Tools["agent/tools"]
  Main --> Store["DeepAgents Store<br/>Postgres / Redis / LangSmith / local fallback"]
  Runtime --> Trace["agent/trace"]
  Runtime --> Eval["agent/evaluation"]
  Brain --> DB["MySQL<br/>users / shops / business data / reports / jobs"]
```

### 分层职责

| 层级 | 目录 | 说明 |
| --- | --- | --- |
| 前端 | `ui/` | Vue 3 + TypeScript + Vite，提供工作台、AI Chat、商品分析、库存风险、活动复盘、报告和数字员工界面。 |
| 网关 | `gateway/` | Go 服务，负责登录注册、JWT、租户/店铺上下文、Casbin 权限、Brain API 代理和 WebSocket 代理。 |
| Brain API | `api/` | FastAPI 服务，承载业务 API、AI Chat、后台任务、报告、导入、任务队列和结果落库。 |
| Agent 核心 | `agent/` | 多 Agent 调度、Planner、DeepAgents subagents、工具、记忆、trace、安全、评估和反思。 |
| 脚本 | `scripts/` | 启动、数据重置、smoke、非功能测试汇总、模块审计。 |
| 测试 | `tests/` | 单元、契约、安全、E2E、评测、性能和混沌测试。 |
| 文档 | `docs/` | 架构图、交付说明、非功能测试方案等。 |

## 核心链路

### 1. AI Chat 实时链路

AI Chat 用于日常问答、轻量解释和意图识别，目标是快速响应，不把每次聊天都变成重型后台分析。

```mermaid
sequenceDiagram
  participant User as User
  participant UI as Vue UI
  participant GW as Go Gateway
  participant API as Python Brain
  participant RT as AgentRuntime
  participant Agent as DeepAgents realtime main agent

  User->>UI: 输入问题
  UI->>GW: POST /api/v1/ai-chat/messages
  GW->>API: 注入 tenant/user/shop 后代理
  API-->>UI: 快速返回 running / acceptedLatencyMs
  API->>RT: 创建 realtime task
  RT->>Agent: 无工具、无 subagent、短预算执行
  Agent-->>API: 返回回答或引导创建后台任务
  API-->>UI: WebSocket 推送最终结果
```

realtime profile 的原则：

- 不加载业务工具。
- 不委托业务 subagent。
- 不执行数据库查询、网络搜索或报告生成。
- 可以做普通聊天、能力边界说明、澄清问题。
- 如果识别为深入经营分析，会说明需要创建 standard/deep 后台任务。

### 2. 数字员工后台链路

商品分析、活动复盘、库存诊断、日报/周报、数据质量检查等任务走 standard 或 deep profile。

```mermaid
flowchart TD
  Request["用户任务 / 报告触发"] --> API["Python Brain 创建 job"]
  API --> Queue["task_queue"]
  Queue --> Runtime["AgentRuntime"]
  Runtime --> Planner["PlannerAgent 生成 AgentTaskPlan"]
  Planner --> Boundary{"需要澄清或超出边界?"}
  Boundary -->|是| Return["返回 clarification / boundary"]
  Boundary -->|否| Main["DeepAgents main agent"]
  Main --> Decide["按依赖选择并行或串行委托"]
  Decide --> Subs["业务 subagents"]
  Subs --> Tools["授权工具调用"]
  Tools --> Reduce["结果汇总"]
  Reduce --> Eval["Critic / evaluation"]
  Eval --> Persist["落库、trace、报告、WebSocket"]
```

standard/deep profile 的原则：

- Planner 只规划和分派，不执行工具。
- DeepAgents main agent 作为真正的调度核心。
- 业务 subagent 自主理解参数、选择工具、分析结果。
- 有数据依赖时，上游 subagent 结果驱动下游分析。
- 无数据依赖时，可以并行执行。
- 工具调用受 profile、权限、schema、RuntimeGuard 约束。

## Agent 模块详解

`agent/` 是本项目最重要的目录。当前文件结构已经围绕 DeepAgents 多 Agent 架构重组：

```text
agent/
  evaluation/   Critic 评估与质量检查
  memory/       DeepAgents store 长期记忆入口
  plan/         PlannerAgent 与 AgentTaskPlan 协议
  platform/     数据库和 LLM 路由等平台适配
  reflection/   任务复盘、策略候选、演进日志
  runtime/      AgentRuntime、profile、预算、任务上下文、结果管线
  security/     prompt guard、权限、脱敏
  subagent/     DeepAgents main agent、subagents、middleware、HITL、MCP、checkpoint
  tools/        业务工具、工具注册表、toolbox
  trace/        trace 事件、timeline、metrics、慢任务诊断
  llm.py        模型实例入口
  main_agent.py 稳定的 Agent 调用入口
```

### agent/plan

`agent/plan` 负责把用户自然语言请求转换成可执行的 Agent 任务计划。

核心文件：

| 文件 | 说明 |
| --- | --- |
| `planner.py` | PlannerAgent 实现，负责意图理解、任务拆解、业务 subagent 分派、依赖说明和边界判断。 |
| `models.py` | AgentTaskPlan、AgentAssignment、依赖关系和序列化协议。 |

PlannerAgent 的职责：

- 理解用户原始输入，包括目标、时间范围、指标、对象、输出格式。
- 判断是 realtime、standard 还是 deep。
- 判断是否缺少关键信息，需要 clarification。
- 判断是否超出电商经营分析边界，需要 boundary。
- 为每个业务 subagent 生成更清晰的 instruction，而不是简单转发原始输入。
- 标注任务依赖：哪些 assignment 可以并行，哪些必须等待上游结果。
- 输出结构化 AgentTaskPlan，供 DeepAgents main agent 执行。

PlannerAgent 不做的事情：

- 不直接写 SQL。
- 不直接调用工具。
- 不直接生成报告文件。
- 不自己替代业务 subagent 做经营结论。

### agent/subagent

`agent/subagent` 是 DeepAgents 框架的集成层，负责把模型、工具、subagents、memory、checkpoint、HITL、MCP、filesystem 和 middleware 组装为 main agent。

核心文件：

| 文件 | 说明 |
| --- | --- |
| `runtime.py` | 构建并缓存 DeepAgents main agent，执行 task plan。 |
| `subagents.py` | 业务 subagent 注册表，声明每个 subagent 的职责、工具白名单、profile、风险等级和输出 schema。 |
| `config.py` | realtime、standard、deep 三类 profile 的 DeepAgents 配置。 |
| `tools.py` | 将项目工具适配成 DeepAgents 可调用工具，并注入租户、店铺、任务上下文。 |
| `guard.py` | AgentSafetyMiddleware，限制循环、重复工具调用、重复 subagent 调用和超预算执行。 |
| `hitl.py` | human-in-the-loop 中断策略。 |
| `mcp.py` | MCP 能力开关和 profile 策略。 |
| `filesystem.py` | 受控文件系统 backend 和权限。 |
| `checkpoint.py` | checkpoint/checkpointer 构建。 |

当前业务 subagents：

| Subagent | 用途 | 典型工具 |
| --- | --- | --- |
| `product_analysis` | 商品表现、爆品、低转化、商品优化、季节选品 | hot products、low conversion、inventory velocity、campaign ROI |
| `inventory` | 库存风险、缺货、滞销、补货承接 | inventory risks、sales trend、inventory velocity |
| `campaign` | 活动复盘、ROI、投放风险、活动承接 | campaign traffic、campaign ROI、campaign risks |
| `report` | 日报、周报、经营总结、多 Agent 结果汇总 | daily metrics、daily risks、markdown/report tools |
| `data_quality` | 数据导入、数据新鲜度、授权、schema 健康 | import jobs、freshness、authorization、schema health |
| `knowledge_base` | 历史报告、历史策略、长期记忆、经验复用 | memory search、report search、strategy search |
| `network_search` | 外部趋势、市场信息、竞品信息 | internet search，仅 deep profile 默认允许 |
| `database_query` | 复杂只读数据库查询、schema lookup | schema lookup、safe read SQL |

### agent/runtime

`agent/runtime` 是 Agent 任务生命周期的中心。

核心文件：

| 文件 | 说明 |
| --- | --- |
| `agent_runtime.py` | 统一运行入口，串联 plan、execute、critic、persist、trace。 |
| `profiles.py` | runtime profile 归一化和默认策略。 |
| `budget.py` | wall time、模型调用、工具调用、subagent 调用等预算。 |
| `task_context.py` | 任务上下文，包含 tenant/user/shop/conversation/task/profile。 |
| `runtime_context.py` | 运行时上下文构建。 |
| `execution_result.py` | Agent 执行结果对象。 |
| `result_pipeline.py` | 结果处理、评估、反思、降级、取消、trace 写入。 |
| `task_profiles.py` | 任务 profile 辅助定义。 |

Runtime 的职责：

- 为每个任务建立身份和上下文。
- 调用 PlannerAgent 生成 AgentTaskPlan。
- 按 profile 调用 DeepAgents runtime。
- 捕获失败、超时、取消和降级。
- 将结果交给 Critic、Reflection、Trace 和 API 持久化层。

### agent/tools

`agent/tools` 是所有可被 subagent 使用的业务工具集合。旧的根目录 `tools/` 已合并到这里。

核心文件：

| 文件 | 说明 |
| --- | --- |
| `registry.py` | ToolRegistry，集中注册工具、权限元数据和 lazy factory。 |
| `tool_schemas.py` | 工具参数 schema。 |
| `business_metrics.py` | 电商业务指标查询和计算。 |
| `database_workflow_tool.py` | 数据库工作流工具入口。 |
| `db_tools.py` | 受控数据库查询工具。 |
| `ragflow_tools.py` | RAGFlow 知识库工具。 |
| `tavily_tool.py` | Tavily/网络搜索工具。 |
| `markdown_tools.py` | Markdown 报告生成工具。 |
| `pdf_tools.py` | PDF 工具。 |
| `upload_file_read_tool.py` | 上传文件读取工具。 |
| `toolbox/` | 面向 subagent 的业务工具箱封装。 |

工具设计原则：

- 每个工具都必须有明确权限。
- 数据库工具必须租户/店铺隔离。
- 高风险能力必须经过 profile 和 permission 控制。
- subagent 只能调用自己白名单内的工具。
- 工具失败必须返回可诊断信息，而不是让 Agent 编造结果。

### agent/memory

`agent/memory` 是长期记忆的唯一入口。项目不再维护一套外层 MySQL 记忆系统。

当前设计：

- DeepAgents store 是唯一长期记忆主存。
- 支持 `langsmith`、`postgres`、`redis`、`filesystem`、`memory` 等 backend 策略。
- 生产环境应使用 `postgres`、`redis` 或 LangSmith managed store。
- `InMemoryStore` 只允许测试或本地降级。
- namespace 按 tenant/shop/user 隔离，避免跨租户记忆泄漏。

核心对象：

| 对象 | 说明 |
| --- | --- |
| `MemoryBackendFactory` | 根据环境变量创建 DeepAgents store。 |
| `MemoryBackend` | 描述 store backend、是否可持久化、告警信息。 |
| `MemoryIdentity` | tenant/user/shop/conversation/task 身份对象。 |
| `memory_namespace()` | 生成长期记忆命名空间。 |

### agent/trace

`agent/trace` 负责可观测性。

| 文件 | 说明 |
| --- | --- |
| `tracer.py` | 统一 trace 事件写入。 |
| `events.py` | 事件结构定义。 |
| `reader.py` | timeline、metrics、slow-tasks 查询。 |
| `slow_task_analyzer.py` | 慢任务诊断。 |

trace 用于：

- 查看每个 Agent 任务的 planner、subagent、tool、critic、result 阶段。
- 汇总平均耗时、失败原因、慢任务。
- 支撑非功能测试报告。
- 排查 E2E 和后台任务资源竞争问题。

### agent/evaluation

`agent/evaluation` 负责质量评估。

| 文件 | 说明 |
| --- | --- |
| `critic_agent.py` | Critic 评估逻辑。 |
| `critic_policy.py` | Critic 策略和阈值。 |

Critic 关注：

- 输出是否满足用户目标。
- 是否说明证据、风险和缺失数据。
- 是否出现幻觉或越权结论。
- 是否需要 revision 或降级。

### agent/reflection

`agent/reflection` 负责任务复盘和策略演进。

| 文件 | 说明 |
| --- | --- |
| `reflection.py` | 根据任务结果生成反思。 |
| `policy_review.py` | 策略候选、审批、拒绝和查询。 |
| `evolution_log.py` | 演进日志。 |

reflection 不直接改变高风险策略。需要审批的策略候选会进入 review 流程。

### agent/security

`agent/security` 负责 Agent 执行安全。

| 文件 | 说明 |
| --- | --- |
| `prompt_guard.py` | prompt injection 和危险输入检测。 |
| `permissions.py` | 工具权限、用户权限和 profile 权限判断。 |
| `redaction.py` | 敏感信息脱敏。 |

安全边界：

- Gateway 做用户、租户、角色和 Casbin 权限。
- Agent 工具层做工具权限和数据范围控制。
- Prompt guard 防止越权指令、系统提示泄露和工具滥用。
- Trace 和报告输出前做必要脱敏。

### agent/platform

`agent/platform` 放平台适配代码。

| 文件 | 说明 |
| --- | --- |
| `db.py` | Python 侧数据库连接和辅助能力。 |
| `llm_router.py` | fast/standard/deep 模型路由。 |

## Runtime Profile

| Profile | 场景 | 默认能力 |
| --- | --- | --- |
| `realtime` | AI Chat 快速问答 | 无工具、无 subagent、短预算、快速返回。 |
| `standard` | 常规数字员工和报告 | 允许业务 subagents、业务工具、数据库只读查询，默认不允许网络搜索。 |
| `deep` | 深度分析、跨来源研究、复杂报告 | 更高预算，允许更多 subagent 调用，可开启网络搜索、HITL、记忆写入和反思。 |

推荐用法：

- 聊天框普通问答：`realtime`
- 商品/库存/活动/报告后台任务：`standard`
- 需要外部趋势、复杂多轮分析、跨模块依赖：`deep`

## Docker 沙箱机制

EcommerceAgent 支持容器级 Agent Sandbox，用于隔离文件解析、用户上传文件处理、潜在代码执行和后续第三方脚本类工具。链路是：

```text
Agent Runtime / subagent tool
  -> agent.sandbox.SandboxClient
  -> POST /api/v1/sandbox/execute
  -> api.sandbox Sandbox Server
  -> 临时 Docker container
  -> 收集 /workspace/output
  -> 销毁容器和 workspace
```

Agent、subagent 和 tool 侧不直接 import Docker SDK，也不调用 Docker CLI；它们只能提交 `SandboxTask`。Docker 容器创建、资源限制、网络策略、挂载策略和审计都由 `api/sandbox` 内部服务端模块完成。

默认限制：

| Profile | Sandbox 能力 |
| --- | --- |
| `realtime` | 禁止所有容器执行，即使工具误传入也拒绝。 |
| `standard` | 允许受控 Python/file 任务；默认禁止 shell、网络、数据库凭证进入容器。 |
| `deep` | 允许 Python/Node/file；shell 仍需 `SANDBOX_ENABLE_SHELL=true`；网络默认关闭，只能通过 allowlist 策略启用。 |

容器安全参数包括：默认无网络、非 root 用户 `1000:1000`、`--read-only` rootfs、`/tmp` tmpfs、只挂载单任务 workspace 到 `/workspace`、`--cap-drop ALL`、`no-new-privileges`、内存/CPU/pids/timeout 限制。禁止 Docker socket、host network、host pid、privileged container 和宿主敏感路径挂载。

构建沙箱镜像：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_sandbox_images.ps1
```

当前已实际接入的 sandbox 工具：`read_file_content`。该工具会把用户会话目录中的目标文件复制成 `SandboxFile`，由 Python 沙箱容器解析文本/CSV/Excel，不再在 Agent 进程内直接解析上传文件。数据库只读业务工具仍 native 执行，但保留权限、SQL guard 和 ToolRegistry metadata。长期记忆仍只走 DeepAgents Store，不进入容器。

Trace 中可查看 `sandbox_task_requested`、`sandbox_task_denied`、`sandbox_container_created`、`sandbox_task_finished`、`sandbox_task_failed`、`sandbox_container_removed`。Docker 不可用或镜像未构建时，Docker runner/e2e 相关测试会 skip；非 Docker 的模型、策略和 workspace 测试仍会执行。

常用沙箱配置：

```env
ENABLE_DOCKER_SANDBOX=true
SANDBOX_SERVER_INTERNAL_TOKEN=dev-sandbox-token-change-me
SANDBOX_ROOT=output/sandbox
SANDBOX_KEEP_WORKSPACE_ON_FAILURE=false
SANDBOX_DEFAULT_TIMEOUT_SECONDS=30
SANDBOX_MAX_TIMEOUT_SECONDS=120
SANDBOX_DEFAULT_MEMORY_MB=512
SANDBOX_DEEP_MEMORY_MB=1024
SANDBOX_DEFAULT_CPU_COUNT=1
SANDBOX_DEEP_CPU_COUNT=2
SANDBOX_PIDS_LIMIT=128
SANDBOX_MAX_INPUT_BYTES=10485760
SANDBOX_MAX_OUTPUT_BYTES=10485760
SANDBOX_ENABLE_NETWORK=false
SANDBOX_DEEP_ENABLE_NETWORK=false
SANDBOX_ALLOWED_DOMAINS=
SANDBOX_ENABLE_SHELL=false
SANDBOX_PYTHON_IMAGE=ecommerce-agent-sandbox-python:latest
SANDBOX_NODE_IMAGE=ecommerce-agent-sandbox-node:latest
```

`/api/v1/sandbox/*` 是 Brain 内部接口，不应作为普通前端功能暴露；调用方必须携带 `X-Sandbox-Internal-Token`。生产环境必须替换默认 token，并在网关或部署层限制该接口只允许内部服务访问。

详细协议和扩展方式见 [docs/docker_sandbox.md](docs/docker_sandbox.md)。

## 运行说明

### 环境要求

- Windows PowerShell
- Python 3.12+
- Go 1.22+
- Node.js 20+
- MySQL 8+
- 可选：Redis、NATS、Postgres DeepAgents Store、RAGFlow、Tavily

### 1. 准备配置

复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

至少修改：

```env
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your-api-key
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your-password
MYSQL_DATABASE=ecommerce_demo
GATEWAY_JWT_SECRET=dev-only-change-me
```

如果要启用生产级长期记忆，建议配置：

```env
ENABLE_DEEPAGENTS_MEMORY=true
DEEPAGENTS_STORE_BACKEND=postgres
DEEPAGENTS_POSTGRES_URL=postgresql://user:password@localhost:5432/ecommerce_agent_memory
```

本地调试也可以先使用：

```env
DEEPAGENTS_STORE_BACKEND=memory
```

### 2. 一键启动开发环境

首次安装依赖并启动：

```powershell
.\scripts\start-dev.ps1 -Install -SeedDemo
```

后续启动：

```powershell
.\scripts\start-dev.ps1
```

默认端口：

| 服务 | 地址 |
| --- | --- |
| Vue UI | `http://127.0.0.1:5173` |
| Go Gateway | `http://127.0.0.1:9090` |
| Python Brain | `http://127.0.0.1:9000` |

启动日志位于：

```text
.run-logs/
```

### 3. 单独启动服务

Python Brain：

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.server:app --host 127.0.0.1 --port 9000
```

Go Gateway：

```powershell
go run ./gateway/cmd/server
```

前端：

```powershell
cd ui
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

### 4. 重置演示数据

```powershell
.\.venv\Scripts\python.exe scripts\reset_dev_data.py
```

或使用启动脚本：

```powershell
.\scripts\start-dev.ps1 -SeedDemo
```

## 测试与验收

### Python 单元测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit -q
```

### 契约、安全、评测测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contract tests/security tests/evals -q
```

### E2E 测试

需要先启动 Python Brain 和 Go Gateway：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/e2e -m e2e -q
```

### Go Gateway 测试

```powershell
go test ./gateway/internal/...
```

### 非功能测试汇总

开发模式：

```powershell
.\.venv\Scripts\python.exe scripts\run_nonfunctional_suite.py --all --mode dev
```

发布模式：

```powershell
.\.venv\Scripts\python.exe scripts\run_nonfunctional_suite.py --all --mode release
```

输出目录：

```text
output/nonfunctional/
```

重点报告：

| 文件 | 说明 |
| --- | --- |
| `summary.md` | 人类可读汇总。 |
| `summary.json` | CI gate 可读结果。 |

发布模式下，E2E、安全、隔离、DAG/TaskPlan、性能或混沌门禁失败会阻断 release。

## 项目目录说明

```text
EcommerceAgent/
  .github/workflows/        CI 工作流
  agent/                    多 Agent 核心
  api/                      Python FastAPI Brain
  data/                     数据脚本、schema、演示数据
  docs/                     项目文档和架构图
  gateway/                  Go Gateway
  scripts/                  启动、测试、审计和报告脚本
  tests/                    自动化测试
  ui/                       Vue 前端
  utils/                    通用辅助工具
  requirements.txt          Python 依赖
  go.mod / go.sum           Go 依赖
  pytest.ini                pytest 配置
  README.md                 项目说明
```

### api/

`api/` 是 Python Brain：

| 目录/文件 | 说明 |
| --- | --- |
| `server.py` | FastAPI app、生命周期、Agent task 启动入口。 |
| `routes/` | 商品、库存、活动、报告、AI Chat、数据导入等 API router。 |
| `services/` | AI Chat、Agent job、报告结果、业务聚合等服务。 |
| `task_queue.py` | inline/Redis/NATS 队列适配。 |
| `task_runtime.py` | conversation/task 归属校验和运行时状态。 |
| `monitor.py` | WebSocket 推送。 |
| `db.py` | Brain 侧数据库 schema 和连接。 |

### gateway/

`gateway/` 是 Go 网关：

| 目录 | 说明 |
| --- | --- |
| `cmd/server` | 网关启动入口。 |
| `configs/casbin` | Casbin model 和 policy。 |
| `internal/auth` | 用户、JWT、登录注册。 |
| `internal/middleware` | 鉴权、租户上下文、CORS、日志。 |
| `internal/proxy` | Brain API 代理。 |
| `internal/router` | Gateway 路由。 |

### ui/

`ui/` 是前端应用：

- Vue 3
- TypeScript
- Vite
- Axios
- Marked

它通过 Gateway 访问 API，不直接访问 Python Brain。

## 常见配置

### LLM

```env
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your-api-key
LLM_FAST_MODEL=gpt-5.4-mini
LLM_DEEP_MODEL=gpt-5.5
LLM_FAST_TIMEOUT_SECONDS=8
LLM_DEEP_TIMEOUT_SECONDS=60
```

### Profile 预算

```env
REALTIME_AGENT_MAX_WALL_TIME_SECONDS=15
STANDARD_AGENT_MAX_WALL_TIME_SECONDS=45
DEEP_AGENT_MAX_WALL_TIME_SECONDS=180

REALTIME_AGENT_MAX_MODEL_CALLS=1
STANDARD_AGENT_MAX_MODEL_CALLS=2
DEEP_AGENT_MAX_MODEL_CALLS=6
```

### DeepAgents 能力开关

```env
ENABLE_DEEPAGENTS_RUNTIME=true
ENABLE_DEEPAGENTS_REALTIME_CHAT=true
ENABLE_DEEPAGENTS_STANDARD=true
ENABLE_DEEPAGENTS_DEEP=true
ENABLE_AGENT_SAFETY_MIDDLEWARE=true
ENABLE_DEEPAGENTS_HITL=true
ENABLE_DEEPAGENTS_MCP=false
ENABLE_DEEPAGENTS_FILESYSTEM=true
ENABLE_DEEPAGENTS_SHELL=false
ENABLE_DEEPAGENTS_MEMORY=true
```

### 任务队列

```env
TASK_QUEUE_BACKEND=inline
REDIS_URL=redis://localhost:6379/0
NATS_URL=nats://localhost:4222
```

本地开发默认 `inline`。如果要模拟更真实的后台任务并发，可以切换到 Redis 或 NATS。

### 权限和租户

```env
GATEWAY_AUTH_ENABLED=true
GATEWAY_USER_STORE_BACKEND=mysql
GATEWAY_CASBIN_MODEL=gateway/configs/casbin/model.conf
GATEWAY_CASBIN_POLICY=gateway/configs/casbin/policy.csv
GATEWAY_DEMO_TENANT_ID=tenant_demo
GATEWAY_DEMO_SHOP_ID=default_shop
```

所有 Agent 工具调用都必须带 tenant/user/shop 上下文。

## 开发约定

### 新增业务 Agent

1. 在 `agent/subagent/subagents.py` 新增 `DeepAgentsSubagentSpec`。
2. 声明 `name`、`description`、`prompt`、`allowed_tools`、`supported_profiles`、`risk_level`。
3. 如需新工具，在 `agent/tools/` 中实现，并在 `agent/tools/registry.py` 注册。
4. 在 `agent/plan/planner.py` 的能力描述中加入可分派能力。
5. 增加单元测试、契约测试和必要的 E2E。

### 新增工具

1. 工具实现放到 `agent/tools/` 或 `agent/tools/toolbox/`。
2. 参数必须有 schema。
3. 必须经过 ToolRegistry 注册。
4. 必须声明权限和风险等级。
5. 数据库工具必须确保 tenant/shop scope。
6. 网络、文件、shell、MCP 等高风险能力必须受 profile 控制。

### 新增 Planner 能力

1. 修改 `agent/plan/planner.py`。
2. 更新 AgentTaskPlan 相关测试。
3. 覆盖 clarification、boundary、profile、dependencies。
4. 不要把固定 DAG 或固定参数解析重新加回来。

### 记忆规则

- 长期记忆只通过 DeepAgents store。
- 不再新增 MySQL memory writer/retriever/extractor。
- 生产环境禁止使用 InMemoryStore。
- 记忆 namespace 必须包含 tenant/shop/user 或等价隔离字段。

### 安全规则

- Planner 不执行工具。
- Main agent 不绕过 ToolRegistry。
- Subagent 不调用白名单外工具。
- 数据库工具只读并限制租户/店铺。
- 网络搜索仅在 deep profile 中按配置开启。
- Shell 默认关闭。
- HITL 用 DeepAgents human-in-the-loop，不单独做 human_approval subagent。

## 当前架构取舍

本项目刻意删除了旧的固定 DAG 工作流兼容层。新的设计是：

- PlannerAgent 作为计划和分派层。
- DeepAgents main agent 作为调度执行层。
- 业务 subagent 作为专业能力层。
- step 不再是固定 DAG 节点，而是 subagent 可选的业务工具。
- 参数理解由 LLM 和工具 schema 共同完成，不再依赖固定关键字规则。
- 长期记忆统一走 DeepAgents store，不再维护外层 MySQL 记忆系统。

这样做的好处是架构更清晰，业务 Agent 更灵活，也更容易接入 DeepAgents 的上下文管理、store、checkpoint、HITL、MCP 和 middleware 能力。
