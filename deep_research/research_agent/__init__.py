"""Deep Research Agent Example.

This module demonstrates building a research agent using the deepagents package
with custom tools for web search and strategic thinking.
"""

from research_agent.prompts import (
    RESEARCHER_INSTRUCTIONS,
    RESEARCH_WORKFLOW_INSTRUCTIONS,
    SUBAGENT_DELEGATION_INSTRUCTIONS,
)
from research_agent.backend_factory import create_tenant_backend
from research_agent.tools import (
    build_citation_ledger,
    finalize_mission_report,
    mission_storage_manifest,
    persist_citation_ledger,
    persist_sources_appendix,
    render_sources_from_ledger,
    request_plan_approval,
    route_research,
    tavily_search,
    think_tool,
    verify_and_repair_final_report,
)

__all__ = [
    "tavily_search",
    "think_tool",
    "route_research",
    "request_plan_approval",
    "build_citation_ledger",
    "render_sources_from_ledger",
    "mission_storage_manifest",
    "persist_citation_ledger",
    "persist_sources_appendix",
    "finalize_mission_report",
    "verify_and_repair_final_report",
    "create_tenant_backend",
    "RESEARCHER_INSTRUCTIONS",
    "RESEARCH_WORKFLOW_INSTRUCTIONS",
    "SUBAGENT_DELEGATION_INSTRUCTIONS",
]
