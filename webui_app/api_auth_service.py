from __future__ import annotations


def provider_env_api_key(provider: str | None, *, normalize_provider_type_fn, provider_env_key_map, runtime_env_value_fn) -> str:
    provider_name = normalize_provider_type_fn(provider or "")
    env_key = provider_env_key_map.get(provider_name)
    return runtime_env_value_fn(env_key, "") if env_key else ""


def resolved_target_api_key(
    target: dict | None,
    *,
    resolve_runtime_template_fn,
    provider_env_api_key_fn,
    api_url_port_fn,
    effective_hermes_api_url_fn,
    default_hermes_api_url: str,
    repo_env_values_fn,
    api_token_repo_keys_for_port_fn,
    os_environ,
) -> str:
    target = target or {}
    explicit_api_key = resolve_runtime_template_fn((target.get("api_key") or "").strip()).strip()
    if explicit_api_key:
        return explicit_api_key
    provider_api_key = provider_env_api_key_fn(target.get("provider"))
    if provider_api_key:
        return provider_api_key
    repo_env = repo_env_values_fn()
    gateway_port = api_url_port_fn(effective_hermes_api_url_fn(default_hermes_api_url))
    target_port = api_url_port_fn(target.get("base_url"))
    candidate_ports = []
    for port in (gateway_port, target_port):
        normalized = str(port or "").strip()
        if normalized and normalized not in candidate_ports:
            candidate_ports.append(normalized)
    repo_token = ""
    for port in candidate_ports:
        repo_token = next(
            (str(repo_env.get(key) or "").strip() for key in api_token_repo_keys_for_port_fn(port) if str(repo_env.get(key) or "").strip()),
            "",
        )
        if repo_token:
            break
    return (
        str(os_environ.get("HERMES_API_KEY") or "").strip()
        or str(os_environ.get("HERMES_API_TOKEN") or "").strip()
        or str(os_environ.get("API_SERVER_KEY") or "").strip()
        or str(os_environ.get("API_SERVER_TOKEN") or "").strip()
        or repo_token
    )


def api_server_headers(
    api_key: str | None = None,
    provider: str | None = None,
    target: dict | None = None,
    *,
    provider_env_api_key_fn,
    resolved_target_api_key_fn,
) -> dict:
    headers = {}
    resolved_api_key = (api_key or "").strip() if api_key is not None else ""
    if not resolved_api_key and provider:
        resolved_api_key = provider_env_api_key_fn(provider)
    if not resolved_api_key:
        resolved_api_key = resolved_target_api_key_fn(target)
    if resolved_api_key:
        headers["Authorization"] = f"Bearer {resolved_api_key}"
    return headers