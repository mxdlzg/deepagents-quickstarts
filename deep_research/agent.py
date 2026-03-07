"""Research Agent - Standalone script for LangGraph deployment.

This module creates a deep research agent with custom tools and prompts
for conducting web research with strategic thinking and context management.
"""

import os
from datetime import datetime
import asyncio
import json

from dotenv import load_dotenv
from deepagents import create_deep_agent, CompiledSubAgent
from langchain.agents import create_agent

from research_agent.middlewares import CustomSummarizationMiddleware, CustomMemoryMiddleware
from research_agent.backend_factory import create_tenant_backend
from research_agent.prompts import (
    RESEARCHER_INSTRUCTIONS,
    RESEARCH_WORKFLOW_INSTRUCTIONS,
    SUBAGENT_DELEGATION_INSTRUCTIONS,
)
from research_agent.tools import (
    ALB_MCP_CLIENT,
    CustomContext,
    build_citation_ledger,
    finalize_mission_report,
    mission_storage_manifest,
    persist_citation_ledger,
    persist_sources_appendix,
    publish_final_report,
    request_plan_approval,
    render_sources_from_ledger,
    route_research,
    tavily_search,
    think_tool,
    verify_and_repair_final_report,
)
from utils import create_openai_chat_model

# Load environment variables
load_dotenv()

# Summarization settings (can be overridden via .env)
# MAIN_AGENT_COMPRESS_TOKEN_LIMIT / SUB_AGENT_COMPRESS_TOKEN_LIMIT control the trigger token threshold
# MAIN_AGENT_KEEP_HISTORYS / SUB_AGENT_KEEP_HISTORYS control how many messages to keep
MAIN_AGENT_COMPRESS_TOKEN_LIMIT = int(os.getenv("MAIN_AGENT_COMPRESS_TOKEN_LIMIT", "80000"))
SUB_AGENT_COMPRESS_TOKEN_LIMIT = int(os.getenv("SUB_AGENT_COMPRESS_TOKEN_LIMIT", "90000"))

MAIN_AGENT_KEEP_HISTORYS = int(os.getenv("MAIN_AGENT_KEEP_HISTORYS", "6"))
SUB_AGENT_KEEP_HISTORYS = int(os.getenv("SUB_AGENT_KEEP_HISTORYS", "4"))

# Limits
max_concurrent_research_units = 3
max_researcher_iterations = 3

# Get current date
current_date = datetime.now().strftime("%Y-%m-%d")

# Combine orchestrator instructions (RESEARCHER_INSTRUCTIONS only for sub-agents)
INSTRUCTIONS = (
    RESEARCH_WORKFLOW_INSTRUCTIONS
    + "\n\n"
    + "=" * 80
    + "\n\n"
    + SUBAGENT_DELEGATION_INSTRUCTIONS.format(
        max_concurrent_research_units=max_concurrent_research_units,
        max_researcher_iterations=max_researcher_iterations,
    )
    + "\n\n"
    + "HITL requirement: You MUST call request_plan_approval before delegating large-scale research tasks."
    + "\n"
    + "Finalization requirement: publish_final_report must return status=pass, then execute a final write_todos marking all tasks [DONE], then respond to user."
)

my_model = create_openai_chat_model()


def _apply_safe_tool_error_handling(tools):
    """Ensure tool exceptions degrade to structured text instead of aborting the run."""

    def _to_error_payload(error: Exception, tool_name: str = "unknown_tool") -> str:
        return json.dumps(
            {
                "status": "error",
                "tool": tool_name,
                "error_type": error.__class__.__name__,
                "message": str(error),
            },
            ensure_ascii=False,
        )

    for tool in tools:
        try:
            tool_name = getattr(tool, "name", "unknown_tool")
            tool.handle_tool_error = lambda error, _tool_name=tool_name: _to_error_payload(error, _tool_name)
            tool.handle_validation_error = True
        except Exception:
            continue


def create_research_subagent(tools):
    custom_graph = create_agent(
        model=my_model,
        tools=tools,
        system_prompt=RESEARCHER_INSTRUCTIONS.format(date=current_date),
        middleware=[
            CustomSummarizationMiddleware(
                model=my_model,
                trigger=("tokens", SUB_AGENT_COMPRESS_TOKEN_LIMIT),
                keep=("messages", SUB_AGENT_KEEP_HISTORYS),
            )
        ],
    ).with_config({
        "recursion_limit": 500
    })

    return CompiledSubAgent(
        name="research-agent",
        description="Delegate research to the sub-agent researcher. Only give this researcher one topic at a time.",
        runnable=custom_graph,
    )


async def create_agent_with_mcp():
    """Create agent with MCP tools loaded asynchronously."""
    # Load MCP tools asynchronously
    mcp_tools = await ALB_MCP_CLIENT.get_tools()

    # Combine base tools with MCP tools
    all_tools = [
        route_research,
        tavily_search,
        think_tool,
        request_plan_approval,
        build_citation_ledger,
        render_sources_from_ledger,
        mission_storage_manifest,
        persist_citation_ledger,
        persist_sources_appendix,
        publish_final_report,
        finalize_mission_report,
        verify_and_repair_final_report,
    ] + mcp_tools
    _apply_safe_tool_error_handling(all_tools)

    research_sub_agent = create_research_subagent(all_tools)

    # Create the agent
    agent = create_deep_agent(
        model=my_model,
        tools=all_tools,
        context_schema=CustomContext,
        system_prompt=INSTRUCTIONS,
        subagents=[research_sub_agent],
        backend=create_tenant_backend,
        interrupt_on={"request_plan_approval": True},
        middleware=[
            CustomSummarizationMiddleware(
                model=my_model,
                trigger=("tokens", MAIN_AGENT_COMPRESS_TOKEN_LIMIT),
                keep=("messages", MAIN_AGENT_KEEP_HISTORYS)
            ),
            CustomMemoryMiddleware(
                backend=create_tenant_backend,
                sources=[],
            ),
        ]
    ).with_config({
        "recursion_limit": 500
    })
    return agent

# Create the agent at module level by running the async function
agent = asyncio.run(create_agent_with_mcp())
