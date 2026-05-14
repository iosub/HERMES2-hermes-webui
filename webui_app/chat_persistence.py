from __future__ import annotations

import copy
import json
import os
from contextlib import contextmanager

from werkzeug.utils import secure_filename


@contextmanager
def chat_data_lock(*, lock_path, shared: bool = False):
    try:
        import fcntl
    except ImportError:
        yield
        return
    path = lock_path()
    path.touch(exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def chat_session_path(session_id: str, *, chat_data_dir):
    return chat_data_dir() / f"{secure_filename(session_id)}.json"


def session_from_file(path, *, normalize_chat_session):
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and data.get("id"):
        return normalize_chat_session(data)
    raise ValueError(f"Invalid session payload in {path.name}")


def load_all_sessions(
    *,
    chat_data_lock_fn,
    chat_data_dir,
    chat_folders_path,
    session_from_file_fn,
    chat_sessions,
    logger,
):
    loaded_sessions = {}
    with chat_data_lock_fn(shared=True):
        for file_path in sorted(chat_data_dir().glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True):
            if file_path == chat_folders_path():
                continue
            try:
                data = session_from_file_fn(file_path)
                loaded_sessions[data["id"]] = data
            except Exception as exc:
                logger.warning("Failed to load session file %s: %s", file_path.name, exc)
    chat_sessions.clear()
    chat_sessions.update(loaded_sessions)
    return copy.deepcopy(loaded_sessions)


def load_session(
    session_id,
    *,
    chat_session_path_fn,
    chat_data_lock_fn,
    session_from_file_fn,
    chat_sessions,
    logger,
):
    path = chat_session_path_fn(session_id)
    with chat_data_lock_fn(shared=True):
        if not path.exists():
            chat_sessions.pop(session_id, None)
            return None
        try:
            data = session_from_file_fn(path)
        except Exception as exc:
            logger.warning("Failed to load session file %s: %s", path.name, exc)
            chat_sessions.pop(session_id, None)
            return None
    chat_sessions[session_id] = data
    return copy.deepcopy(data)


def write_session(
    session,
    *,
    normalize_chat_session,
    chat_session_path_fn,
    chat_data_lock_fn,
    chat_sessions,
):
    normalized = normalize_chat_session(session)
    session_id = normalized["id"]
    path = chat_session_path_fn(session_id)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(normalized, ensure_ascii=False, indent=2)
    with chat_data_lock_fn():
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, path)
    chat_sessions[session_id] = copy.deepcopy(normalized)


def delete_session_from_disk(session_id, *, chat_sessions, chat_session_path_fn, chat_data_lock_fn):
    chat_sessions.pop(session_id, None)
    path = chat_session_path_fn(session_id)
    with chat_data_lock_fn():
        if path.exists():
            path.unlink()