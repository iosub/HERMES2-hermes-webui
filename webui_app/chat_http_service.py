from __future__ import annotations

import json
from pathlib import Path
import urllib.parse


def api_target_missing_credentials(target: dict | None, *, resolved_target_api_key_fn) -> bool:
    target = target or {}
    base_url = str(target.get("base_url") or "").strip()
    if not base_url:
        return False
    parsed = urllib.parse.urlparse(base_url)
    hostname = (parsed.hostname or "").strip().lower()
    if hostname in {"", "localhost", "127.0.0.1", "::1"}:
        return False
    return not bool(resolved_target_api_key_fn(target))


def build_openai_api_url(base_url: str, path: str) -> str:
    base = (base_url or "").rstrip("/")
    clean_path = path.lstrip("/")
    if base.endswith("/v1"):
        return f"{base}/{clean_path}"
    return f"{base}/v1/{clean_path}"


def openrouter_model_supports_images(
    target: dict,
    timeout: int = 3,
    *,
    build_openai_api_url_fn,
    api_server_headers_fn,
) -> tuple[bool | None, str]:
    import urllib.request

    if (target.get("provider") or "").strip().lower() != "openrouter":
        return None, ""
    model_id = (target.get("model") or "").strip()
    if not model_id:
        return None, ""

    req = urllib.request.Request(
        build_openai_api_url_fn(target.get("base_url") or "", "models"),
        headers=api_server_headers_fn(target.get("api_key"), target.get("provider")),
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:
        return None, f"Could not verify image support for {model_id}: {exc}"

    for item in payload.get("data", []) or []:
        if item.get("id") != model_id:
            continue
        architecture = item.get("architecture") or {}
        input_modalities = architecture.get("input_modalities") or []
        if "image" in input_modalities:
            return True, ""
        modality = str(architecture.get("modality") or "")
        if "image" in modality.lower():
            return True, ""
        return False, f"The configured OpenRouter model {model_id} does not advertise image input support"

    return None, f"Could not find model metadata for {model_id} on OpenRouter"


def openrouter_fetch_json(path: str, timeout: int = 10, *, build_openai_api_url_fn) -> dict:
    import urllib.request

    req = urllib.request.Request(
        build_openai_api_url_fn("https://openrouter.ai/api/v1", path),
        headers={"User-Agent": "hermes-web-ui"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def openrouter_discovery_models(*, vision_only: bool = False, timeout: int = 10, openrouter_fetch_json_fn) -> list[dict]:
    payload = openrouter_fetch_json_fn("models", timeout=timeout)
    models = []
    for item in payload.get("data", []) or []:
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        architecture = item.get("architecture") or {}
        input_modalities = architecture.get("input_modalities") or []
        modality = str(architecture.get("modality") or "")
        supports_image = "image" in input_modalities or "image" in modality.lower()
        if vision_only and not supports_image:
            continue
        models.append({
            "id": model_id,
            "name": str(item.get("name") or model_id).strip(),
            "description": str(item.get("description") or "").strip(),
            "supports_image": supports_image,
            "input_modalities": input_modalities,
        })
    return sorted(models, key=lambda item: item.get("id", ""))


def openrouter_discovery_endpoints(model_id: str, timeout: int = 10, *, openrouter_fetch_json_fn) -> list[dict]:
    encoded_model = urllib.parse.quote(str(model_id or "").strip(), safe="/")
    payload = openrouter_fetch_json_fn(f"models/{encoded_model}/endpoints", timeout=timeout)
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    endpoints = data.get("endpoints", []) if isinstance(data, dict) else []
    normalized = []
    for endpoint in endpoints or []:
        normalized.append({
            "provider_name": str(endpoint.get("provider_name") or endpoint.get("name") or "").strip(),
            "tag": str(endpoint.get("tag") or "").strip(),
            "status": endpoint.get("status"),
            "uptime_last_30m": endpoint.get("uptime_last_30m"),
            "context_length": endpoint.get("context_length"),
        })
    return normalized


def summarize_upstream_error_detail(raw_body: str, fallback: str = "") -> str:
    detail = (raw_body or "").strip()
    if not detail:
        return fallback or "upstream request failed"
    try:
        payload = json.loads(detail)
    except Exception:
        return detail

    if isinstance(payload, dict):
        error_payload = payload.get("error", payload)
        if isinstance(error_payload, dict):
            metadata = error_payload.get("metadata") if isinstance(error_payload.get("metadata"), dict) else {}
            metadata_raw = str(metadata.get("raw") or "").strip()
            message = str(error_payload.get("message") or "").strip()
            code = str(error_payload.get("code") or "").strip()
            if metadata_raw:
                return metadata_raw
            if message and code and message.lower() != code.lower():
                return f"{message} ({code})"
            if message:
                return message
            if code:
                return code
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return detail


def estimate_base64_decoded_size(payload: str) -> int:
    cleaned = "".join(str(payload).split())
    if not cleaned:
        return 0
    if len(cleaned) % 4 != 0:
        raise ValueError("Invalid base64 length")
    padding = len(cleaned) - len(cleaned.rstrip("="))
    return (len(cleaned) * 3) // 4 - padding


def save_upload_stream(
    file_storage,
    destination: Path,
    *,
    upload_stream_chunk_size: int,
    max_upload_size: int,
    request_entity_too_large,
) -> int:
    temp_path = destination.with_name(f".{destination.name}.part")
    total = 0
    stream = getattr(file_storage, "stream", file_storage)
    if hasattr(stream, "seek"):
        stream.seek(0)
    try:
        with temp_path.open("wb") as handle:
            while True:
                chunk = stream.read(upload_stream_chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_upload_size:
                    raise request_entity_too_large()
                handle.write(chunk)
        temp_path.replace(destination)
        return total
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def api_server_probe(
    timeout: int = 3,
    prefer_vision: bool = False,
    *,
    resolve_api_target_fn,
    api_server_headers_fn,
    build_openai_api_url_fn,
) -> tuple[bool, str, dict | None]:
    import urllib.error
    import urllib.request

    target = resolve_api_target_fn(prefer_vision=prefer_vision)
    base_url = (target.get("base_url") or "").strip()
    if not base_url:
        return False, "API base URL is not configured", None

    headers = api_server_headers_fn(target.get("api_key"), target.get("provider"))
    probes = [
        ("health", f"{base_url.rstrip('/')}/health"),
        ("models", build_openai_api_url_fn(base_url, "models")),
    ]
    last_error = "API endpoint did not respond"

    for probe_name, url in probes:
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= resp.status < 300:
                    return True, "", {"probe": probe_name, "url": url, "status": resp.status}
                last_error = f"{probe_name} probe returned HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            if probe_name == "health" and exc.code == 404:
                last_error = "health probe returned HTTP 404"
                continue
            last_error = f"{probe_name} probe returned HTTP {exc.code}"
        except Exception as exc:
            last_error = f"{probe_name} probe failed: {exc}"

    return False, last_error, None


def vision_configured(*, normalized_model_config_fn) -> tuple[bool, str]:
    model_cfg = normalized_model_config_fn()
    vision_cfg = model_cfg.get("vision")
    if isinstance(vision_cfg, str):
        if vision_cfg.strip():
            return True, ""
    elif isinstance(vision_cfg, dict):
        provider = (vision_cfg.get("provider") or "").strip()
        model = (vision_cfg.get("model") or "").strip()
        base_url = (vision_cfg.get("base_url") or "").strip()
        if model or base_url or (provider and provider.lower() != "auto"):
            return True, ""
    return False, "Hermes vision is not configured"


def image_attachment_support_status(
    *,
    vision_configured_fn,
    resolve_api_target_fn,
    api_target_missing_credentials_fn,
    effective_hermes_api_url_fn,
    default_hermes_api_url: str,
    api_server_probe_fn,
    openrouter_model_supports_images_fn,
) -> tuple[bool, str]:
    vision_ready, vision_reason = vision_configured_fn()
    if not vision_ready:
        return False, vision_reason
    target = resolve_api_target_fn(prefer_vision=True)
    if api_target_missing_credentials_fn(target):
        api_url = target.get("base_url") or effective_hermes_api_url_fn(default_hermes_api_url)
        return False, f"Vision API key is missing for remote endpoint {api_url}"
    api_ok, api_reason, _ = api_server_probe_fn(timeout=2, prefer_vision=True)
    if api_ok:
        image_model_ok, image_model_reason = openrouter_model_supports_images_fn(target, timeout=3)
        if image_model_ok is False:
            return False, image_model_reason
        return True, ""
    api_url = target.get("base_url") or effective_hermes_api_url_fn(default_hermes_api_url)
    return False, f"OpenAI-compatible vision sidecar API is not reachable at {api_url} ({api_reason})"