import asyncio
import uuid
import uvicorn
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import shutil

# Add project root to sys.path
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent

# Import monitor and lightweight platform services only.
# 注意：agent.main_agent 会构建 DeepAgents 图并初始化模型，不能在 server 导入阶段加载。
# start_agent_task / approve_policy 会在真正需要执行 Agent 或热重载策略时再懒加载它。
from api.monitor import manager
from api.task_queue import task_queue
from api.task_runtime import task_runtime
from agent.memory.retriever import retrieve_long_term_memories
from agent.memory.schema import MemoryIdentity
from agent.memory.store import get_memory_store, init_memory_store
from agent.core.tool_registry import tool_registry
from agent.evolution.policy_review import approve_policy_proposal, list_policy_proposals, reject_policy_proposal
from agent.observability.trace_reader import build_agent_metrics, build_task_timeline, list_task_traces
from api.routes import agents, ai_chat, campaigns, dashboard, data_import, integrations, inventory, onboarding, products, reports, shops, workspace

app = FastAPI(title="DeepAgents API")

# 产品化 SaaS 前端所需的业务 API 独立拆到 api/routes 与 api/services。
# 这里仅挂载 router，不把平台业务继续塞进 server.py，避免入口文件继续膨胀。
app.include_router(workspace.router)
app.include_router(dashboard.router)
app.include_router(products.router)
app.include_router(inventory.router)
app.include_router(campaigns.router)
app.include_router(reports.router)
app.include_router(agents.router)
app.include_router(data_import.router)
app.include_router(shops.router)
app.include_router(integrations.router)
app.include_router(onboarding.router)
app.include_router(ai_chat.router)

# 挂载输出目录，以便前端访问生成的静态文件
# 假设输出目录位于项目根目录下的 output
output_dir = project_root / "output"
output_dir.mkdir(exist_ok=True)

# 定义上传目录 updated
updated_dir = project_root / "updated"
updated_dir.mkdir(exist_ok=True)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
class TaskRequest(BaseModel):
    query: str
    thread_id: str = None
    conversation_id: str = None
    task_id: str = None
    tenant_id: str = "default_tenant"
    user_id: str = "local_user"
    shop_id: str = "default_shop"

class MemorySearchRequest(BaseModel):
    query: str
    top_k: int = 3
    tenant_id: str = "default_tenant"
    user_id: str = "local_user"
    shop_id: str = "default_shop"

class MemoryReviewActionRequest(BaseModel):
    reviewer_id: str = "local_user"
    comment: str = ""

class ResumeTaskRequest(BaseModel):
    decision: str
    instruction: str = ""


def trusted_identity(http_request: Request, tenant_id: str, user_id: str, shop_id: str) -> MemoryIdentity:
    """解析可信身份：网关注入的 Header 优先，body/query 只作为本地直连调试兜底。"""
    return MemoryIdentity(
        tenant_id=http_request.headers.get("X-Tenant-ID") or tenant_id,
        user_id=http_request.headers.get("X-User-ID") or user_id,
        shop_id=http_request.headers.get("X-Shop-ID") or shop_id,
    )


def identity_scope(identity: MemoryIdentity) -> dict:
    """转换为 task_runtime 使用的身份字典，确保所有资源级校验使用同一组字段。"""
    return {
        "tenant_id": identity.tenant_id,
        "user_id": identity.user_id,
        "shop_id": identity.shop_id,
    }


def session_dir_for(conversation_id: str) -> Path:
    """
    根据 conversation_id 定位 Agent 产物目录。

    目录名只能由服务端拼接，不能接受前端传入的绝对 path；真正的归属关系由 task_runtime 元数据校验。
    """
    return (output_dir / f"session_{conversation_id}").resolve()


def resolve_session_file(conversation_id: str, filename: str) -> Path:
    """把会话内文件名解析为绝对路径，并阻止 .. 或绝对路径逃逸到会话目录之外。"""
    if not filename or Path(filename).is_absolute():
        raise ValueError("文件名无效")
    session_dir = session_dir_for(conversation_id)
    candidate = (session_dir / filename).resolve()
    if not candidate.is_relative_to(session_dir):
        raise ValueError("拒绝访问: 文件不属于当前会话目录")
    return candidate


async def ensure_conversation_access(conversation_id: str, identity: MemoryIdentity) -> None:
    """确认 conversation 属于当前 tenant/user/shop，文件列表和下载都必须先通过这里。"""
    if not await task_runtime.owns_conversation(conversation_id, identity_scope(identity)):
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")

@app.on_event("startup")
async def startup_event():
    """
    服务启动时，获取当前运行的事件循环，并绑定到 WebSocket 管理器。
    确保后台线程能通过 run_coroutine_threadsafe 准确投递消息。
    """
    loop = asyncio.get_running_loop()
    manager.set_loop(loop)
    init_memory_store()
    await task_queue.start(start_agent_task)
    print(f"[Server] WebSocket Manager bound to loop: {id(loop)}")


async def start_agent_task(payload: dict):
    from agent.main_agent import run_deep_agent
    from api.services.job_result_service import finalize_agent_job_failure, finalize_agent_job_success

    conversation_id = payload["conversation_id"]
    task_id = payload["task_id"]
    if await task_runtime.is_cancelled(task_id):
        return
    metadata = {
        "conversation_id": conversation_id,
        "tenant_id": payload["tenant_id"],
        "user_id": payload["user_id"],
        "shop_id": payload["shop_id"],
    }
    async def run_and_finalize():
        try:
            final_result = await run_deep_agent(
                payload["query"],
                conversation_id=conversation_id,
                task_id=task_id,
                tenant_id=payload["tenant_id"],
                user_id=payload["user_id"],
                shop_id=payload["shop_id"],
            )
            await finalize_agent_job_success(
                tenant_id=payload["tenant_id"],
                shop_id=payload["shop_id"],
                user_id=payload["user_id"],
                task_id=task_id,
                conversation_id=conversation_id,
                final_result=final_result,
                execution_metadata=metadata,
            )
            return final_result
        except Exception as error:
            await finalize_agent_job_failure(
                tenant_id=payload["tenant_id"],
                shop_id=payload["shop_id"],
                user_id=payload["user_id"],
                task_id=task_id,
                conversation_id=conversation_id,
                error_message=str(error),
            )
            raise

    agent_coroutine = run_and_finalize()
    try:
        await task_runtime.start_and_wait(task_id, payload["query"], agent_coroutine, metadata=metadata)
    except Exception:
        if hasattr(agent_coroutine, "close"):
            agent_coroutine.close()
        raise


@app.post("/api/task")
async def run_task(request: TaskRequest, http_request: Request):
    # 1. [ID 初始化] conversation_id 兼容旧前端 thread_id；task_id 是单次后台执行。
    conversation_id = request.conversation_id or request.thread_id or str(uuid.uuid4())
    task_id = request.task_id or str(uuid.uuid4())
    identity = trusted_identity(http_request, request.tenant_id, request.user_id, request.shop_id)
    if await task_runtime.conversation_exists(conversation_id) and not await task_runtime.owns_conversation(conversation_id, identity_scope(identity)):
        raise HTTPException(status_code=403, detail="无权复用该会话")
    payload = {
        "query": request.query,
        "conversation_id": conversation_id,
        "thread_id": conversation_id,
        "task_id": task_id,
        "tenant_id": identity.tenant_id,
        "user_id": identity.user_id,
        "shop_id": identity.shop_id,
    }

    # 2. [后台执行] 异步运行 Agent，不阻塞主线程
    try:
        await task_runtime.enqueue(task_id, request.query, metadata={
            "conversation_id": conversation_id,
            "tenant_id": identity.tenant_id,
            "user_id": identity.user_id,
            "shop_id": identity.shop_id,
        })
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await task_queue.enqueue(payload)

    # 3. [立即响应]
    return {"status": "started", "thread_id": conversation_id, "conversation_id": conversation_id, "task_id": task_id}


@app.get("/api/tasks")
async def list_tasks(http_request: Request):
    identity = trusted_identity(http_request, "default_tenant", "local_user", "default_shop")
    return {"tasks": await task_runtime.list_scoped(identity_scope(identity))}


@app.get("/api/task/{thread_id}")
async def get_task(thread_id: str, http_request: Request):
    identity = trusted_identity(http_request, "default_tenant", "local_user", "default_shop")
    task = await task_runtime.get_scoped(thread_id, identity_scope(identity))
    if not task:
        return {"error": "任务不存在", "thread_id": thread_id}
    return task


@app.post("/api/task/{thread_id}/cancel")
async def cancel_task(thread_id: str, http_request: Request):
    identity = trusted_identity(http_request, "default_tenant", "local_user", "default_shop")
    task_id = await task_runtime.resolve_task_id(thread_id)
    task = await task_runtime.get_scoped(task_id, identity_scope(identity))
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")
    cancelled = await task_runtime.cancel(task_id)
    return {"thread_id": thread_id, "task_id": task_id, "cancelled": cancelled}


@app.post("/api/task/{thread_id}/resume")
async def resume_task(thread_id: str, request: ResumeTaskRequest, http_request: Request):
    try:
        identity = trusted_identity(http_request, "default_tenant", "local_user", "default_shop")
        task_id = await task_runtime.resolve_task_id(thread_id)
        task = await task_runtime.get_scoped(task_id, identity_scope(identity))
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在或无权访问")
        resumed = await task_runtime.resume(task_id, request.decision, request.instruction)
        return {"thread_id": thread_id, "task_id": task_id, "resumed": resumed, "decision": request.decision}
    except ValueError as error:
        return {"thread_id": thread_id, "resumed": False, "error": str(error)}


@app.post("/api/memories/search")
async def search_memories(request: MemorySearchRequest, http_request: Request):
    identity = trusted_identity(http_request, request.tenant_id, request.user_id, request.shop_id)
    return {"memories": retrieve_long_term_memories(identity, request.query, request.top_k)}


@app.get("/api/tools/catalog")
async def get_tool_catalog():
    # 给前端/外部系统展示统一工具目录。这里只返回 metadata，不暴露实际 Python tool 对象。
    return {"tools": tool_registry.catalog()}


@app.get("/api/traces/{task_id}")
async def get_task_traces(task_id: str, http_request: Request):
    """
    返回单个任务的原始 trace 事件。

    这个接口直接读取 agent_traces.jsonl，适合排障和前端详情页；后续如果 trace 落到数据库，保持
    返回结构不变即可。
    """
    identity = trusted_identity(http_request, "default_tenant", "local_user", "default_shop")
    task = await task_runtime.get_scoped(task_id, identity_scope(identity))
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")
    return {"task_id": task_id, "events": list_task_traces(task_id)}


@app.get("/api/traces/{task_id}/timeline")
async def get_task_trace_timeline(task_id: str, http_request: Request):
    """返回前端更容易渲染的任务时间线：Agent、工具、耗时、token 和失败点。"""
    identity = trusted_identity(http_request, "default_tenant", "local_user", "default_shop")
    task = await task_runtime.get_scoped(task_id, identity_scope(identity))
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")
    return build_task_timeline(task_id)


@app.get("/api/metrics/agents")
async def get_agent_metrics():
    """返回基于本地 JSONL trace 聚合的 Agent/工具指标。"""
    return build_agent_metrics()


@app.get("/api/memories/reviews")
async def list_memory_reviews(
    http_request: Request,
    tenant_id: str = "default_tenant",
    user_id: str = "local_user",
    shop_id: str = "default_shop",
    status: str = "pending",
    limit: int = 50,
):
    identity = trusted_identity(http_request, tenant_id, user_id, shop_id)
    return {"reviews": get_memory_store().list_reviews(identity, status=status, limit=limit)}


@app.post("/api/memories/reviews/{review_id}/approve")
async def approve_memory_review(review_id: str, request: MemoryReviewActionRequest, http_request: Request):
    reviewer_id = http_request.headers.get("X-User-ID") or request.reviewer_id
    try:
        return get_memory_store().approve_review(review_id, reviewer_id, request.comment)
    except ValueError as error:
        return {"error": str(error), "review_id": review_id}


@app.post("/api/memories/reviews/{review_id}/reject")
async def reject_memory_review(review_id: str, request: MemoryReviewActionRequest, http_request: Request):
    reviewer_id = http_request.headers.get("X-User-ID") or request.reviewer_id
    try:
        return get_memory_store().reject_review(review_id, reviewer_id, request.comment)
    except ValueError as error:
        return {"error": str(error), "review_id": review_id}


@app.get("/api/policy/proposals")
async def get_policy_proposals(http_request: Request, status: str = None):
    identity = trusted_identity(http_request, "default_tenant", "local_user", "default_shop")
    proposals = []
    for proposal in list_policy_proposals(status):
        if await task_runtime.get_scoped(proposal.get("session_id", ""), identity_scope(identity)):
            proposals.append(proposal)
    return {"proposals": proposals}


@app.post("/api/policy/proposals/{proposal_id}/approve")
async def approve_policy(proposal_id: str, http_request: Request):
    try:
        from agent.main_agent import reload_agent_policy

        identity = trusted_identity(http_request, "default_tenant", "local_user", "default_shop")
        proposal = next((item for item in list_policy_proposals() if item.get("proposal_id") == proposal_id), None)
        if not proposal or not await task_runtime.get_scoped(proposal.get("session_id", ""), identity_scope(identity)):
            raise HTTPException(status_code=404, detail="策略建议不存在或无权访问")
        proposal = approve_policy_proposal(proposal_id)
        reload_agent_policy()
        return proposal
    except ValueError as error:
        return {"error": str(error)}


@app.post("/api/policy/proposals/{proposal_id}/reject")
async def reject_policy(proposal_id: str, http_request: Request):
    try:
        identity = trusted_identity(http_request, "default_tenant", "local_user", "default_shop")
        proposal = next((item for item in list_policy_proposals() if item.get("proposal_id") == proposal_id), None)
        if not proposal or not await task_runtime.get_scoped(proposal.get("session_id", ""), identity_scope(identity)):
            raise HTTPException(status_code=404, detail="策略建议不存在或无权访问")
        return reject_policy_proposal(proposal_id)
    except ValueError as error:
        return {"error": str(error)}


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...), thread_id: str = Form(...)):
    """
    文件上传接口 (File Upload)。

    目标：
    1. 接收用户上传的一个或多个文件。
    2. 保存到 `updated/session_{thread_id}` 目录。
    3. 供 Agent 在后续任务中读取和分析。

    Args:
        files (List[UploadFile]): 文件对象列表。
        thread_id (str): 关联的任务会话 ID。
    """
    # 1. [目录准备] 确保上传目录存在
    target_dir = updated_dir / f"session_{thread_id}"
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    # 2. [保存] 遍历并写入文件
    for file in files:
        file_path = target_dir / file.filename
        # 使用二进制模式写入，支持各种文件格式 (图片、PDF、文本等)
        # shutil.copyfileobj 高效复制文件流，避免一次性加载大文件到内存
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_files.append(file.filename)

    # 3. [响应] 返回成功保存的文件列表
    return {"status": "uploaded", "files": saved_files}


@app.get("/api/download")
async def download_file(conversation_id: str, filename: str, http_request: Request):
    """
    文件下载接口 (File Download)。

    目标：
    1. 只允许下载当前 conversation 工作目录下的文件。
    2. 先用 task_runtime 元数据确认 conversation 属于当前 tenant/user/shop。
    3. 不再接受前端传任意绝对路径，避免跨租户猜路径下载 output/session_xxx 文件。
    """
    try:
        identity = trusted_identity(http_request, "default_tenant", "local_user", "default_shop")
        await ensure_conversation_access(conversation_id, identity)
        abs_path = resolve_session_file(conversation_id, filename)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(abs_path, filename=abs_path.name)


@app.get("/api/files")
async def list_files(conversation_id: str, http_request: Request):
    """
    文件列表查询接口 (File Explorer)。

    目标：
    1. 通过 conversation_id 定位 output/session_{conversation_id}。
    2. 先确认该 conversation 属于当前网关注入的 tenant/user/shop。
    3. 返回会话内相对 filename，不再把服务器绝对路径交给前端。
    """
    try:
        identity = trusted_identity(http_request, "default_tenant", "local_user", "default_shop")
        await ensure_conversation_access(conversation_id, identity)
        abs_path = session_dir_for(conversation_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if not abs_path.exists():
        return {"files": []}

    files = []
    try:
        # 5. [遍历] 递归查找所有文件
        for file_path in abs_path.rglob("*"):
            if file_path.is_file():
                stat = file_path.stat()
                filename = file_path.relative_to(abs_path).as_posix()
                files.append({
                    "name": file_path.name,
                    "type": "file",
                    "filename": filename,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime
                })

    except Exception as e:
        print(f"[ERROR] 遍历文件失败: {e}")
        return {"error": str(e)}

    # 6. [排序] 按修改时间倒序排列 (最新的在前)
    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    print(f"[DEBUG] 找到 {len(files)} 个文件")
    return {"files": files}


# 当浏览器请求 ws://localhost:8000/ws/thread_123 时：
# 1. 路由匹配 ：FastAPI 发现这个 URL 匹配了你写的 @app.websocket("/ws/{thread_id}") 。
# 2. 创建对象 ：FastAPI (基于 Starlette) 会立刻在 主事件循环 中实例化一个 WebSocket 对象。
#    - 这个对象封装了底层的 TCP 连接、HTTP 握手信息、以及后续的消息收发方法 ( send_text , receive_text 等)。
# 3. 注入参数 ：FastAPI 自动把这个刚创建好的 WebSocket 对象，作为参数传给你的 websocket_endpoint(websocket, ...) 函数。
@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    print(f"会话向我们发起了请求，要求建立连接：{thread_id} 对应：{websocket}")
    """
    WebSocket 实时通讯核心接口 (Real-time Communication)。
    目标：
    1. 建立长连接，实现服务端与前端的双向通信。
    2. 绑定 `thread_id`，实现会话级消息隔离。
    3. 维持心跳 (Keep-Alive)，防止连接超时。

    执行步骤：
    1. 握手：接受 WebSocket 连接请求。
    2. 注册：将连接实例绑定到 `monitor.manager`，关联 `thread_id`。
    3. 循环：进入消息监听循环，处理前端发送的心跳或指令。
    4. 异常：捕获断开连接异常，清理资源。

    Args:
        websocket (WebSocket): WebSocket 连接实例。
        thread_id (str): 当前会话的唯一标识。
    """
    # 1. [注册] 建立连接并绑定到管理器
    await manager.connect(websocket, thread_id)

    try:
        # 2. [循环] 保持连接活跃
        while True:
            # 3. [监听] 接收前端消息 (通常是 ping 心跳)
            data = await websocket.receive_text()

            # 4. [响应] 回复 pong 消息
            await websocket.send_json({
                "type": "pong",
                "message": f"服务端已收到: {data}"
            })

    except WebSocketDisconnect:
        # 5. [清理] 客户端主动断开
        manager.disconnect(websocket, thread_id)
        print(f"[WebSocket] 客户端已断开: {thread_id}")

    except Exception as e:
        # 6. [异常] 发生错误时断开
        print(f"[WebSocket] 连接异常: {e}")
        manager.disconnect(websocket, thread_id)

if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)