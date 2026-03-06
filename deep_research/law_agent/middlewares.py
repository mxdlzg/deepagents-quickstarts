"""Middleware helpers for law report agent."""

import os

from research_agent.middlewares import CustomSummarizationMiddleware


def build_law_middlewares(model):
    """Build middleware stack for law agent."""
    return [
        CustomSummarizationMiddleware(
            model=model,
            trigger=("tokens", int(os.getenv("MAIN_AGENT_COMPRESS_TOKEN_LIMIT", "150000"))),
            keep=("messages", int(os.getenv("MAIN_AGENT_KEEP_HISTORYS", "10"))),
        )
    ]
