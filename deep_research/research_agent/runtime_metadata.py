"""Helpers to extract tenant metadata from runtime/config objects."""

from __future__ import annotations

from typing import Any

from langgraph.config import get_config


def extract_metadata(config_like: Any) -> dict[str, Any]:
    """Extract metadata with support for thread-config shapes.

    Priority:
    1) config.metadata
    2) config.configurable.metadata
    3) config.configurable.thread.metadata
    4) config.configurable.thread_config.metadata
    """
    if not isinstance(config_like, dict):
        return {}

    top_level = config_like.get("metadata")
    if isinstance(top_level, dict):
        if isinstance(top_level.get("user_id"), str) and isinstance(top_level.get("mission_id"), str):
            return top_level

        nested_thread = top_level.get("thread")
        if isinstance(nested_thread, dict):
            nested_thread_metadata = nested_thread.get("metadata")
            if isinstance(nested_thread_metadata, dict):
                return nested_thread_metadata

    configurable = config_like.get("configurable")
    if isinstance(configurable, dict):
        configurable_metadata = configurable.get("metadata")
        if isinstance(configurable_metadata, dict):
            return configurable_metadata

        thread = configurable.get("thread")
        if isinstance(thread, dict):
            thread_metadata = thread.get("metadata")
            if isinstance(thread_metadata, dict):
                return thread_metadata

        thread_config = configurable.get("thread_config")
        if isinstance(thread_config, dict):
            thread_config_metadata = thread_config.get("metadata")
            if isinstance(thread_config_metadata, dict):
                return thread_config_metadata

    context = config_like.get("context")
    if isinstance(context, dict):
        context_thread = context.get("thread")
        if isinstance(context_thread, dict):
            context_thread_metadata = context_thread.get("metadata")
            if isinstance(context_thread_metadata, dict):
                return context_thread_metadata

    return {}


def require_tenant_ids(config_like: Any) -> tuple[str, str]:
    metadata = extract_metadata(config_like)
    user_id = metadata.get("user_id")
    mission_id = metadata.get("mission_id")

    if not user_id or not mission_id:
        raise ValueError("metadata.user_id and metadata.mission_id are required")

    return str(user_id), str(mission_id)


def resolve_config_like(runtime_or_config: Any) -> dict[str, Any]:
    """Resolve a config-like dict from runtime/config inputs.

    Supports:
    - direct config dict
    - runtime objects with `.config`
    - runtime objects with `.context`
    - LangGraph ambient `get_config()` fallback
    """
    if isinstance(runtime_or_config, dict):
        return runtime_or_config

    runtime_config = getattr(runtime_or_config, "config", None)
    if isinstance(runtime_config, dict):
        return runtime_config

    runtime_context = getattr(runtime_or_config, "context", None)
    if isinstance(runtime_context, dict):
        return {"context": runtime_context}

    try:
        ambient = get_config()
    except Exception:
        ambient = None

    if isinstance(ambient, dict):
        return ambient

    return {}


def require_tenant_ids_from_runtime(runtime_or_config: Any) -> tuple[str, str]:
    config_like = resolve_config_like(runtime_or_config)
    return require_tenant_ids(config_like)
