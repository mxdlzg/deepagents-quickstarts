"""Tenant-isolated memory path utilities for deep research agents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re

SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@dataclass(frozen=True)
class MemoryPathManager:
    """Builds strict, tenant-scoped memory paths.

    All task data is namespaced under user and thread scope:
    - /memories/users/{user_id}/profile/preferences.json
    - /memories/users/{user_id}/threads/{thread_id}/...
    """

    user_id: str
    thread_id: str

    def __post_init__(self) -> None:
        if not SAFE_ID_PATTERN.match(self.user_id):
            raise ValueError("Invalid user_id. Allowed: [a-zA-Z0-9_-], 1-64 chars")
        if not SAFE_ID_PATTERN.match(self.thread_id):
            raise ValueError("Invalid thread_id. Allowed: [a-zA-Z0-9_-], 1-64 chars")

    def user_root(self) -> PurePosixPath:
        return PurePosixPath("/memories/users") / self.user_id

    def user_profile_preferences(self) -> PurePosixPath:
        return self.user_root() / "profile" / "preferences.json"

    def thread_root(self) -> PurePosixPath:
        return self.user_root() / "threads" / self.thread_id

    def mission_root(self) -> PurePosixPath:
        return self.thread_root()

    def raw_materials_dir(self) -> PurePosixPath:
        return self.thread_root() / "raw_materials"

    def knowledge_graph_dir(self) -> PurePosixPath:
        return self.thread_root() / "knowledge_graph"

    def drafts_dir(self) -> PurePosixPath:
        return self.thread_root() / "drafts"

    def thread_path(self, *parts: str) -> str:
        path = self.thread_root().joinpath(*parts)
        root = str(self.thread_root())
        if not str(path).startswith(root):
            raise PermissionError("Path traversal blocked for thread scope")
        return str(path)

    def mission_path(self, *parts: str) -> str:
        return self.thread_path(*parts)
