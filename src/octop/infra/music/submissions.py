"""Voice submissions: archive review, request records, whitelist writes.

FlowBee-mode users upload an RVC zip (or a model link) on the voice plaza. The
gateway validates the archive, runs a best-effort pickle safety scan and an
optional LLM review over a compact summary, then auto-approves by writing the
voice into ``sing-voices.json``. Names get a random suffix so a submitted voice
stays known only to its submitter.

Records live in ``sing-voice-requests.json`` (``SING_VOICE_REQUESTS_FILE``) so
the plaza status page can resolve a submission and a future FlowBee JWT login
can bind requests to accounts by ``flowbee_user``.
"""

from __future__ import annotations

import io
import json
import logging
import os
import pickletools
import re
import secrets
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from octop.infra.music.voices import voices_path

logger = logging.getLogger(__name__)

REQUESTS_FILE_ENV = "SING_VOICE_REQUESTS_FILE"
REVIEW_BASE_ENV = "SING_REVIEW_BASE_URL"
REVIEW_KEY_ENV = "SING_REVIEW_API_KEY"
REVIEW_MODEL_ENV = "SING_REVIEW_MODEL"
DEFAULT_REVIEW_MODEL = "deepseek-chat"

# Pickle globals that can execute code or touch the filesystem. RVC checkpoints
# only need torch/collections/numpy rebuild helpers, so anything here is a
# strong signal of a malicious upload (best effort, not a sandbox).
_BANNED_MODULES = frozenset(
    {
        "os",
        "posix",
        "nt",
        "subprocess",
        "socket",
        "shutil",
        "ctypes",
        "pty",
        "multiprocessing",
        "importlib",
        "runpy",
        "code",
        "codeop",
        "commands",
        "popen2",
        "pickle",
        "marshal",
        "webbrowser",
    }
)
_BANNED_NAMES = frozenset(
    {
        "system",
        "popen",
        "exec",
        "eval",
        "execv",
        "execve",
        "execvp",
        "spawn",
        "spawnl",
        "spawnv",
        "Popen",
        "call",
        "run",
        "check_call",
        "check_output",
        "getoutput",
        "__import__",
        "compile",
        "rmtree",
        "unlink",
        "remove",
        "chmod",
        "chown",
    }
)
_BANNED_EXTENSIONS = frozenset(
    {
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".sh",
        ".bat",
        ".cmd",
        ".ps1",
        ".js",
        ".vbs",
        ".scr",
        ".py",
        ".pyc",
        ".jar",
        ".msi",
    }
)
_PICKLE_SCAN_LIMIT = 10_000_000  # bytes per pickle member
_OPCODE_LIMIT = 500_000

_SLUG_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")
_FLOWBEE_USER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
_MAX_FILES_IN_SUMMARY = 20
_MAX_GLOBALS_IN_SUMMARY = 40


@dataclass(frozen=True)
class ReviewResult:
    approved: bool
    reason: str
    summary: dict[str, Any]


def requests_path() -> Path:
    override = os.environ.get(REQUESTS_FILE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    home = Path(os.environ.get("OCTOP_HOME") or (Path.home() / ".octop"))
    return home / "sing-voice-requests.json"


def _load_requests(path: Path | None = None) -> dict[str, Any]:
    target = path or requests_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_request(entry: dict[str, Any], path: Path | None = None) -> None:
    target = path or requests_path()
    records = _load_requests(target)
    records[str(entry["id"])] = entry
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(target)


def get_request(request_id: str, path: Path | None = None) -> dict[str, Any] | None:
    record = _load_requests(path).get(request_id)
    return record if isinstance(record, dict) else None


def add_voice(name: str, label: str, url: str, *, extra: dict[str, Any] | None = None) -> None:
    """Append an entry to the whitelist file, preserving everything else."""
    target = voices_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, ValueError):
        raw = {}
    entry: dict[str, Any] = {"label": label, "url": url}
    if extra:
        entry.update(extra)
    raw[name] = entry
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


def valid_flowbee_user(value: str) -> bool:
    return bool(_FLOWBEE_USER_RE.fullmatch((value or "").strip()))


def slugify_label(label: str) -> str:
    text = _SLUG_RE.sub("-", (label or "").strip().lower()).strip("-")
    return text[:32].strip("-") or "voice"


def generate_name(label: str, taken: set[str]) -> str:
    base = slugify_label(label)
    suffix = secrets.token_hex(2)
    name = f"{base}-{suffix}"
    while name in taken:
        suffix = secrets.token_hex(2)
        name = f"{base}-{suffix}"
    return name


def _dangerous_global(module: str, name: str) -> bool:
    top = (module or "").split(".")[0]
    return top in _BANNED_MODULES or (name or "") in _BANNED_NAMES


def scan_pickle(data: bytes, *, globals_out: set[str] | None = None) -> str | None:
    """Best-effort scan of a pickle stream; returns a reason when suspicious."""
    recent: list[str] = []
    try:
        for index, (opcode, arg, _pos) in enumerate(pickletools.genops(data)):
            if index > _OPCODE_LIMIT:
                return "pickle 操作码数量异常"
            name = opcode.name
            if name == "GLOBAL" and isinstance(arg, str):
                module, _, symbol = arg.partition(" ")
                if globals_out is not None:
                    globals_out.add(f"{module}.{symbol}")
                if _dangerous_global(module, symbol):
                    return f"pickle 引用了可执行符号 {module}.{symbol}"
            elif name == "STACK_GLOBAL":
                if len(recent) >= 2:
                    module, symbol = recent[-2], recent[-1]
                    if globals_out is not None:
                        globals_out.add(f"{module}.{symbol}")
                    if _dangerous_global(module, symbol):
                        return f"pickle 引用了可执行符号 {module}.{symbol}"
                recent.clear()
            elif name in {
                "SHORT_BINUNICODE",
                "BINUNICODE",
                "BINUNICODE8",
                "UNICODE",
                "STRING",
                "BINSTRING",
                "SHORT_BINSTRING",
            }:
                if isinstance(arg, bytes):
                    recent.append(arg.decode("utf-8", "replace"))
                elif isinstance(arg, str):
                    recent.append(arg)
                recent = recent[-4:]
    except Exception:  # noqa: BLE001 - malformed/exotic pickles are not verifiable
        logger.debug("pickle scan could not parse stream", exc_info=True)
        return None
    return None


def _zip_member_pickles(zf: zipfile.ZipFile) -> list[str]:
    names = zf.namelist()
    return [n for n in names if n.lower().endswith(".pkl")]


def _scan_archive_pickles(zf: zipfile.ZipFile, globals_out: set[str]) -> str | None:
    """Scan torch ``data.pkl`` sidecars and loose ``.pkl`` files."""
    for member in _zip_member_pickles(zf):
        try:
            with zf.open(member) as handle:
                data = handle.read(_PICKLE_SCAN_LIMIT)
        except (OSError, KeyError):
            continue
        reason = scan_pickle(data, globals_out=globals_out)
        if reason:
            return f"{member}: {reason}"
    return None


def _scan_pth_payload(data: bytes, globals_out: set[str]) -> str | None:
    """A torch checkpoint is a zip (new format) or a bare pickle (legacy)."""
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as inner:
                return _scan_archive_pickles(inner, globals_out)
        except zipfile.BadZipFile:
            return None
    return scan_pickle(data[:_PICKLE_SCAN_LIMIT], globals_out=globals_out)


def review_archive(path: Path, *, max_bytes: int) -> ReviewResult:
    """Structural + pickle-safety review of an uploaded zip archive."""
    try:
        size = path.stat().st_size
    except OSError:
        return ReviewResult(False, "无法读取上传文件", {})
    if size <= 0:
        return ReviewResult(False, "上传文件为空", {})
    if size > max_bytes:
        return ReviewResult(False, f"文件超过大小限制（{max_bytes // (1024 * 1024)}MB）", {})
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        return ReviewResult(False, "不是有效的 zip 压缩包", {})
    with zf:
        names = zf.namelist()
        if not names:
            return ReviewResult(False, "压缩包为空", {})
        for name in names:
            lowered = name.lower()
            if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
                return ReviewResult(False, f"压缩包包含非法路径：{name}", {})
            suffix = "." + lowered.rsplit(".", 1)[-1] if "." in lowered else ""
            if suffix in _BANNED_EXTENSIONS:
                return ReviewResult(False, f"压缩包包含可执行/脚本文件：{name}", {})
        pth_members = [n for n in names if n.lower().endswith(".pth")]
        index_members = [n for n in names if n.lower().endswith(".index")]
        if not pth_members:
            return ReviewResult(False, "压缩包里没有 .pth 模型文件（RVC 音色需要 .pth）", {})
        globals_out: set[str] = set()
        for member in pth_members:
            try:
                with zf.open(member) as handle:
                    payload = handle.read(_PICKLE_SCAN_LIMIT)
            except (OSError, KeyError):
                continue
            reason = _scan_pth_payload(payload, globals_out)
            if reason:
                return ReviewResult(False, f"{member}: {reason}", {})
        summary: dict[str, Any] = {
            "size_mb": round(size / (1024 * 1024), 2),
            "pth_count": len(pth_members),
            "index_count": len(index_members),
            "files": [
                {"name": name.rsplit("/", 1)[-1], "size": info.file_size}
                for name, info in zip(names, zf.infolist(), strict=True)
            ][:_MAX_FILES_IN_SUMMARY],
            "pickle_globals": sorted(globals_out)[:_MAX_GLOBALS_IN_SUMMARY],
        }
        return ReviewResult(True, "结构校验与 pickle 安全检查通过", summary)


def _review_prompt(summary: dict[str, Any]) -> list[dict[str, str]]:
    payload = json.dumps(summary, ensure_ascii=False)[:4000]
    return [
        {
            "role": "system",
            "content": (
                "你是 RVC 音色提交的自动审核员。输入是用户提交模型的摘要 JSON"
                "（显示名、FlowBee 用户名、来源、文件清单、pickle 全局符号）。"
                "判定规则：正常 RVC 模型（torch/collections/numpy 等重建符号）应通过；"
                "出现广告/联系方式/URL 等垃圾显示名、可疑 pickle 符号、或结构明显不是"
                ' RVC 权重时拒绝。只输出 JSON：{"decision": "approve" 或 "reject", '
                '"reason": "一句话中文理由"}'
            ),
        },
        {"role": "user", "content": payload},
    ]


def llm_review(summary: dict[str, Any]) -> tuple[bool | None, str]:
    """Ask an external LLM to review the submission summary.

    Returns ``(None, reason)`` when the LLM is not configured or fails, in
    which case the caller falls back to the rules verdict.
    """
    base = os.environ.get(REVIEW_BASE_ENV, "").strip()
    key = os.environ.get(REVIEW_KEY_ENV, "").strip()
    model = os.environ.get(REVIEW_MODEL_ENV, "").strip() or DEFAULT_REVIEW_MODEL
    if not base or not key:
        return None, "LLM 审核未配置（已按规则结果处理）"
    try:
        response = httpx.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": _review_prompt(summary), "temperature": 0},
            timeout=20.0,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        text = content if isinstance(content, str) else str(content)
        match = re.search(r"\{.*\}", text, re.S)
        data = json.loads(match.group(0) if match else text)
        decision = str(data.get("decision") or "").strip().lower()
        reason = str(data.get("reason") or "").strip()[:200] or "AI 审核未给出理由"
        if decision in {"approve", "approved"}:
            return True, f"AI 审核通过：{reason}"
        if decision in {"reject", "rejected"}:
            return False, f"AI 审核拒绝：{reason}"
        return None, "AI 审核结论不明确（已按规则结果处理）"
    except Exception as exc:  # noqa: BLE001 - never block submissions on review outages
        logger.warning("llm review failed: %s", exc)
        return None, "AI 审核暂不可用（已按规则结果处理）"


def new_request_id() -> str:
    return f"vr-{secrets.token_hex(6)}"


def build_request_record(
    *,
    request_id: str,
    name: str,
    label: str,
    url: str,
    flowbee_user: str,
    source: str,
    approved: bool,
    reason: str,
    flowbee_uid: int = 0,
) -> dict[str, Any]:
    return {
        "id": request_id,
        "name": name,
        "label": label,
        "url": url,
        "flowbee_user": flowbee_user,
        "flowbee_uid": flowbee_uid,
        "source": source,
        "status": "approved" if approved else "rejected",
        "reason": reason,
        "created_at": int(time.time()),
    }


def hf_upload(local_path: Path, *, repo_id: str, path_in_repo: str, token: str) -> str:
    """Upload a model archive to Hugging Face and return its resolve URL.

    ``huggingface_hub`` is an optional runtime dependency: install it with
    ``pip install huggingface_hub`` to enable HF storage mode.
    """
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:  # pragma: no cover - depends on deployment
        raise RuntimeError(
            "huggingface_hub 未安装，无法使用 HF 存储模式（pip install huggingface_hub）"
        ) from exc
    api = HfApi(token=token)
    api.upload_file(
        path_or_fileobj=str(local_path),
        path_in_repo=path_in_repo,
        repo_id=repo_id,
        commit_message=f"voice upload: {path_in_repo}",
    )
    return f"https://huggingface.co/{repo_id}/resolve/main/{path_in_repo}"


def modelscope_upload(local_path: Path, *, repo_id: str, path_in_repo: str, token: str) -> str:
    """Upload a model archive to ModelScope and return its resolve URL.

    ``modelscope`` is an optional runtime dependency: install it with
    ``pip install modelscope`` to enable ModelScope storage mode.
    """
    try:
        from modelscope.hub.api import HubApi
    except ImportError as exc:  # pragma: no cover - depends on deployment
        raise RuntimeError(
            "modelscope 未安装，无法使用 ModelScope 存储模式（pip install modelscope）"
        ) from exc
    api = HubApi()
    api.login(token)
    api.upload_file(
        path_or_fileobj=str(local_path),
        path_in_repo=path_in_repo,
        repo_id=repo_id,
    )
    return f"https://modelscope.cn/models/{repo_id}/resolve/master/{path_in_repo}"


def list_requests(path: Path | None = None) -> list[dict[str, Any]]:
    return [record for record in _load_requests(path).values() if isinstance(record, dict)]


def mark_deleted(request_id: str, path: Path | None = None) -> dict[str, Any] | None:
    target = path or requests_path()
    records = _load_requests(target)
    record = records.get(request_id)
    if not isinstance(record, dict):
        return None
    record["status"] = "deleted"
    record["deleted_at"] = int(time.time())
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(target)
    return record


def modelscope_delete(*, repo_id: str, token: str, path_in_repo: str) -> None:
    try:
        from modelscope.hub.api import HubApi
    except ImportError as exc:  # pragma: no cover - depends on deployment
        raise RuntimeError("modelscope 未安装，无法删除 ModelScope 文件") from exc
    api = HubApi()
    api.login(token)
    api.delete_files(repo_id=repo_id, file_paths=[path_in_repo])


def hf_delete(*, repo_id: str, token: str, path_in_repo: str) -> None:
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:  # pragma: no cover - depends on deployment
        raise RuntimeError("huggingface_hub 未安装，无法删除 HF 文件") from exc
    HfApi(token=token).delete_file(path_in_repo=path_in_repo, repo_id=repo_id)
