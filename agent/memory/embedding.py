import os
import atexit
import json
import queue
import subprocess
import sys
import threading
import uuid
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from typing import List

from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[2]
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass


class EmbeddingUnavailable(RuntimeError):
    pass


def embedding_enabled() -> bool:
    return os.getenv("MEMORY_EMBEDDING_ENABLED", "false").lower() == "true"


def embedding_provider() -> str:
    return os.getenv("MEMORY_EMBEDDING_PROVIDER", "local_bge_m3").lower()


def embedding_model_name() -> str:
    return os.getenv("MEMORY_EMBEDDING_MODEL") or os.getenv("BGE_M3") or "BAAI/bge-m3"


def embedding_dimension() -> int:
    return int(os.getenv("MEMORY_EMBEDDING_DIM", "1024"))


def local_bge_process_mode() -> str:
    return os.getenv("BGE_PROCESS_MODE", "worker").strip().lower()


def embed_text(text: str) -> List[float]:
    if not embedding_enabled():
        raise EmbeddingUnavailable("memory embedding is disabled")

    if embedding_provider() == "local_bge_m3":
        return _embed_text_with_local_bge_m3(text)

    return _embed_text_with_openai_compatible_api(text)


def _embed_text_with_openai_compatible_api(text: str) -> List[float]:
    api_key = os.getenv("MEMORY_EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("MEMORY_EMBEDDING_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    if not api_key or not base_url:
        raise EmbeddingUnavailable("MEMORY_EMBEDDING_API_KEY/MEMORY_EMBEDDING_BASE_URL is not configured")

    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.embeddings.create(model=embedding_model_name(), input=text[:8000])
    vector = response.data[0].embedding
    if not vector:
        raise EmbeddingUnavailable("embedding provider returned an empty vector")
    return [float(value) for value in vector]


def _embed_text_with_local_bge_m3(text: str) -> List[float]:
    if local_bge_process_mode() == "worker":
        return _get_local_bge_m3_worker().embed(text)

    model = _get_local_bge_m3_model()
    result = model.encode(text[:8000], return_dense=True, return_sparse=False, return_colbert_vecs=False)
    dense_vector = result.get("dense_vecs") if isinstance(result, dict) else result
    if hasattr(dense_vector, "tolist"):
        dense_vector = dense_vector.tolist()
    if dense_vector and isinstance(dense_vector[0], list):
        dense_vector = dense_vector[0]
    if not dense_vector:
        raise EmbeddingUnavailable("local BGE-M3 returned an empty dense vector")
    return [float(value) for value in dense_vector]


@lru_cache(maxsize=1)
def _get_local_bge_m3_model():
    try:
        flag_embedding = import_module("FlagEmbedding")
    except ImportError as error:
        raise EmbeddingUnavailable("FlagEmbedding is not installed; install requirements.txt to use local BGE-M3") from error

    model_class = getattr(flag_embedding, "BGEM3FlagModel")
    model_path = os.getenv("BGE_M3_PATH") or embedding_model_name()
    device = os.getenv("BGE_DEVICE", "cpu")
    use_fp16 = os.getenv("BGE_FP16", "0").strip().lower() in {"1", "true", "yes", "on"}
    if device == "cpu":
        use_fp16 = False

    try:
        return model_class(model_path, use_fp16=use_fp16, device=device)
    except TypeError:
        # 兼容旧版 FlagEmbedding：部分版本不接受 device 参数，会自行选择运行设备。
        return model_class(model_path, use_fp16=use_fp16)


class _LocalBGEWorker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._responses: "queue.Queue[dict]" = queue.Queue()
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None

    def embed(self, text: str) -> List[float]:
        with self._lock:
            process = self._ensure_started()
            request_id = str(uuid.uuid4())
            payload = json.dumps({"id": request_id, "text": text[:8000]}, ensure_ascii=False)
            try:
                assert process.stdin is not None
                process.stdin.write(payload + "\n")
                process.stdin.flush()
            except Exception as error:
                self._stop()
                raise EmbeddingUnavailable(f"local BGE worker is unavailable: {error}") from error

            timeout = float(os.getenv("BGE_WORKER_TIMEOUT_SECONDS", "300"))
            try:
                response = self._responses.get(timeout=timeout)
            except queue.Empty as error:
                self._stop()
                raise EmbeddingUnavailable(f"local BGE worker timed out after {timeout:g}s") from error

            if response.get("id") != request_id:
                self._stop()
                raise EmbeddingUnavailable("local BGE worker returned an unexpected response")
            if response.get("error"):
                raise EmbeddingUnavailable(f"local BGE worker failed: {response['error']}")

            vector = response.get("vector")
            if not vector:
                raise EmbeddingUnavailable("local BGE worker returned an empty vector")
            return [float(value) for value in vector]

    def _ensure_started(self) -> subprocess.Popen:
        if self._process and self._process.poll() is None:
            return self._process

        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("TOKENIZERS_PARALLELISM", "false")
        self._process = subprocess.Popen(
            [sys.executable, "-m", "agent.memory.bge_worker"],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_responses, daemon=True)
        self._reader.start()
        return self._process

    def _read_responses(self) -> None:
        process = self._process
        if not process or not process.stdout:
            return
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._responses.put(json.loads(line))
            except json.JSONDecodeError:
                self._responses.put({"id": "", "error": f"invalid worker response: {line[:120]}"})
        self._responses.put({"id": "", "error": "local BGE worker exited"})

    def _stop(self) -> None:
        process = self._process
        self._process = None
        if not process or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            process.kill()


@lru_cache(maxsize=1)
def _get_local_bge_m3_worker() -> _LocalBGEWorker:
    worker = _LocalBGEWorker()
    atexit.register(worker._stop)
    return worker
