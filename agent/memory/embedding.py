import os
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
