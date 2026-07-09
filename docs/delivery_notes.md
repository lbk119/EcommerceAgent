# 交付说明

## 已闭环能力

- 账号注册、登录、token 会话、本地登出和 401 过期清理。
- 用户、企业、租户、店铺和用户-店铺关系默认写入 MySQL Gateway 表，Gateway 启动时自动建表。
- 新用户 onboarding：创建店铺、选择平台、选择数据模式、启用数字员工。
- 多店铺工作台：Gateway 注入租户和店铺上下文，Brain 侧查询按 `tenant_id` 和 `shop_id` 聚合。
- 示例数据导入：写入订单、商品、库存、流量、活动、退款等测试数据。
- CSV/Excel 上传导入：上传、预览、确认入库、刷新工作台。
- 数据导入后经营闭环：生成经营概览报告和待审核策略。
- 数字员工任务：创建 `agent_jobs`，通过 `task_queue -> start_agent_task -> run_agent_task -> AgentRuntime` 执行，成功/失败回写业务 job。
- 报告中心：生成草稿报告、Agent 完成后更新报告内容、报告详情查询；Plan-first 任务会同时保存 `structuredResult`，前端优先用结论、依据、动作、风险、缺失数据和执行明细卡片展示，Markdown 仅作为原文查看/复制/导出格式。
- 商品分析、库存补货建议、活动复盘：前端按钮接 Gateway，Gateway 代理 Brain，Brain 创建受控 Agent job。
- AI 对话：前端请求 `POST /api/v1/ai-chat/messages`，Gateway 代理到 Python Brain 的 `/api/ai-chat/messages`；Brain 只做鉴权上下文、任务分类、MySQL run/message 创建和入队，目标是在 1 秒内返回 `status=running` 的任务受理结果。
- AI 对话执行：后台 `task_queue -> start_agent_task -> AgentRuntime -> deepagents-native realtime profile` 继续运行，AI Chat 不再使用独立聊天运行时；完成/失败/超时后回写 MySQL `ai_chat_messages` 与 `ai_chat_runs`，前端不再同步等待 60 秒，也不会停在“正在调用 Python Brain AgentRuntime 分析...”。
- AI 对话观测：前端使用同一个 `conversationId/wsThreadId` 连接 Gateway `/api/v1/ws/{thread_id}`，实时展示后端 trace 事件；断线或刷新后通过 `/api/v1/ai-chat/tasks/{task_id}/timeline` 和 `/api/v1/ai-chat/messages/{message_id}` 补拉状态。
- AI 对话体验：前端拆成左侧对话和右侧 Agent 状态面板，助手消息展示答案、可折叠分析过程、状态、耗时、来源；WebSocket 事件按“接收问题、识别意图、读取店铺数据、命中工作流、生成建议、质量检查、写入结果、完成”等阶段聚合，默认只展示当前阶段和最近关键事件。
- AI 对话并发：同一 `conversationId` 内允许多个后台任务并行运行，生命周期隔离改由唯一 `task_id` 承担；用户在长任务运行时可以继续提交下一问，不再因为同会话已有任务而返回 409。
- AI 对话性能：高频经营问题在 AI Chat 中快速分流，复杂分析转入后台 standard/deep job；standard/deep 由 deepagents-native subagents 理解参数、选择授权工具并基于真实工具结果分析；模型或工具异常时返回 degraded/clarification；`AI_CHAT_MODEL_PROFILE=fast`、`AI_CHAT_LLM_TIMEOUT_SECONDS=8`、`AI_CHAT_TOTAL_TARGET_SECONDS=15` 用于约束 AI Chat 的成本和等待时间。
- AI 对话规划：HTTP 受理阶段用 PlannerAgent 同步 fallback 生成轻量 `task_plan`，入队 payload 保留 `raw_user_question`、`task_plan` 和 `agent_query`；包装 prompt 不再参与规划，避免“天气”被包装词里的库存、商品、活动误判成业务 workflow。
- AI 对话持久化：新增 MySQL `ai_chat_conversations`、`ai_chat_messages`、`ai_chat_runs`；完成消息保存 `structured_json` 并返回 `structuredResult`，刷新后可从后端恢复历史消息、任务状态和已完成结构化结果。
- AgentRuntime profiles：新增 `realtime`、`standard`、`deep` 三档运行时。`realtime` 由 deepagents-native main agent 承接，但不挂工具和 subagents；`standard` 和 `deep` 统一走 deepagents-native main agent + business subagents；`deep` 允许更高预算、网络搜索、Critic、Memory、Evolution。
- AgentRuntime budget：新增模型/工具/subagent/wall time 硬预算，默认 realtime 为 15s/1 model/3 tools/0 subagents，standard 为 45s/2 models/6 tools/1 subagent，deep 为 180s/6 models/12 tools/3 subagents；超预算会写入 `budget_exceeded` trace 并返回阶段性结果。
- deepagents-native 业务执行：PlannerAgent 输出 `AgentTaskPlan` 和 `AgentAssignment`，只负责分派 Product/Inventory/Campaign/Report/DataQuality/KnowledgeBase/NetworkSearch/DatabaseQuery 等业务 subagent；deepagents main agent 根据依赖关系委托 subagents，并通过 `agent/tools` 注册工具和权限边界执行。
- Agent 模块瘦身：旧固定执行层已从主链路移除并删除；Runtime、AI Chat、Agent Job 和 API 队列全部使用 `AgentTaskPlan`。
- Optional extensions：Milvus/BGE 语义记忆和 checkpointer 保留在 `agent/subagent/checkpoint.py`；知识库和网络搜索 subagent 已进入 `agent/subagent/subagents.py`，由 profile 和 `DEEPAGENTS_DEEP_ENABLE_NETWORK_SEARCH` 控制。
- Runtime guard：deepagents-native 使用 RuntimeGuard/profile budget 控制模型、工具、subagent 调用次数和 wall time，替代旧固定 step executor/loop_guard。
- Task queue profile 并发：后台队列在总并发 `MAX_AGENT_CONCURRENCY=10` 外，再按 `REALTIME_AGENT_CONCURRENCY`、`STANDARD_AGENT_CONCURRENCY`、`DEEP_AGENT_CONCURRENCY` 做 profile semaphore，默认 realtime=8、standard=2、deep=1，避免深度任务无预算挤占实时体验。
- AgentRuntime health/metrics：新增 `/api/v1/agent-runtime/health`、`/api/v1/agent-runtime/metrics` 与 `/api/v1/agent-runtime/slow-tasks`，health 区分 `ok`、`disabled`、`not_started`、`jsonl_not_mysql`，不把未接入模块伪装成健康；慢任务接口用于排查 trace 中的高耗时 LLM、subagent 或 deepagents-native 阶段。
- AgentRuntime diagnosis：新增 `/api/v1/agent-runtime/tasks/{task_id}/diagnosis`，按单任务返回总耗时、模型调用、工具调用、subagent 调用、Critic/Memory 事件、最慢阶段和优化建议。
- Agent 模块审计：新增 `scripts/audit_agent_modules.ps1`，输出每个 `agent`/`agent/subagent/checkpoint.py` Python 文件的模块分类、引用数量、是否热路径和治理建议。
- 任务治理：AI Chat 提供 `POST /api/v1/ai-chat/tasks/{task_id}/cancel`，可取消 queued/running 任务并回写 MySQL 状态；已完成、失败或超时的任务保持终态不被覆盖。
- 策略审核：approve/reject/defer 均按租户和店铺更新，不存在的 strategy 返回 404。
- Gateway API 治理：业务路由经过 Auth -> Tenant -> Casbin；新注册 admin 注入当前业务路由权限。
- smoke E2E：注册临时账号、完成 onboarding、导入示例数据、查询 workspace、查询报告详情、创建商品分析 job、查询 job detail。

## 仍是确定性规则的能力

- JSON / memory 用户存储仅用于本地开发或测试显式启用；商业化默认用户存储是 MySQL，不再使用 `data/gateway_users.json`。

- 工作台指标、商品分层、库存风险和活动评分的数据来源主要由 SQL 聚合和确定性工具生成；deepagents-native subagent 的工具选择、参数理解和结论分析由 LLM 主导，并受工具 schema 与权限约束。
- 导入后经营概览和策略候选由确定性模板生成，适合验收闭环，不代表最终智能策略质量。
- SQL 聚合保留为 Agent workflow 节点和数据库工具的数据来源，不再由 AI Chat API route 直接拼接固定答案。
- 导入后经营概览仍是确定性摘要，用于数据接入后的快速反馈，不代表 Agent 深度分析。
- 前端 AI 对话已移除本地伪回答 fallback；后端 AI Chat route 不再拼 SQL 固定回答。模型或 API key 未配置时，AI 对话会保留任务状态并显示真实失败/超时。
- AI Chat 默认使用 `runtime_profile=realtime`：由 deepagents-native realtime main agent 承接，无工具、无 subagents、短预算；standard/realtime 默认跳过 Critic、长期记忆和结果 enrichment，deep profile 才启用完整 Runtime。

## 依赖真实模型或平台授权的能力

- 数字员工最终报告质量依赖 `agent.main_agent.run_agent_task` 的模型调用和工具执行结果。
- 商品优化、补货计划、活动复盘的深度分析依赖模型可用性、工具链稳定性和数据质量。
- 真实平台授权、订单同步、库存同步、活动同步仍需要接入平台开放 API。
- 多平台增量同步、授权过期刷新、失败重试和同步日志仍需要真实平台环境验证。

## 运行方式

### Gateway 用户存储

默认配置：

`scripts/start-dev.ps1` 启动前会自动读取项目根目录 `.env`，并把同一份 `MYSQL_*` 配置传给 Python Brain 和 Go Gateway；当前 PowerShell 已存在的环境变量优先，不会被 `.env` 覆盖。

```powershell
$env:GATEWAY_USER_STORE_BACKEND = "mysql"
$env:MYSQL_HOST = "localhost"
$env:MYSQL_PORT = "3306"
$env:MYSQL_USER = "root"
$env:MYSQL_PASSWORD = "your-password"
$env:MYSQL_DATABASE = "ecommerce_demo"
```

Gateway 启动会自动创建并使用：`gateway_tenants`、`gateway_users`、`gateway_user_tenants`、`gateway_shops`、`gateway_user_shops`。

检查用户数据：

```sql
SHOW TABLES LIKE 'gateway_%';
SELECT id, email, phone, default_tenant_id, default_shop_id, onboarding_completed FROM gateway_users;
```

仅开发/测试可用：`GATEWAY_USER_STORE_BACKEND=memory` 且 `GIN_MODE=debug`。

### Python Brain

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.server:app --host 127.0.0.1 --port 9000
```

### Go Gateway

```powershell
$env:PYTHON_BRAIN_URL = "http://127.0.0.1:9000"
$env:GATEWAY_ADDR = ":9090"
go run ./gateway/cmd/server
```

### Frontend

```powershell
Push-Location ui
npm run dev -- --host 127.0.0.1 --port 5173
Pop-Location
```

### Smoke

默认验证 MySQL 用户体系、onboarding、示例数据导入、workspace、异步 AI Chat 受理、timeline、message 查询、AgentRuntime health，以及 Agent job 创建和详情可查：

```powershell
.\scripts\smoke_e2e.ps1
```

性能链路验收：验证 realtime AI Chat 1 秒内受理、5 秒内有进度、diagnosis 可解释慢因、standard 数字员工 5 秒内返回 jobId：

```powershell
.\scripts\smoke_agent_performance.ps1
```

覆盖 Gateway 地址：

```powershell
$env:GATEWAY_URL = "http://127.0.0.1:9090"
.\scripts\smoke_e2e.ps1
```

等待商品分析 Agent job 最多 180 秒：

```powershell
.\scripts\smoke_e2e.ps1 -WaitAgentJob
```

要求 Agent job 必须在 180 秒内完成，否则失败：

```powershell
.\scripts\smoke_e2e.ps1 -WaitAgentJob -StrictAgentComplete
```

## 交付前验证命令

```powershell
go test ./gateway/...
.\.venv\Scripts\python.exe -B -m compileall api agent
Push-Location ui
npm run build
Pop-Location
.\scripts\smoke_e2e.ps1
.\scripts\smoke_e2e.ps1 -WaitAgentJob
```

## AI Chat API 契约

发送消息：

```http
POST /api/v1/ai-chat/messages
```

返回任务受理，不返回最终答案：

```json
{
	"messageId": "...",
	"conversationId": "...",
	"taskId": "...",
	"status": "running",
	"source": "agent",
	"wsThreadId": "...",
	"intent": "seasonal_selection",
	"acceptedLatencyMs": 123,
	"message": { "role": "assistant", "status": "running", "content": "Agent 已接收任务..." }
}
```

进度事件来自 trace/WebSocket，常见阶段包括：`queued`、`prompt_guard_started/finished`、`task_classified`、`context_prepared`、`memory_retrieval_*`、`workflow_*`、`tool_call_*`、`llm_call_*`、`critic_*`、`persistence_*`、`memory_write_*`、`agent_finished/failed`。

补偿查询：

```http
GET /api/v1/ai-chat/tasks/{task_id}/timeline
GET /api/v1/ai-chat/messages/{message_id}
GET /api/v1/ai-chat/conversations/{conversation_id}/messages
GET /api/v1/agent-runtime/health
GET /api/v1/agent-runtime/metrics
```

## 常见问题

### MySQL 慢

现象：onboarding 或 sample import 耗时几十秒。

处理：先确认 MySQL 本地服务状态、连接数、磁盘 IO 和表结构是否已迁移。smoke 单请求超时为 120 秒，适配本地慢写入；如果超过 120 秒，需要看 Brain 和 MySQL 日志。

### Job 一直 running

现象：商品分析 job 创建成功，但 `status` 长时间为 `running`。

处理：默认 smoke 不把 running 当失败，只验证创建和详情查询。使用 `-WaitAgentJob` 可等待 180 秒；超时会输出“任务仍在后台执行”并以 0 退出。若传 `-StrictAgentComplete`，超时会以失败退出。

### 401

现象：前端请求返回 401。

处理：`platformApi.ts` 会清理本地 session 并广播登录过期事件，App 会跳转登录页。若没有跳转，检查当前页面是否已经挂载 App，以及浏览器 localStorage 中是否残留旧 token。

### GATEWAY_URL 残留

现象：smoke 打到旧端口，例如 `19091`，提示无法连接。

处理：PowerShell 进程内环境变量会残留。显式设置：

```powershell
$env:GATEWAY_URL = "http://127.0.0.1:9090"
.\scripts\smoke_e2e.ps1
```

或清理变量：

```powershell
Remove-Item Env:GATEWAY_URL -ErrorAction SilentlyContinue
```
