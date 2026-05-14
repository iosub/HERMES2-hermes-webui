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


def folders_from_file(*, chat_folders_path, normalize_chat_folder):
    path = chat_folders_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    folders = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        entry = dict(value)
        entry.setdefault("id", key)
        normalized = normalize_chat_folder(entry)
        if normalized["id"]:
            folders[normalized["id"]] = normalized
    return folders


def write_all_folders(*, folders, normalize_chat_folder, chat_folders_path, chat_data_lock_fn, chat_folders):
    serializable = {}
    for folder_id, folder in (folders or {}).items():
        normalized = normalize_chat_folder(folder)
        if normalized["id"]:
            serializable[folder_id] = normalized
    payload = json.dumps(serializable, ensure_ascii=False, indent=2)
    path = chat_folders_path()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with chat_data_lock_fn():
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, path)
    chat_folders.clear()
    chat_folders.update(copy.deepcopy(serializable))
    return copy.deepcopy(serializable)


def load_all_folders(*, chat_data_lock_fn, folders_from_file_fn, chat_folders):
    with chat_data_lock_fn(shared=True):
        folders = folders_from_file_fn()
    chat_folders.clear()
    chat_folders.update(copy.deepcopy(folders))
    return copy.deepcopy(folders)


def load_folder(folder_id, *, load_all_folders_fn):
    normalized_folder_id = str(folder_id or "").strip()
    if not normalized_folder_id:
        return None
    folders = load_all_folders_fn()
    return folders.get(normalized_folder_id)


def write_folder(folder, *, normalize_chat_folder, load_all_folders_fn, write_all_folders_fn):
    normalized = normalize_chat_folder(folder)
    folders = load_all_folders_fn()
    folders[normalized["id"]] = normalized
    return write_all_folders_fn(folders)[normalized["id"]]


def delete_folder(folder_id, *, load_all_folders_fn, write_all_folders_fn):
    normalized_folder_id = str(folder_id or "").strip()
    if not normalized_folder_id:
        return
    folders = load_all_folders_fn()
    if normalized_folder_id in folders:
        folders.pop(normalized_folder_id, None)
        write_all_folders_fn(folders)


def request_control_path(request_id: str, *, chat_request_dir):
    return chat_request_dir() / f"{secure_filename(request_id)}.json"


def read_request_control(request_id: str, *, request_control_path_fn, logger):
    path = request_control_path_fn(request_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read chat request control file %s: %s", path.name, exc)
        return None


def write_request_control(request_id: str, payload: dict, *, request_control_path_fn):
    path = request_control_path_fn(request_id)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def request_output_path(request_id: str, *, chat_request_dir):
    return chat_request_dir() / f"{secure_filename(request_id)}.log"


def remove_chat_request(request_id: str, *, request_control_path_fn):
    path = request_control_path_fn(request_id)
    if path.exists():
        path.unlink()