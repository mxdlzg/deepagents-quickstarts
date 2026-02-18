"""Research Agent - Standalone script for LangGraph deployment.

This module creates a deep research agent with custom tools and prompts
for conducting web research with strategic thinking and context management.
"""

import os
from datetime import datetime
import asyncio

from langchain_openai import ChatOpenAI
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
    request_plan_approval,
    render_sources_from_ledger,
    route_research,
    tavily_search,
    think_tool,
)

# Load environment variables
load_dotenv()

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
)

def create_model():
    """Create OpenAI-compatible chat model from environment variables.

    Environment variables:
        OPENAI_API_KEY: API key for OpenAI or compatible service (required)
        OPENAI_MODEL: Model name (default: gpt-4o)
        OPENAI_BASE_URL: Base URL for API endpoint (optional, uses OpenAI by default)
        OPENAI_TEMPERATURE: Temperature for sampling (default: 0.0)
        OPENAI_TOP_P: Nucleus sampling parameter (default: 1.0)
        OPENAI_MAX_TOKENS: Maximum tokens in response (optional)

    Returns:
        ChatOpenAI: Configured OpenAI chat model
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set")

    model_name = os.getenv("OPENAI_MODEL", "gpt-4o")
    base_url = os.getenv("OPENAI_BASE_URL", None)
    temperature = float(os.getenv("OPENAI_TEMPERATURE", "0.0"))
    top_p = float(os.getenv("OPENAI_TOP_P", "1.0"))
    max_tokens = os.getenv("OPENAI_MAX_TOKENS", None)

    model_kwargs = {
        "api_key": api_key,
        "model": model_name,
        "temperature": temperature,
        "top_p": top_p,
    }

    if base_url:
        model_kwargs["base_url"] = base_url

    if max_tokens:
        model_kwargs["max_tokens"] = int(max_tokens)

    return ChatOpenAI(**model_kwargs)

my_model = create_model()


def create_research_subagent(tools):
    custom_graph = create_agent(
        model=my_model,
        tools=tools,
        system_prompt=RESEARCHER_INSTRUCTIONS.format(date=current_date),
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
        finalize_mission_report,
    ] + mcp_tools
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
                trigger=("tokens", 80000),
                keep=("messages", 6)
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
