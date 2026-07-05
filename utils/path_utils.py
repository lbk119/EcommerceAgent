import os
from pathlib import Path
from typing import Optional


def resolve_path(filename: str, session_dir: Optional[str] = None) -> str:
    """
    统一的文件路径解析工具方法。

    有 session_dir 时启用严格沙箱：所有工具读写路径都必须 resolve 到 session_dir 内。
    支持文件名、相对路径、虚拟路径 /workspace/...、/mnt/data/...、/home/user/... 映射到 session_dir。
    updated/... 只作为兼容输入映射到 session_dir 内的已复制文件，不直接开放项目 updated 目录。
    """
    if not filename or not str(filename).strip():
        raise ValueError("文件路径不能为空")

    raw_path = str(filename).strip()
    path_str = raw_path.replace("\\", "/")

    if not session_dir:
        return str(Path(raw_path).resolve())

    session_path = Path(session_dir).resolve()
    session_name = session_path.name
    path_str = _strip_virtual_prefix(path_str)
    path_str = _map_updated_reference(path_str)

    input_path = Path(path_str)
    is_windows_unix_style_abs = os.name == "nt" and path_str.startswith("/") and not input_path.drive

    if input_path.is_absolute() and not is_windows_unix_style_abs:
        resolved = input_path.resolve()
    else:
        relative_path = _normalize_session_relative_path(path_str, session_name)
        resolved = (session_path / relative_path).resolve()

    if not _is_relative_to(resolved, session_path):
        raise ValueError(f"路径越界：{filename} 不在当前会话工作目录内")
    return str(resolved)


def _strip_virtual_prefix(path_str: str) -> str:
    for prefix in ("/workspace", "/mnt/data", "/home/user"):
        if path_str == prefix or path_str.startswith(f"{prefix}/"):
            return path_str[len(prefix):].lstrip("/")
    return path_str


def _map_updated_reference(path_str: str) -> str:
    parts = Path(path_str).parts
    normalized_parts = [part.replace("\\", "/") for part in parts]
    if "updated" not in normalized_parts:
        return path_str

    updated_index = normalized_parts.index("updated")
    trailing_parts = list(normalized_parts[updated_index + 1:])
    if trailing_parts and trailing_parts[0].startswith("session_"):
        trailing_parts = trailing_parts[1:]
    if not trailing_parts:
        raise ValueError("updated 路径必须指向已复制到会话目录内的具体文件")
    return str(Path(*trailing_parts))


def _normalize_session_relative_path(path_str: str, session_name: str) -> Path:
    relative_path = Path(path_str.lstrip("/"))
    parts = list(relative_path.parts)

    if session_name in parts:
        parts = parts[parts.index(session_name) + 1:]
    elif parts and parts[0] == "output":
        parts = parts[1:]

    if not parts:
        return Path(".")
    return Path(*parts)


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False