"""File Chat Agent - Document Q&A using MCP file tools."""

import os
from datetime import datetime
import asyncio

from dotenv import load_dotenv
from deepagents import create_deep_agent
from deepagents.backends import StateBackend

from research_agent.middlewares import (
	CustomSummarizationMiddleware,
)
from notebook_agent.middlewares import DocMetadataMiddleware
from notebook_agent.prompts import FILE_CHAT_INSTRUCTIONS
from research_agent.tools import (
	CustomContext,
	alb_mcp_client,
	think_tool,
)
from utils import create_openai_chat_model

# Load environment variables
load_dotenv()

# Summarization settings (can be overridden via .env)
MAIN_AGENT_COMPRESS_TOKEN_LIMIT = int(os.getenv("MAIN_AGENT_COMPRESS_TOKEN_LIMIT", "80000"))
MAIN_AGENT_KEEP_HISTORYS = int(os.getenv("MAIN_AGENT_KEEP_HISTORYS", "10"))

# Get current date (reserved for potential prompt use)
current_date = datetime.now().strftime("%Y-%m-%d")

my_model = create_openai_chat_model()


async def create_agent_with_mcp():
	"""Create file chat agent with MCP tools loaded asynchronously."""
	mcp_tools = await alb_mcp_client.get_tools()

	all_tools = [think_tool] + mcp_tools

	agent = create_deep_agent(
		model=my_model,
		tools=all_tools,
		context_schema=CustomContext,
		system_prompt=FILE_CHAT_INSTRUCTIONS,
		middleware=[
			# DocMetadataMiddleware(),
			CustomSummarizationMiddleware(
				model=my_model,
				trigger=("tokens", MAIN_AGENT_COMPRESS_TOKEN_LIMIT),
				keep=("messages", MAIN_AGENT_KEEP_HISTORYS),
			)
		],
	).with_config({"recursion_limit": 300})
	return agent


agent = asyncio.run(create_agent_with_mcp())
