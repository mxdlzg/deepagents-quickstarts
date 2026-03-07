"""Law Report Agent entrypoint for LangGraph deployment."""

import asyncio

from dotenv import load_dotenv

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
from utils import create_openai_chat_model

# Load environment variables
load_dotenv()

my_model = create_openai_chat_model()

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
