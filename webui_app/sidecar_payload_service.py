from __future__ import annotations

import json
import re


def strip_json_fence(text: str) -> str:
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def coerce_sidecar_string_list(value) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                decoded = json.loads(text)
            except Exception:
                decoded = None
            if isinstance(decoded, list):
                items = decoded
            else:
                items = [part.strip() for part in re.split(r"(?:\r?\n|;\s*)", text) if part.strip()]
        else:
            items = [part.strip() for part in re.split(r"(?:\r?\n|;\s*)", text) if part.strip()]
            if not items:
                items = [text]
    else:
        return []
    normalized = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        text = re.sub(r"^[\-\*\u2022]+\s*", "", text)
        text = re.sub(r"^\d+[\.\)]\s*", "", text)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def find_json_object_candidates(text: str, *, strip_json_fence_fn) -> list[str]:
    raw = str(text or "")
    candidates = []
    seen = set()

    def add(candidate: str):
        snippet = str(candidate or "").strip()
        if not snippet or snippet in seen:
            return
        seen.add(snippet)
        candidates.append(snippet)

    add(strip_json_fence_fn(raw))
    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE):
        add(match.group(1))

    depth = 0
    start = None
    in_string = False
    escape = False
    for idx, ch in enumerate(raw):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                add(raw[start:idx + 1])
                start = None

    candidates.sort(key=len, reverse=True)
    return candidates


def looks_like_sidecar_payload(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    keys = set(payload.keys())
    return bool({"overall_summary", "images", "follow_up_hints", "visible_text", "details"} & keys)


def extract_sidecar_json_payload(raw_text: str, *, find_json_object_candidates_fn, looks_like_sidecar_payload_fn) -> dict | None:
    for candidate in find_json_object_candidates_fn(raw_text):
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if looks_like_sidecar_payload_fn(payload):
            return payload
        if isinstance(payload, dict):
            for value in payload.values():
                if looks_like_sidecar_payload_fn(value):
                    return value
            return payload
    return None


def parse_sidecar_payload(raw_text: str, image_labels: list[str], *, extract_sidecar_json_payload_fn, coerce_sidecar_string_list_fn) -> dict:
    raw_text = str(raw_text or "").strip()
    fallback = {
        "overall_summary": raw_text,
        "images": [],
        "follow_up_hints": [],
        "raw_text": raw_text,
    }
    if not raw_text:
        return fallback
    payload = extract_sidecar_json_payload_fn(raw_text)
    if not payload:
        return fallback
    images = payload.get("images")
    normalized_images = []
    overall_summary = str(payload.get("overall_summary") or "").strip()
    if isinstance(images, list):
        for idx, item in enumerate(images):
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            if not label:
                label = image_labels[idx] if idx < len(image_labels) else f"Image {idx + 1}"
            summary = str(item.get("summary") or "").strip()
            if not summary:
                summary = overall_summary
            normalized_images.append({
                "label": label,
                "summary": summary,
                "visible_text": coerce_sidecar_string_list_fn(item.get("visible_text")),
                "details": coerce_sidecar_string_list_fn(item.get("details")),
                "follow_up_hints": coerce_sidecar_string_list_fn(item.get("follow_up_hints")),
            })
    if not overall_summary and normalized_images:
        overall_summary = normalized_images[0].get("summary") or ""
    if image_labels and not normalized_images:
        top_level_visible_text = coerce_sidecar_string_list_fn(payload.get("visible_text"))
        top_level_details = coerce_sidecar_string_list_fn(payload.get("details"))
        top_level_hints = coerce_sidecar_string_list_fn(payload.get("follow_up_hints"))
        for idx, label in enumerate(image_labels):
            normalized_images.append({
                "label": label,
                "summary": overall_summary if idx == 0 else "",
                "visible_text": top_level_visible_text if idx == 0 else [],
                "details": top_level_details if idx == 0 else [],
                "follow_up_hints": top_level_hints if idx == 0 else [],
            })
    return {
        "overall_summary": overall_summary,
        "images": normalized_images,
        "follow_up_hints": coerce_sidecar_string_list_fn(payload.get("follow_up_hints")),
        "raw_text": raw_text,
    }


def format_sidecar_context_block(sidecar_result: dict) -> str:
    if not sidecar_result:
        return ""
    lines = ["Vision sidecar analysis:"]
    if sidecar_result.get("reanalysis"):
        lines.append("This analysis refreshes an earlier screenshot because the user referred back to it.")
    overall_summary = str(sidecar_result.get("overall_summary") or "").strip()
    if overall_summary:
        lines.append(f"Overall summary: {overall_summary}")
    image_results = sidecar_result.get("images") or []
    for idx, item in enumerate(image_results, start=1):
        label = str(item.get("label") or f"Image {idx}").strip()
        asset_id = str(item.get("asset_id") or "").strip()
        lines.append(f"{label}{f' [{asset_id}]' if asset_id else ''}:")
        summary = str(item.get("summary") or "").strip()
        if summary:
            lines.append(f"- Summary: {summary}")
        visible_text = [str(value).strip() for value in (item.get("visible_text") or []) if str(value).strip()]
        if visible_text:
            lines.append("- Visible text:")
            lines.extend(f"  - {value}" for value in visible_text)
        details = [str(value).strip() for value in (item.get("details") or []) if str(value).strip()]
        if details:
            lines.append("- Details:")
            lines.extend(f"  - {value}" for value in details)
        follow_up_hints = [str(value).strip() for value in (item.get("follow_up_hints") or []) if str(value).strip()]
        if follow_up_hints:
            lines.append("- Follow-up hints:")
            lines.extend(f"  - {value}" for value in follow_up_hints)
    return "\n".join(lines)