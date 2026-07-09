# Docker Sandbox

EcommerceAgent 的 Docker Sandbox 是 Brain 内部能力，不面向普通前端用户暴露。Agent 侧只能提交结构化 `SandboxTask`，不能直接 import Docker SDK 或调用 Docker CLI。

## 架构

```mermaid
flowchart LR
  Runtime[AgentRuntime] --> Tool[DeepAgents Tool Wrapper]
  Tool --> Client[agent.sandbox.SandboxClient]
  Client --> API[POST /api/v1/sandbox/execute]
  API --> Policy[SandboxPolicyEngine]
  Policy --> Workspace[Ephemeral Workspace]
  Workspace --> Docker[Temporary Docker Container]
  Docker --> Output[/workspace/output]
  Output --> Result[SandboxResult]
  Docker --> Cleanup[Remove Container + Workspace]
```

## API

Internal endpoint:

```http
POST /api/v1/sandbox/execute
X-Sandbox-Internal-Token: <SANDBOX_SERVER_INTERNAL_TOKEN>
```

Health endpoint:

```http
GET /api/v1/sandbox/health
X-Sandbox-Internal-Token: <SANDBOX_SERVER_INTERNAL_TOKEN>
```

Requests without the internal token return `403`. The route is mounted in Brain for internal calls and should not be proxied as a public user feature.

## SandboxTask

Important fields:

- `task_id`, `conversation_id`, `tenant_id`, `user_id`, `shop_id`: audit and tenant isolation context.
- `profile`: `realtime`, `standard`, or `deep`.
- `agent_id`, `tool_name`: caller and tool metadata.
- `runtime`: `python`, `node`, `shell`, or `file`.
- `command` or `code`: command vector or inline code written to a task entrypoint.
- `input_files`: base64 encoded relative-path files copied into `/workspace`.
- `network_policy`: `none` or `allowlist`.
- `resource_limits`: CPU, memory, pids, disk, timeout.

`SandboxResult` returns `ok`, `exit_code`, bounded `stdout`/`stderr`, collected `output_files`, `duration_ms`, `sandbox_id`, `denied_reason`, `trace_id`, and resource metadata.

## Container Security

The Docker runner creates one short-lived container per task:

- Image is selected by runtime:
  - Python: `ecommerce-agent-sandbox-python:latest`
  - Node: `ecommerce-agent-sandbox-node:latest`
  - Shell/base: `ecommerce-agent-sandbox-base:latest`
- Network is `none` in phase 1.
- Root filesystem is read-only where Docker supports it.
- `/tmp` is tmpfs.
- Only the task workspace is mounted to `/workspace`.
- User is `1000:1000`.
- `--cap-drop ALL` and `--security-opt no-new-privileges` are always used.
- Memory, CPU, pids and timeout limits are enforced.
- Containers are labeled with `app=ecommerce-agent`, `component=sandbox`, `task_id`, `tenant_id`, and `profile`.

Forbidden by policy or runner design:

- privileged containers
- Docker socket mounts
- host network
- host pid
- project root mounts
- `.env`, `.git`, `.venv`, `node_modules`, `Dockerfile`, `docker-compose.yml`
- secret env names such as `OPENAI_API_KEY`, `MYSQL_PASSWORD`, `GATEWAY_JWT_SECRET`

## Network Policy

The default network mode is `none` for all profiles.

`allowlist` is only accepted for `deep` when both `SANDBOX_ENABLE_NETWORK=true` and `SANDBOX_DEEP_ENABLE_NETWORK=true` are set, and requested domains are present in `SANDBOX_ALLOWED_DOMAINS` when that env var is configured.

Phase 1 does not give raw container network access. The Docker container still runs with `--network none`. Tools that need internet access should use a Sandbox Server proxy layer that validates URLs/domains before making requests. This avoids depending on Docker-level domain filtering, which is not portable across Docker Desktop and Linux hosts.

Blocked targets include localhost, loopback, private IP ranges, link-local metadata addresses, `file://`, and `ftp://`.

## File Policy

The workspace manager only accepts relative paths. It rejects absolute paths, `..` traversal, and sensitive names. Inputs are size-limited by `SANDBOX_MAX_INPUT_BYTES`; outputs are collected only from `/workspace/output` and limited by `SANDBOX_MAX_OUTPUT_BYTES`.

Workspaces default to `output/sandbox/{task_id}` and are deleted after execution unless `SANDBOX_KEEP_WORKSPACE_ON_FAILURE=true`.

## Adding A Sandbox Tool

1. Add or update a `ToolSpec` in `agent/tools/registry.py`:

```python
ToolSpec(
    name="my_file_tool",
    category="file",
    tool_factory=..., 
    execution_mode="sandbox",
    sandbox_runtime="python",
    sandbox_required=True,
    allowed_profiles=["standard", "deep"],
    needs_filesystem=True,
)
```

2. In `agent/subagent/tools.py` or the LangChain wrapper, build a `SandboxTask` and call `SandboxClient.execute()`.
3. Do not import Docker outside `api/sandbox`.
4. Return a structured `ToolSandboxError` when denied instead of throwing an uncaught exception to the Agent.
5. Add policy and workspace tests for the new path.

## Build Images

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_sandbox_images.ps1
```

The images intentionally do not copy project source or `.env`.

## Troubleshooting

- `Docker is not available`: start Docker Desktop or install Docker Engine.
- `sandbox python image is not built`: run `scripts/build_sandbox_images.ps1`.
- `invalid sandbox internal token`: set `SANDBOX_SERVER_INTERNAL_TOKEN` consistently for Brain and callers.
- `path traversal is not allowed`: pass a file path relative to the task input root.
- `network sandbox tasks require deep profile`: switch to `deep` and configure allowlist env vars.
- `shell sandbox runtime is disabled`: set `SANDBOX_ENABLE_SHELL=true` only in controlled environments.

## Tests

Always-run unit coverage:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_sandbox_models.py tests/unit/test_sandbox_policy.py tests/unit/test_sandbox_workspace.py -q
```

Docker-specific coverage:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_sandbox_images.ps1
.\.venv\Scripts\python.exe -m pytest tests/unit/test_sandbox_docker_runner.py tests/e2e/test_docker_sandbox.py -q
```

Docker-specific tests skip automatically when Docker or sandbox images are unavailable.