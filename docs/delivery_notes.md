# 交付说明

## 已闭环能力

- 账号注册、登录、token 会话、本地登出和 401 过期清理。
- 新用户 onboarding：创建店铺、选择平台、选择数据模式、启用数字员工。
- 多店铺工作台：Gateway 注入租户和店铺上下文，Brain 侧查询按 `tenant_id` 和 `shop_id` 聚合。
- 示例数据导入：写入订单、商品、库存、流量、活动、退款等测试数据。
- CSV/Excel 上传导入：上传、预览、确认入库、刷新工作台。
- 数据导入后经营闭环：生成经营概览报告和待审核策略。
- 数字员工任务：创建 `agent_jobs`，通过 task runtime 执行，成功/失败回写业务 job。
- 报告中心：生成草稿报告、Agent 完成后更新报告内容、报告详情查询。
- 商品分析、库存补货建议、活动复盘：前端按钮接 Gateway，Gateway 代理 Brain，Brain 创建受控 Agent job。
- 策略审核：approve/reject/defer 均按租户和店铺更新，不存在的 strategy 返回 404。
- Gateway API 治理：业务路由经过 Auth -> Tenant -> Casbin；新注册 admin 注入当前业务路由权限。
- smoke E2E：注册临时账号、完成 onboarding、导入示例数据、查询 workspace、查询报告详情、创建商品分析 job、查询 job detail。

## 仍是确定性规则的能力

- 工作台指标、商品分层、库存风险、活动评分和活动结论主要由 SQL 聚合和确定性规则生成。
- 导入后经营概览和策略候选由确定性模板生成，适合验收闭环，不代表最终智能策略质量。
- AI 对话在后端不可用时有前端兜底回复，用于不中断体验。
- 经营建议文本仍以规则模板为主，真实效果需要结合线上数据和模型输出持续校准。

## 依赖真实模型或平台授权的能力

- 数字员工最终报告质量依赖 `agent.main_agent.run_deep_agent` 的模型调用和工具执行结果。
- 商品优化、补货计划、活动复盘的深度分析依赖模型可用性、工具链稳定性和数据质量。
- 真实平台授权、订单同步、库存同步、活动同步仍需要接入平台开放 API。
- 多平台增量同步、授权过期刷新、失败重试和同步日志仍需要真实平台环境验证。

## 运行方式

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

默认只验证 Agent job 创建和详情可查：

```powershell
.\scripts\smoke_e2e.ps1
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
