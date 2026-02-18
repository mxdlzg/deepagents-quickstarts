# 🚀 Deep Research

## 🚀 Quickstart

**Prerequisites**: Install [uv](https://docs.astral.sh/uv/) package manager:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Ensure you are in the `deep_research` directory:
```bash
cd deep_research
```

Install packages:
```bash
uv sync
```

Set your API keys in your environment. Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
# Edit .env with your API keys
```

Or set them directly:

```bash
export OPENAI_API_KEY=sk-...                          # Required for OpenAI model
export TAVILY_API_KEY=your_tavily_api_key_here        # Required for web search ([get one here](https://www.tavily.com/)) with a generous free tier
export LANGSMITH_API_KEY=your_langsmith_api_key_here  # [LangSmith API key](https://smith.langchain.com/settings) (free to sign up)
```

## Usage Options

You can run this quickstart in two ways:

### Option 1: Jupyter Notebook

Run the interactive notebook to step through the research agent:

```bash
uv run jupyter notebook research_agent.ipynb
```

### Option 2: LangGraph Server

Run a local [LangGraph server](https://langchain-ai.github.io/langgraph/tutorials/langgraph-platform/local-server/) with a web interface:

```bash
langgraph dev
langgraph dev --debug-port 5678  --allow-blocking
 {
            "name": "Attach to LangGraph",
            "type": "debugpy",
            "request": "attach",
            "connect": {
                "host": "0.0.0.0",
                "port": 5678
            }
}
```

LangGraph server will open a new browser window with the Studio interface, which you can submit your search query to: 

<img width="2869" height="1512" alt="Screenshot 2025-11-17 at 11 42 59 AM" src="https://github.com/user-attachments/assets/03090057-c199-42fe-a0f7-769704c2124b" />

You can also connect the LangGraph server to a [UI specifically designed for deepagents](https://github.com/langchain-ai/deep-agents-ui):

```bash
$ git clone https://github.com/langchain-ai/deep-agents-ui.git
$ cd deep-agents-ui
$ yarn install
$ yarn dev
```

Then follow the instructions in the [deep-agents-ui README](https://github.com/langchain-ai/deep-agents-ui?tab=readme-ov-file#connecting-to-a-langgraph-server) to connect the UI to the running LangGraph server.

This provides a user-friendly chat interface and visualization of files in state. 

<img width="2039" height="1495" alt="Screenshot 2025-11-17 at 1 11 27 PM" src="https://github.com/user-attachments/assets/d559876b-4c90-46fb-8e70-c16c93793fa8" />

## 📚 Resources

- **[Deep Research Course](https://academy.langchain.com/courses/deep-research-with-langgraph)** - Full course on deep research with LangGraph

### Custom Model

The agent is configured to use OpenAI-compatible models with environment variables. By default, it uses `gpt-4o`.

**Configuration via Environment Variables:**

```bash
# Required
OPENAI_API_KEY=sk-...

# Optional (with defaults)
OPENAI_MODEL=gpt-4o                    # Model name (default: gpt-4o)
OPENAI_BASE_URL=https://api.openai.com/v1  # API endpoint (optional)
OPENAI_TEMPERATURE=0.0                 # Sampling temperature (default: 0.0)
OPENAI_TOP_P=1.0                       # Nucleus sampling (default: 1.0)
OPENAI_MAX_TOKENS=2048                 # Max response tokens (optional)
```

See `.env.example` for more details and examples of using different providers (Azure, local endpoints, etc.).

**Programmatic Configuration:**

If you want to use a different model provider, you can modify `agent.py`:

```python
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

# Using OpenAI
model = ChatOpenAI(model="gpt-4o", temperature=0.0)

# Using Claude (if you want to switch back)
model = ChatAnthropic(model="claude-3-5-sonnet-20241022", temperature=0.0)

# Using Azure OpenAI
model = ChatOpenAI(
    api_key="your-azure-api-key",
    model="gpt-4",
    base_url="https://<your-resource>.openai.azure.com/openai/deployments/<model-name>",
    api_version="2024-02-15-preview",
)

# Using local/self-hosted endpoints
model = ChatOpenAI(
    api_key="dummy-key",
    model="local-model",
    base_url="http://localhost:8000/v1",
)

from deepagents import create_deep_agent
agent = create_deep_agent(model=model)

### Custom Instructions

The deep research agent uses custom instructions defined in `deep_research/research_agent/prompts.py` that complement (rather than duplicate) the default middleware instructions. You can modify these in any way you want. 

| Instruction Set | Purpose |
|----------------|---------|
| `RESEARCH_WORKFLOW_INSTRUCTIONS` | Defines the 5-step research workflow: save request → plan with TODOs → delegate to sub-agents → synthesize → respond. Includes research-specific planning guidelines like batching similar tasks and scaling rules for different query types. |
| `SUBAGENT_DELEGATION_INSTRUCTIONS` | Provides concrete delegation strategies with examples: simple queries use 1 sub-agent, comparisons use 1 per element, multi-faceted research uses 1 per aspect. Sets limits on parallel execution (max 3 concurrent) and iteration rounds (max 3). |
| `RESEARCHER_INSTRUCTIONS` | Guides individual research sub-agents to conduct focused web searches. Includes hard limits (2-3 searches for simple queries, max 5 for complex), emphasizes using `think_tool` after each search for strategic reflection, and defines stopping criteria. |

### Custom Tools

The deep research agent adds the following custom tools beyond the built-in deepagent tools. You can also use your own tools, including via MCP servers. See the Deepagents package [README](https://github.com/langchain-ai/deepagents?tab=readme-ov-file#mcp) for more details.

| Tool Name | Description |
|-----------|-------------|
| `tavily_search` | Web search tool that uses Tavily purely as a URL discovery engine. Performs searches using Tavily API to find relevant URLs, fetches full webpage content via HTTP with proper User-Agent headers (avoiding 403 errors), converts HTML to markdown, and returns the complete content without summarization to preserve all information for the agent's analysis. Works with both Claude and Gemini models. |
| `think_tool` | Strategic reflection mechanism that helps the agent pause and assess progress between searches, analyze findings, identify gaps, and plan next steps. |

## Harness-Aligned Production Notes

This project now aligns with DeepAgents Harness guidance for planning, subagents, HITL, and memory routing.

- **HITL checkpoint**: `request_plan_approval` is configured in `interrupt_on`, creating a mandatory plan-approval pause.
- **Hybrid retrieval routing**: `route_research` helps pick internal MCP, external web, or hybrid retrieval paths.
- **Tenant-scoped memory**: memory paths are managed by `MemoryPathManager` with strict `user_id`/`mission_id` validation.
- **Long-term memory route**: `create_tenant_backend` uses `CompositeBackend` to route `/memories/users/{user_id}/...` to `StoreBackend` when a LangGraph store is available.

### Required runtime metadata

For tenant isolation to work, each run must include metadata fields:

- `user_id`
- `mission_id`

Without these values, the agent will fail fast to prevent cross-tenant memory leakage.

