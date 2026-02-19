"""Backend factory for tenant-isolated, harness-aligned storage routing."""

from __future__ import annotations

from deepagents.backends import CompositeBackend, StateBackend, StoreBackend

from research_agent.memory_paths import MemoryPathManager
from research_agent.runtime_metadata import require_tenant_ids_from_runtime


def create_tenant_backend(runtime):
    """Create a backend with `/memories/users/{user_id}/` routed to StoreBackend.

    Behavior:
    - Always uses StateBackend as default (thread-scoped workspace files)
    - If `runtime.store` exists, route tenant memory namespace to StoreBackend
      for cross-thread durability
    - If no store is available, gracefully falls back to StateBackend-only
    """
    user_id, mission_id = require_tenant_ids_from_runtime(runtime)

    path_manager = MemoryPathManager(user_id=user_id, mission_id=mission_id)
    user_memory_prefix = f"{path_manager.user_root().as_posix()}/"

    default_backend = StateBackend(runtime)

    if runtime.store is None:
        return default_backend

    return CompositeBackend(
        default=default_backend,
        routes={
            user_memory_prefix: StoreBackend(runtime),
        },
    )
