"""Research Agent - Standalone script for LangGraph deployment.

This module creates a deep research agent with custom tools and prompts
for conducting web research with strategic thinking and context management.
"""

import os
from datetime import datetime

from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from deepagents import create_deep_agent

from research_agent.middlewares import CustomSummarizationMiddleware
from research_agent.prompts import (
    RESEARCHER_INSTRUCTIONS,
    RESEARCH_WORKFLOW_INSTRUCTIONS,
    SUBAGENT_DELEGATION_INSTRUCTIONS,
)
from research_agent.tools import tavily_search, think_tool, alb_mcp_client
from langchain.agents.middleware.summarization import SummarizationMiddleware

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
)

# Create research sub-agent
research_sub_agent = {
    "name": "research-agent",
    "description": "Delegate research to the sub-agent researcher. Only give this researcher one topic at a time.",
    "system_prompt": RESEARCHER_INSTRUCTIONS.format(date=current_date),
    "tools": [tavily_search, think_tool],
}


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

async def create_agent_with_mcp():
    """Create agent with MCP tools loaded asynchronously."""
    model = create_model()
    
    # Load MCP tools asynchronously
    mcp_tools = await alb_mcp_client.get_tools()
    
    # Combine base tools with MCP tools
    all_tools = [tavily_search, think_tool] + mcp_tools
    
    # Create the agent
    agent = create_deep_agent(
        model=model,
        tools=all_tools,
        system_prompt=INSTRUCTIONS,
        subagents=[research_sub_agent],
        middleware=[
            CustomSummarizationMiddleware(
                model=model,
                trigger=("tokens", 120000),
                keep=("messages", 6)
            )
        ]
    )
    return agent

# Create the agent at module level by running the async function
import asyncio
agent = asyncio.run(create_agent_with_mcp())
