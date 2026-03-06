"""Law Report Agent entrypoint for LangGraph deployment."""

import asyncio
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from deepagents import create_deep_agent
from law_agent.middlewares import build_law_middlewares
from law_agent.prompts import LAW_AGENT_INSTRUCTIONS
from law_agent.tools import (
    cancel_law_report_task,
    get_law_report_task,
    law_report_api,
    list_law_report_tasks,
    run_law_report_stream_and_wait,
    start_law_report_task,
    wait_law_report_task,
)
from research_agent.tools import CustomContext

# Load environment variables
load_dotenv()


def create_model() -> ChatOpenAI:
    """Create OpenAI-compatible chat model from environment variables."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set")

    model_name = os.getenv("OPENAI_MODEL", "gpt-4o")
    base_url = os.getenv("OPENAI_BASE_URL", None)
    temperature = float(os.getenv("OPENAI_TEMPERATURE", "0.0"))
    top_p = float(os.getenv("OPENAI_TOP_P", "1.0"))
    max_tokens = os.getenv("OPENAI_MAX_TOKENS", None)
    enable_thinking = os.getenv("OPENAI_MODEL_ENABLE_THINKING", "false").lower() == "true"

    model_kwargs = {
        "api_key": api_key,
        "model": model_name,
        "temperature": temperature,
        "top_p": top_p,
        "extra_body": {
            "chat_template_kwargs": {
                "enable_thinking": enable_thinking,
            }
        },
    }

    if base_url:
        model_kwargs["base_url"] = base_url

    if max_tokens:
        model_kwargs["max_tokens"] = int(max_tokens)

    return ChatOpenAI(**model_kwargs)


my_model = create_model()

async def create_agent_with_tools():
    """Create law report agent."""
    agent = create_deep_agent(
        model=my_model,
        tools=[
            law_report_api,
            run_law_report_stream_and_wait,
            start_law_report_task,
            get_law_report_task,
            list_law_report_tasks,
            cancel_law_report_task,
            wait_law_report_task,
        ],
        context_schema=CustomContext,
        system_prompt=LAW_AGENT_INSTRUCTIONS,
        middleware=build_law_middlewares(my_model),
    ).with_config({"recursion_limit": 300})
    return agent


agent = asyncio.run(create_agent_with_tools())
