from __future__ import annotations

import json
import re
from pathlib import Path


def find_updated_hermes_native_session(
    before: dict[str, tuple[int, int]] | None,
    hermes_session_id: str | None = None,
    *,
    clean_hermes_session_id_fn,
    hermes_native_session_file_candidates_fn,
) -> Path | None:
    before = before or {}
    session_id = clean_hermes_session_id_fn(hermes_session_id)
    changed = []
    preferred = []
    for path in hermes_native_session_file_candidates_fn(hermes_session_id):
        try:
            stat = path.stat()
        except OSError:
            continue
        current = (stat.st_mtime_ns, stat.st_size)
        previous = before.get(str(path))
        if previous != current:
            changed.append((current[0], path))
        if session_id and path.name == f"session_{session_id}.json":
            preferred.append((current[0], path))
    pool = preferred or changed
    if not pool:
        return None
    pool.sort(key=lambda item: item[0], reverse=True)
    return pool[0][1]


def load_hermes_native_session_reply(path: Path, *, clean_hermes_session_id_fn) -> tuple[str | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    hermes_session_id = clean_hermes_session_id_fn(data.get("session_id") or path.stem.removeprefix("session_"))
    messages = data.get("messages")
    if not isinstance(messages, list):
        return None, hermes_session_id
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "assistant":
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip(), hermes_session_id
    return None, hermes_session_id


def extract_cli_reply_after_session_marker(output: str) -> str:
    matches = list(re.finditer(r"(?mi)^session_id:\s*\S+\s*$", output))
    if not matches:
        return ""
    tail = output[matches[-1].end():]
    tail = re.sub(r"(?mi)^Resume this session with:\s*$", "", tail)
    tail = re.sub(r"(?mi)^\s*hermes\s+--resume\s+\S+\s*$", "", tail)
    tail = re.sub(r"(?mi)^Session:\s*\S+\s*$", "", tail)
    tail = re.sub(r"(?mi)^Duration:\s*.*$", "", tail)
    tail = re.sub(r"(?mi)^Messages:\s*.*$", "", tail)
    return tail.strip()


def clean_cli_output(output: str, *, extract_cli_reply_after_session_marker_fn) -> str:
    lines = output.split('\n')

    in_response_box = False
    response_lines = []
    latest_response = ""
    for line in lines:
        if re.match(r'^\s*╭.*Hermes.*╮\s*$', line):
            in_response_box = True
            response_lines = []
            continue
        if in_response_box and re.match(r'^\s*╰.*╯\s*$', line):
            normalized_lines = [part.strip() if part.strip() else '' for part in response_lines]
            while normalized_lines and not normalized_lines[0]:
                normalized_lines.pop(0)
            while normalized_lines and not normalized_lines[-1]:
                normalized_lines.pop()
            response = '\n'.join(normalized_lines).strip()
            if response:
                latest_response = response
            in_response_box = False
            continue
        if in_response_box:
            response_lines.append(line)

    if latest_response:
        return latest_response

    quiet_response = extract_cli_reply_after_session_marker_fn(output)
    if quiet_response:
        return quiet_response

    output = re.sub(r'<think>.*?</think>', '', output, flags=re.DOTALL)
    lines = output.split('\n')
    clean = []
    skip = False
    for line in lines:
        if 'Hermes Agent v' in line or 'Available Tools' in line or 'Available Skills' in line:
            skip = True
            continue
        if 'Query:' in line:
            skip = False
            continue
        if 'Resume this session' in line or line.strip().startswith('hermes --resume'):
            skip = True
            continue
        if skip:
            continue
        if re.match(r'^[\u2500-\u257f\u2550-\u256f\u2800-\u28ff\u2580-\u259f\u2591-\u2593\u2b1b\u2b1c ]+$', line.strip()):
            continue
        stripped = line.strip()
        if any(c in stripped for c in '\u2500\u2501\u2502\u2503\u2504\u2505\u2506\u2507\u2508\u2509\u250a\u250b\u250c\u250d\u250e\u250f\u2510\u2511\u2512\u2513\u2514\u2515\u2516\u2517\u2518\u2519\u251a\u251b\u251c\u251d\u251e\u251f\u2520\u2521\u2522\u2523\u2524\u2525\u2526\u2527\u2528\u2529\u252a\u252b\u252c\u252d\u252e\u252f\u2530\u2531\u2532\u2533\u2534\u2535\u2536\u2537\u2538\u2539\u253a\u253b\u253c\u253d\u253e\u253f\u2540\u2541\u2542\u2543\u2544\u2545\u2546\u2547\u2548\u2549\u254a\u254b\u254c\u254d\u254e\u254f\u2550\u2551\u2552\u2553\u2554\u2555\u2556\u2557\u2558\u2559\u255a\u255b\u255c\u255d\u255e\u255f\u2560\u2561\u2562\u2563\u2564\u2565\u2566\u2567\u2568\u2569\u256a\u256b\u256c\u256d\u256e\u256f\u2570\u2571\u2572\u2573\u2574\u2575\u2576\u2577\u2578\u2579\u257a\u257b\u257c\u257d\u257e\u257f'):
            box_char_count = sum(1 for c in stripped if ord(c) in range(0x2500, 0x2580) or ord(c) in range(0x2550, 0x2570))
            if box_char_count > len(stripped) * 0.6:
                continue
        if re.match(r'^\s*(Session|Duration|Messages):', line):
            continue
        braille = sum(1 for c in line if '\u2800' <= c <= '\u28ff')
        if braille > len(line) * 0.4:
            continue
        stripped = line.strip()
        if stripped:
            if '\u2502' in stripped:
                parts = stripped.split('\u2502')
                content = [p.strip() for p in parts if p.strip() and not re.match(r'^[\u2800-\u28ff\s]+$', p.strip())]
                if content:
                    text = ' '.join(content)
                    if any(s in text for s in ['Available Tools', 'Available Skills', '/help', 'hermes --resume', 'Session:', 'Duration:', 'Messages:', 'Hermes Agent v', 'Nous Research', '/home/']):
                        continue
                    clean.append(text)
            else:
                clean.append(stripped)
    result = '\n'.join(l for l in clean if len(l) > 1)
    return result or '(Empty response)'


def parse_hermes_chat_result(output: str, *, clean_cli_output_fn) -> tuple[str, str | None]:
    session_match = re.search(r"(?mi)^session_id:\s*(\S+)\s*$", output)
    resume_match = re.search(r"(?mi)^\s*hermes\s+--resume\s+(\S+)\s*$", output)
    summary_match = re.search(r"(?mi)^Session:\s*(\S+)\s*$", output)
    hermes_session_id = None
    for match in (session_match, resume_match, summary_match):
        if match and match.group(1):
            hermes_session_id = match.group(1)
            break
    cleaned = output
    cleaned = re.sub(r"(?mi)^Resume this session with:\s*$", "", cleaned)
    cleaned = re.sub(r"(?mi)^\s*hermes\s+--resume\s+\S+\s*$", "", cleaned)
    cleaned = re.sub(r"(?m)^↻ Resumed session .*$", "", cleaned)
    cleaned = re.sub(r"(?m)^Resumed session .*$", "", cleaned)
    return clean_cli_output_fn(cleaned), hermes_session_id