"""Tools for law report agent with backend task-status APIs."""

import asyncio
from collections.abc import AsyncGenerator
import json
import os
import re
import time

import httpx
from langchain_core.tools import tool
from utils import bind_tool_event_source as _bind_tool_event_source
from utils import emit_tool_event as _emit_tool_event

_ALLOWED_ROLES = {"system", "user", "assistant", "function", "tool"}
_TASK_ID_CONTENT_PATTERN = re.compile(r"\[TASK_ID\]([A-Za-z0-9._:-]+)")


def _normalize_messages(messages_json: str) -> list[dict]:
    try:
        payload = json.loads(messages_json)
    except json.JSONDecodeError as error:
        raise ValueError(f"messages_json is not valid JSON: {error}") from error

    if not isinstance(payload, list) or len(payload) == 0:
        raise ValueError("messages_json must be a non-empty JSON array")

    normalized: list[dict] = []
    for idx, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"messages_json[{idx}] must be an object")
        role = item.get("role")
        if role not in _ALLOWED_ROLES:
            raise ValueError(f"messages_json[{idx}].role is invalid: {role}")
        content = item.get("content")
        if content is None:
            content = ""
        normalized.append({"role": role, "content": content, "name": item.get("name")})
    return normalized


def _safe_parse_scope(scope_json: str) -> list[str] | None:
    try:
        scope = json.loads(scope_json) if scope_json else None
    except json.JSONDecodeError as error:
        raise ValueError(f"scope_json is not valid JSON: {error}") from error

    if scope is None:
        return None
    if not isinstance(scope, list):
        raise ValueError("scope_json must decode to an array")
    return [str(item) for item in scope]


def _build_payload(
    *,
    messages_json: str,
    model: str,
    stream: bool,
    jurisdiction: str,
    country: str,
    industry: str,
    scope_json: str,
    temperature: float,
    max_tokens: int,
) -> dict[str, object]:
    messages = _normalize_messages(messages_json)
    scope = _safe_parse_scope(scope_json)

    payload: dict[str, object] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
    }
    if max_tokens > 0:
        payload["max_tokens"] = max_tokens
    if jurisdiction:
        payload["jurisdiction"] = jurisdiction
    if country:
        payload["country"] = country
    if industry:
        payload["industry"] = industry
    if scope is not None:
        payload["scope"] = scope
    return payload


def _api_config() -> tuple[str, str, float]:
    base_url = os.getenv("LAW_API_BASE_URL", "http://localhost:8000").rstrip("/")
    api_key = os.getenv("LAW_API_KEY", "")
    timeout_seconds = float(os.getenv("LAW_API_TIMEOUT", "1200"))
    return base_url, api_key, timeout_seconds


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _extract_content_from_openai_json(response_body: dict) -> str:
    content = ""
    choices = response_body.get("choices") if isinstance(response_body, dict) else None
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, dict):
            message = first_choice.get("message", {})
            if isinstance(message, dict):
                maybe_content = message.get("content")
                if isinstance(maybe_content, str):
                    content = maybe_content
    return content


def _extract_task_id(payload: object) -> str:
    """Best-effort extraction of task id from JSON payload."""
    if isinstance(payload, dict):
        for key in ("task_id", "taskId", "id", "research_id", "researchId"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            candidate = _extract_task_id(value)
            if candidate:
                return candidate
    elif isinstance(payload, list):
        for item in payload:
            candidate = _extract_task_id(item)
            if candidate:
                return candidate
    elif isinstance(payload, str):
        match = _TASK_ID_CONTENT_PATTERN.search(payload)
        if match:
            return match.group(1).strip()
    return ""


def _strip_task_id_control_text(content: str) -> str:
    if not content:
        return content
    return _TASK_ID_CONTENT_PATTERN.sub("", content).replace("Task created:", "").strip()


def _consume_stream_response(response: httpx.Response) -> tuple[str, list[dict]]:
    chunks: list[dict] = []
    content_parts: list[str] = []

    for line in response.iter_lines():
        if not line:
            continue

        text = line.decode("utf-8") if isinstance(line, bytes) else str(line)
        if not text.startswith("data:"):
            continue

        data = text[5:].strip()
        if data == "[DONE]":
            break

        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            chunk = {"raw": data}

        chunks.append(chunk)

        choices = chunk.get("choices") if isinstance(chunk, dict) else None
        if isinstance(choices, list) and choices:
            delta = choices[0].get("delta", {})
            if isinstance(delta, dict):
                piece = delta.get("content")
                if isinstance(piece, str) and piece:
                    cleaned_piece = _strip_task_id_control_text(piece)
                    if cleaned_piece:
                        content_parts.append(cleaned_piece)

    return "".join(content_parts), chunks


def _is_terminal_task_status(task_status: str) -> bool:
    value = (task_status or "").lower()
    return value in {"completed", "succeeded", "success", "failed", "error", "cancelled", "canceled", "done"}


def _extract_task_status(payload: dict) -> str:
    return str(payload.get("status") or payload.get("task_status") or payload.get("state") or "unknown")


def _fetch_task(task_id: str) -> dict:
    base_url, api_key, timeout_seconds = _api_config()
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.get(
            f"{base_url}/v1/tasks/{task_id}",
            headers=_headers(api_key),
        )
    response.raise_for_status()
    parsed = response.json()
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def _cancel_task(task_id: str) -> dict:
    base_url, api_key, timeout_seconds = _api_config()
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            f"{base_url}/v1/tasks/{task_id}/cancel",
            headers=_headers(api_key),
        )
    response.raise_for_status()
    if not response.content:
        return {}
    parsed = response.json()
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def _consume_stream_for_content_and_task(response: httpx.Response) -> tuple[str, list[dict], str]:
    """Read full SSE stream, collecting content chunks and first task_id if present."""
    chunks: list[dict] = []
    content_parts: list[str] = []
    task_id = ""

    chunk_index = 0
    for line in response.iter_lines():
        if not line:
            continue

        text = line.decode("utf-8") if isinstance(line, bytes) else str(line)
        if not text.startswith("data:"):
            continue

        data = text[5:].strip()
        if data == "[DONE]":
            break

        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            chunk = {"raw": data}

        chunks.append(chunk)
        chunk_index += 1

        if not task_id:
            task_id = _extract_task_id(chunk)
            if task_id:
                _emit_tool_event(
                    "law_task_id_detected",
                    content="Detected task id from stream.",
                    task_id=task_id,
                )

        choices = chunk.get("choices") if isinstance(chunk, dict) else None
        if isinstance(choices, list) and choices:
            delta = choices[0].get("delta", {})
            if isinstance(delta, dict):
                piece = delta.get("content")
                if isinstance(piece, str) and piece:
                    cleaned_piece = _strip_task_id_control_text(piece)
                    if cleaned_piece:
                        content_parts.append(cleaned_piece)
                        _emit_tool_event(
                            "law_stream_delta",
                            content=cleaned_piece,
                            task_id=task_id,
                            index=chunk_index,
                        )

    return "".join(content_parts), chunks, task_id


@tool(parse_docstring=True)
@_bind_tool_event_source
def law_report_api(
    messages_json: str,
    model: str = "legal-report-agent",
    stream: bool = False,
    jurisdiction: str = "",
    country: str = "",
    industry: str = "",
    scope_json: str = "[]",
    temperature: float = 0.7,
    max_tokens: int = 0,
) -> str:
    """Call legal-report API directly (blocking mode).

    Args:
        messages_json: JSON array string of OpenAI chat messages.
        model: Model name for /v1/chat/completions.
        stream: Whether to request streaming payload from server.
        jurisdiction: Optional legal jurisdiction.
        country: Optional country context.
        industry: Optional industry context.
        scope_json: Optional JSON string array for scope values.
        temperature: Sampling temperature.
        max_tokens: Optional token limit. Use 0 to omit this field.

    Returns:
        JSON string containing API result or structured error details.
    """
    try:
        payload = _build_payload(
            messages_json=messages_json,
            model=model,
            stream=stream,
            jurisdiction=jurisdiction,
            country=country,
            industry=industry,
            scope_json=scope_json,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        base_url, api_key, timeout_seconds = _api_config()
        with httpx.Client(timeout=timeout_seconds) as client:
            if stream:
                with client.stream(
                    "POST",
                    f"{base_url}/v1/chat/completions",
                    headers=_headers(api_key),
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    streamed_content, raw_chunks = _consume_stream_response(response)
                    response_body: dict[str, object] = {
                        "choices": [{"message": {"content": streamed_content}}],
                        "stream_chunks": raw_chunks,
                    }
            else:
                response = client.post(
                    f"{base_url}/v1/chat/completions",
                    headers=_headers(api_key),
                    json=payload,
                )
                response.raise_for_status()
                parsed = response.json()
                response_body = parsed if isinstance(parsed, dict) else {"raw": parsed}

        content = _extract_content_from_openai_json(response_body)

        return json.dumps(
            {
                "status": "ok",
                "endpoint": f"{base_url}/v1/chat/completions",
                "content": content,
                "raw": response_body,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as error:
        return json.dumps(
            {
                "status": "error",
                "tool": "law_report_api",
                "error_type": error.__class__.__name__,
                "message": str(error),
                "endpoint": f"{os.getenv('LAW_API_BASE_URL', 'http://localhost:8000').rstrip('/')}/v1/chat/completions",
            },
            ensure_ascii=False,
            indent=2,
        )


@tool(parse_docstring=True)
@_bind_tool_event_source
def start_law_report_task(
    messages_json: str,
    model: str = "legal-report-agent",
    jurisdiction: str = "",
    country: str = "",
    industry: str = "",
    scope_json: str = "[]",
    temperature: float = 0.7,
    max_tokens: int = 0,
) -> str:
    """Start a backend task and return task id for polling.

    Args:
        messages_json: JSON array string of OpenAI chat messages.
        model: Model name for /v1/chat/completions.
        jurisdiction: Optional legal jurisdiction.
        country: Optional country context.
        industry: Optional industry context.
        scope_json: Optional JSON string array for scope values.
        temperature: Sampling temperature.
        max_tokens: Optional token limit. Use 0 to omit this field.

    Returns:
        JSON string with task metadata and task_id for polling from /v1/tasks/{task_id}.
    """
    payload = _build_payload(
        messages_json=messages_json,
        model=model,
        stream=True,
        jurisdiction=jurisdiction,
        country=country,
        industry=industry,
        scope_json=scope_json,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    base_url, api_key, timeout_seconds = _api_config()
    handshake_timeout = float(os.getenv("LAW_STREAM_HANDSHAKE_TIMEOUT", "8"))
    started_at = time.time()
    first_chunks: list[dict] = []
    task_id = ""

    try:
        _emit_tool_event(
            "law_task_start_requested",
            content="Requesting backend task creation in background mode.",
            mode="background",
        )
        with httpx.Client(timeout=timeout_seconds) as client:
            with client.stream(
                "POST",
                f"{base_url}/v1/chat/completions",
                headers=_headers(api_key),
                json=payload,
            ) as response:
                response.raise_for_status()

                for line in response.iter_lines():
                    if not line:
                        continue

                    text = line.decode("utf-8") if isinstance(line, bytes) else str(line)
                    if not text.startswith("data:"):
                        continue

                    data = text[5:].strip()
                    if data == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        chunk = {"raw": data}

                    first_chunks.append(chunk)

                    maybe_task_id = _extract_task_id(chunk)
                    if maybe_task_id:
                        task_id = maybe_task_id
                        break

                    if (time.time() - started_at) >= handshake_timeout:
                        break

        if not task_id:
            _emit_tool_event(
                "law_task_start_failed",
                content="No task id was found in the initial stream handshake.",
                reason="missing_task_id_in_stream_handshake",
            )
            return json.dumps(
                {
                    "status": "error",
                    "tool": "start_law_report_task",
                    "message": "No task_id found in stream handshake. Please use law_report_api(stream=true) or check server event schema.",
                    "preview_chunks": first_chunks[:5],
                },
                ensure_ascii=False,
                indent=2,
            )

        _emit_tool_event(
            "law_task_started",
            content="Backend task created successfully.",
            task_id=task_id,
        )
        return json.dumps(
            {
                "status": "accepted",
                "task_id": task_id,
                "message": "Task created by backend. Use get_law_report_task(task_id) to poll status.",
                "preview_chunks": first_chunks[:5],
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as error:
        _emit_tool_event(
            "law_task_start_error",
            content=str(error),
            error_type=error.__class__.__name__,
        )
        return json.dumps(
            {
                "status": "error",
                "tool": "start_law_report_task",
                "error_type": error.__class__.__name__,
                "message": str(error),
                "endpoint": f"{base_url}/v1/chat/completions",
            },
            ensure_ascii=False,
            indent=2,
        )


@tool(parse_docstring=True)
@_bind_tool_event_source
def run_law_report_stream_and_wait(
    messages_json: str,
    model: str = "legal-report-agent",
    jurisdiction: str = "",
    country: str = "",
    industry: str = "",
    scope_json: str = "[]",
    temperature: float = 0.7,
    max_tokens: int = 0,
    include_raw: bool = False,
) -> str:
    """Run report generation in foreground stream mode and verify final task status.

    Workflow:
    1) Call /v1/chat/completions with stream=true and consume stream until [DONE]
    2) Extract task_id from stream events when available
    3) Query /v1/tasks/{task_id} for final status and result summary

    Args:
        messages_json: JSON array string of OpenAI chat messages.
        model: Model name for /v1/chat/completions.
        jurisdiction: Optional legal jurisdiction.
        country: Optional country context.
        industry: Optional industry context.
        scope_json: Optional JSON string array for scope values.
        temperature: Sampling temperature.
        max_tokens: Optional token limit. Use 0 to omit this field.
        include_raw: Whether to include raw stream chunks and raw task payload.

    Returns:
        JSON string with streamed content, optional task id, and final task status.
    """
    try:
        _emit_tool_event(
            "law_foreground_stream_started",
            content="Starting foreground streaming report generation.",
            mode="foreground",
        )
        payload = _build_payload(
            messages_json=messages_json,
            model=model,
            stream=True,
            jurisdiction=jurisdiction,
            country=country,
            industry=industry,
            scope_json=scope_json,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        base_url, api_key, timeout_seconds = _api_config()
        with httpx.Client(timeout=timeout_seconds) as client:
            with client.stream(
                "POST",
                f"{base_url}/v1/chat/completions",
                headers=_headers(api_key),
                json=payload,
            ) as response:
                response.raise_for_status()
                stream_content, stream_chunks, task_id = _consume_stream_for_content_and_task(response)

        _emit_tool_event(
            "law_foreground_stream_completed",
            content="Foreground stream consumption completed.",
            task_id=task_id,
            stream_chars=len(stream_content),
        )

        task_payload: dict[str, object] = {}
        task_status = "unknown"
        task_content = ""
        task_query_error = ""

        if task_id:
            try:
                fetched_task = _fetch_task(task_id)
                task_payload = fetched_task
                task_status = _extract_task_status(fetched_task)
                task_content = _extract_content_from_openai_json(fetched_task)
                _emit_tool_event(
                    "law_task_status_checked",
                    content=f"Fetched task status: {task_status}.",
                    task_id=task_id,
                    task_status=task_status,
                    is_terminal=_is_terminal_task_status(task_status),
                )
            except Exception as task_error:
                task_query_error = f"{task_error.__class__.__name__}: {task_error}"
                _emit_tool_event(
                    "law_task_status_error",
                    content=task_query_error,
                    task_id=task_id,
                )

        final_content = task_content or stream_content
        result: dict[str, object] = {
            "status": "ok",
            "endpoint": f"{base_url}/v1/chat/completions",
            "task_id": task_id,
            "task_status": task_status,
            "is_terminal": _is_terminal_task_status(task_status) if task_id else False,
            "content": final_content,
            "message": "Foreground stream completed. If task_status is non-terminal/unknown, call get_law_report_task(task_id).",
        }

        if task_query_error:
            result["task_query_error"] = task_query_error

        if include_raw:
            result["raw_stream_chunks"] = stream_chunks
            result["raw_task"] = task_payload

        _emit_tool_event(
            "law_foreground_result_ready",
            content="Foreground result is ready.",
            task_id=task_id,
            task_status=task_status,
            content_chars=len(final_content),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as error:
        _emit_tool_event(
            "law_foreground_stream_error",
            content=str(error),
            error_type=error.__class__.__name__,
        )
        return json.dumps(
            {
                "status": "error",
                "tool": "run_law_report_stream_and_wait",
                "error_type": error.__class__.__name__,
                "message": str(error),
                "endpoint": f"{os.getenv('LAW_API_BASE_URL', 'http://localhost:8000').rstrip('/')}/v1/chat/completions",
            },
            ensure_ascii=False,
            indent=2,
        )


@tool(parse_docstring=True)
@_bind_tool_event_source
def get_law_report_task(task_id: str, include_raw: bool = False) -> str:
    """Get status or result for a previously started task.

    Args:
        task_id: Task id returned by start_law_report_task.
        include_raw: Whether to include raw API response payload.

    Returns:
        JSON string with current task status and available result.
    """
    try:
        payload = _fetch_task(task_id)
        task_status = _extract_task_status(payload)

        result: dict[str, object] = {
            "status": "ok",
            "task_id": task_id,
            "task_status": task_status,
            "is_terminal": _is_terminal_task_status(task_status),
            "content": _extract_content_from_openai_json(payload),
        }

        if include_raw:
            result["raw"] = payload

        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as error:
        return json.dumps(
            {
                "status": "error",
                "tool": "get_law_report_task",
                "error_type": error.__class__.__name__,
                "message": str(error),
            },
            ensure_ascii=False,
            indent=2,
        )


@tool(parse_docstring=True)
@_bind_tool_event_source
def list_law_report_tasks(limit: int = 20) -> str:
    """List recent law report tasks.

    Args:
        limit: Maximum tasks to return, sorted by created_at descending.

    Returns:
        JSON array with task summaries.
    """
    max_items = max(1, min(limit, 100))
    base_url, api_key, timeout_seconds = _api_config()

    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.get(
                f"{base_url}/v1/tasks",
                headers=_headers(api_key),
            )
        response.raise_for_status()
        parsed = response.json()

        tasks_raw = parsed if isinstance(parsed, list) else parsed.get("tasks", []) if isinstance(parsed, dict) else []
        if not isinstance(tasks_raw, list):
            tasks_raw = []

        summaries = []
        for task in tasks_raw[:max_items]:
            if not isinstance(task, dict):
                continue
            status_text = _extract_task_status(task)
            summaries.append(
                {
                    "task_id": _extract_task_id(task) or "",
                    "task_status": status_text,
                    "is_terminal": _is_terminal_task_status(status_text),
                }
            )

        return json.dumps({"status": "ok", "tasks": summaries}, ensure_ascii=False, indent=2)
    except Exception as error:
        return json.dumps(
            {
                "status": "error",
                "tool": "list_law_report_tasks",
                "error_type": error.__class__.__name__,
                "message": str(error),
            },
            ensure_ascii=False,
            indent=2,
        )


@tool(parse_docstring=True)
@_bind_tool_event_source
def cancel_law_report_task(task_id: str, include_raw: bool = False) -> str:
    """Cancel a previously created backend task.

    Args:
        task_id: Backend task id to cancel.
        include_raw: Whether to include raw cancel/status payloads.

    Returns:
        JSON string describing the cancel request result and latest known status.
    """
    previous_payload: dict[str, object] = {}
    previous_status = "unknown"
    latest_payload: dict[str, object] = {}
    latest_status = "unknown"

    try:
        try:
            previous_payload = _fetch_task(task_id)
            previous_status = _extract_task_status(previous_payload)
        except Exception:
            previous_payload = {}

        _emit_tool_event(
            "law_task_cancel_requested",
            content="Sending cancel request for backend task.",
            task_id=task_id,
            previous_task_status=previous_status,
        )

        cancel_payload = _cancel_task(task_id)

        try:
            latest_payload = _fetch_task(task_id)
            latest_status = _extract_task_status(latest_payload)
        except Exception:
            latest_payload = {}
            latest_status = previous_status

        _emit_tool_event(
            "law_task_cancel_completed",
            content=f"Cancel request completed with latest status: {latest_status}.",
            task_id=task_id,
            previous_task_status=previous_status,
            task_status=latest_status,
            is_terminal=_is_terminal_task_status(latest_status),
        )

        result: dict[str, object] = {
            "status": "ok",
            "task_id": task_id,
            "previous_task_status": previous_status,
            "task_status": latest_status,
            "is_terminal": _is_terminal_task_status(latest_status),
            "message": "Cancel request sent successfully.",
        }

        if include_raw:
            result["raw_cancel"] = cancel_payload
            result["raw_previous_task"] = previous_payload
            result["raw_task"] = latest_payload

        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as error:
        _emit_tool_event(
            "law_task_cancel_error",
            content=str(error),
            task_id=task_id,
            error_type=error.__class__.__name__,
        )
        return json.dumps(
            {
                "status": "error",
                "tool": "cancel_law_report_task",
                "task_id": task_id,
                "error_type": error.__class__.__name__,
                "message": str(error),
                "endpoint": f"{os.getenv('LAW_API_BASE_URL', 'http://localhost:8000').rstrip('/')}/v1/tasks/{task_id}/cancel",
            },
            ensure_ascii=False,
            indent=2,
        )


@tool(parse_docstring=True)
@_bind_tool_event_source
def wait_law_report_task(task_id: str, timeout_seconds: int = 300, poll_interval_seconds: int = 10) -> str:
    """Poll backend task status until completion or timeout.

    Args:
        task_id: Backend task id.
        timeout_seconds: Max wait duration.
        poll_interval_seconds: Poll interval.

    Returns:
        JSON with final status (completed/failed/timeout) and content when available.
    """
    start_time = time.time()
    interval = max(1, poll_interval_seconds)

    while True:
        payload = _fetch_task(task_id)
        task_status = _extract_task_status(payload)

        if _is_terminal_task_status(task_status):
            return json.dumps(
                {
                    "status": "ok",
                    "task_id": task_id,
                    "task_status": task_status,
                    "is_terminal": True,
                    "content": _extract_content_from_openai_json(payload),
                    "raw": payload,
                },
                ensure_ascii=False,
                indent=2,
            )

        if (time.time() - start_time) >= max(1, timeout_seconds):
            return json.dumps(
                {
                    "status": "timeout",
                    "task_id": task_id,
                    "task_status": task_status,
                    "is_terminal": False,
                    "message": "Task is still running. Continue polling with get_law_report_task or wait_law_report_task.",
                },
                ensure_ascii=False,
                indent=2,
            )

        time.sleep(interval)
