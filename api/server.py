import uuid
import asyncio
import uvicorn
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException
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

app = FastAPI(title="DeepAgents API")

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
    agent_coroutine = run_deep_agent(
        payload["query"],
        conversation_id=conversation_id,
        task_id=task_id,
        tenant_id=payload["tenant_id"],
        user_id=payload["user_id"],
        shop_id=payload["shop_id"],
    )
    try:
        await task_runtime.start_and_wait(task_id, payload["query"], agent_coroutine, metadata=metadata)
    except Exception:
        if hasattr(agent_coroutine, "close"):
            agent_coroutine.close()
        raise


@app.post("/api/task")
async def run_task(request: TaskRequest):
    # 1. [ID 初始化] conversation_id 兼容旧前端 thread_id；task_id 是单次后台执行。
    conversation_id = request.conversation_id or request.thread_id or str(uuid.uuid4())
    task_id = request.task_id or str(uuid.uuid4())
    payload = {
        "query": request.query,
        "conversation_id": conversation_id,
        "thread_id": conversation_id,
        "task_id": task_id,
        "tenant_id": request.tenant_id,
        "user_id": request.user_id,
        "shop_id": request.shop_id,
    }

    # 2. [后台执行] 异步运行 Agent，不阻塞主线程
    try:
        await task_runtime.enqueue(task_id, request.query, metadata={
            "conversation_id": conversation_id,
            "tenant_id": request.tenant_id,
            "user_id": request.user_id,
            "shop_id": request.shop_id,
        })
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await task_queue.enqueue(payload)

    # 3. [立即响应]
    return {"status": "started", "thread_id": conversation_id, "conversation_id": conversation_id, "task_id": task_id}


@app.get("/api/tasks")
async def list_tasks():
    return {"tasks": await task_runtime.list()}


@app.get("/api/task/{thread_id}")
async def get_task(thread_id: str):
    task_id = await task_runtime.resolve_task_id(thread_id)
    task = await task_runtime.get(task_id)
    if not task:
        return {"error": "任务不存在", "thread_id": thread_id}
    return task


@app.post("/api/task/{thread_id}/cancel")
async def cancel_task(thread_id: str):
    task_id = await task_runtime.resolve_task_id(thread_id)
    cancelled = await task_runtime.cancel(task_id)
    return {"thread_id": thread_id, "task_id": task_id, "cancelled": cancelled}


@app.post("/api/task/{thread_id}/resume")
async def resume_task(thread_id: str, request: ResumeTaskRequest):
    try:
        task_id = await task_runtime.resolve_task_id(thread_id)
        resumed = await task_runtime.resume(task_id, request.decision, request.instruction)
        return {"thread_id": thread_id, "task_id": task_id, "resumed": resumed, "decision": request.decision}
    except ValueError as error:
        return {"thread_id": thread_id, "resumed": False, "error": str(error)}


@app.post("/api/memories/search")
async def search_memories(request: MemorySearchRequest):
    identity = MemoryIdentity(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        shop_id=request.shop_id,
    )
    return {"memories": retrieve_long_term_memories(identity, request.query, request.top_k)}


@app.get("/api/tools/catalog")
async def get_tool_catalog():
    # 给前端/外部系统展示统一工具目录。这里只返回 metadata，不暴露实际 Python tool 对象。
    return {"tools": tool_registry.catalog()}


@app.get("/api/traces/{task_id}")
async def get_task_traces(task_id: str):
    """
    返回单个任务的原始 trace 事件。

    这个接口直接读取 agent_traces.jsonl，适合排障和前端详情页；后续如果 trace 落到数据库，保持
    返回结构不变即可。
    """
    return {"task_id": task_id, "events": list_task_traces(task_id)}


@app.get("/api/traces/{task_id}/timeline")
async def get_task_trace_timeline(task_id: str):
    """返回前端更容易渲染的任务时间线：Agent、工具、耗时、token 和失败点。"""
    return build_task_timeline(task_id)


@app.get("/api/metrics/agents")
async def get_agent_metrics():
    """返回基于本地 JSONL trace 聚合的 Agent/工具指标。"""
    return build_agent_metrics()


@app.get("/api/memories/reviews")
async def list_memory_reviews(
    tenant_id: str = "default_tenant",
    user_id: str = "local_user",
    shop_id: str = "default_shop",
    status: str = "pending",
    limit: int = 50,
):
    identity = MemoryIdentity(tenant_id=tenant_id, user_id=user_id, shop_id=shop_id)
    return {"reviews": get_memory_store().list_reviews(identity, status=status, limit=limit)}


@app.post("/api/memories/reviews/{review_id}/approve")
async def approve_memory_review(review_id: str, request: MemoryReviewActionRequest):
    try:
        return get_memory_store().approve_review(review_id, request.reviewer_id, request.comment)
    except ValueError as error:
        return {"error": str(error), "review_id": review_id}


@app.post("/api/memories/reviews/{review_id}/reject")
async def reject_memory_review(review_id: str, request: MemoryReviewActionRequest):
    try:
        return get_memory_store().reject_review(review_id, request.reviewer_id, request.comment)
    except ValueError as error:
        return {"error": str(error), "review_id": review_id}


@app.get("/api/policy/proposals")
async def get_policy_proposals(status: str = None):
    return {"proposals": list_policy_proposals(status)}


@app.post("/api/policy/proposals/{proposal_id}/approve")
async def approve_policy(proposal_id: str):
    try:
        from agent.main_agent import reload_agent_policy

        proposal = approve_policy_proposal(proposal_id)
        reload_agent_policy()
        return proposal
    except ValueError as error:
        return {"error": str(error)}


@app.post("/api/policy/proposals/{proposal_id}/reject")
async def reject_policy(proposal_id: str):
    try:
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
async def download_file(path: str):
    """
    文件下载接口 (File Download)。

    目标：
    1. 根据绝对路径下载文件。
    2. 严格的安全检查，防止越权访问。

    Args:
        path (str): 文件的绝对路径 (通常从 list_files 接口获取)。
    """
    # 1. [安全检查] 路径解析与越权校验
    try:
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        # 必须确保请求的文件在 output 目录下
        if not abs_path.is_relative_to(output_abs):
            return {"error": "拒绝访问: 只能下载输出目录下的文件"}
    except Exception:
        return {"error": "无效的路径参数"}
    # 2. [存在性检查]
    if not abs_path.exists():
        return {"error": "文件不存在"}

    # 3. [响应] 返回文件流 (浏览器自动触发下载)
    return FileResponse(abs_path, filename=abs_path.name)


@app.get("/api/files")
async def list_files(path: str):
    """
    文件列表查询接口 (File Explorer)。

    目标：
    1. 列出指定目录下的所有生成文件。
    2. 提供文件元数据（大小、时间、下载链接）。
    3. 严格的安全检查，防止路径遍历攻击。

    Args:
        path (str): 目标目录的绝对路径 (必须在 output 目录下)。
    """
    # 1. [调试] 打印请求路径
    print(f"[DEBUG] 请求文件列表: {path}")

    try:
        # 2. [解析] 获取绝对路径对象
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        # 3. [安全] 检查路径是否越界 (Path Traversal Check)
        if not abs_path.is_relative_to(output_abs):
            print(f"[ERROR] 拒绝访问: {abs_path} 不在 {output_abs} 目录下")
            return {"error": "拒绝访问: 只能访问输出目录下的文件"}

    except Exception as e:
        print(f"[ERROR] 路径解析失败: {e}")
        return {"error": f"路径无效: {e}"}

    # 4. [检查] 目录是否存在
    if not abs_path.exists():
        return {"error": "目录不存在"}

    files = []
    try:
        # 5. [遍历] 递归查找所有文件
        for file_path in abs_path.rglob("*"):
            if file_path.is_file():
                # 计算相对路径，生成下载 URL
                stat = file_path.stat()
                files.append({
                    "name": file_path.name,
                    "type": "file",
                    "path": str(file_path),
                    # "url": f"/outputs/{url_path}",
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