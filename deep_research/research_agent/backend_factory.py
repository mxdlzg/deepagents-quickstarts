"""Backend factory for tenant-isolated, harness-aligned storage routing."""

from __future__ import annotations

from deepagents.backends import CompositeBackend, StateBackend, StoreBackend


def create_tenant_backend(runtime):
    """Create a backend with `/memories/` routed to StoreBackend.

    Behavior:
    - Always uses StateBackend as default (thread-scoped workspace files)
    - If `runtime.store` exists, route tenant memory namespace to StoreBackend
      for cross-thread durability
    - If no store is available, gracefully falls back to StateBackend-only
    """
    default_backend = StateBackend(runtime)

    if runtime.store is None:
        return default_backend

    return CompositeBackend(
        default=default_backend,
        routes={
            "/memories/": StoreBackend(runtime),
        },
    )
