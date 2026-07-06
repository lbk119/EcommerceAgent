import json
import os
import sys
from pathlib import Path
from typing import Any, List


PROJECT_ROOT = Path(__file__).resolve().parents[2]
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass


def _bool_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _model_name() -> str:
    return os.getenv("MEMORY_EMBEDDING_MODEL") or os.getenv("BGE_M3") or "BAAI/bge-m3"


def _load_model() -> Any:
    from FlagEmbedding import BGEM3FlagModel

    model_path = os.getenv("BGE_M3_PATH") or _model_name()
    device = os.getenv("BGE_DEVICE", "cpu")
    use_fp16 = _bool_env("BGE_FP16")
    if device == "cpu":
        use_fp16 = False

    try:
        return BGEM3FlagModel(model_path, use_fp16=use_fp16, device=device)
    except TypeError:
        return BGEM3FlagModel(model_path, use_fp16=use_fp16)


def _encode(model: Any, text: str) -> List[float]:
    result = model.encode(text[:8000], return_dense=True, return_sparse=False, return_colbert_vecs=False)
    dense_vector = result.get("dense_vecs") if isinstance(result, dict) else result
    if hasattr(dense_vector, "tolist"):
        dense_vector = dense_vector.tolist()
    if dense_vector and isinstance(dense_vector[0], list):
        dense_vector = dense_vector[0]
    if not dense_vector:
        raise RuntimeError("local BGE-M3 returned an empty dense vector")
    return [float(value) for value in dense_vector]


def _write(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    model = _load_model()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            request_id = request.get("id", "")
            if request.get("command") == "shutdown":
                _write({"id": request_id, "ok": True})
                os._exit(0)
            vector = _encode(model, request.get("text", ""))
            _write({"id": request_id, "vector": vector})
        except Exception as error:
            _write({"id": request.get("id", "") if "request" in locals() else "", "error": str(error)})

    os._exit(0)


if __name__ == "__main__":
    main()
