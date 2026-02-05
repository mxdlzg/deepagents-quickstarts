"""File Chat Agent - Document Q&A using MCP file tools."""

import os
from datetime import datetime
import asyncio

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
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

# Load environment variables
load_dotenv()

# Get current date (reserved for potential prompt use)
current_date = datetime.now().strftime("%Y-%m-%d")


def create_model():
	"""Create OpenAI-compatible chat model from environment variables."""
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
				trigger=("tokens", 80000),
				keep=("messages", 10),
			)
		],
	).with_config({"recursion_limit": 300})
	return agent


agent = asyncio.run(create_agent_with_mcp())
