"""Helpers to extract tenant metadata from runtime/config objects."""

from __future__ import annotations

from typing import Any


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
        return top_level

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

    return {}


def require_tenant_ids(config_like: Any) -> tuple[str, str]:
    metadata = extract_metadata(config_like)
    user_id = metadata.get("user_id")
    mission_id = metadata.get("mission_id")

    if not user_id or not mission_id:
        raise ValueError("metadata.user_id and metadata.mission_id are required")

    return str(user_id), str(mission_id)
