"""Example: consume LangGraph stream events for law agent with subgraph-aware metadata.

Run this from deep_research/ after configuring env variables.
"""

from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk

from agent_law import agent


def _render_message_chunk(token: AIMessageChunk, metadata: dict[str, Any]) -> None:
    agent_name = metadata.get("lc_agent_name") or metadata.get("langgraph_node") or "unknown"
    if token.content:
        print(f"[messages][{agent_name}] {token.content}", end="", flush=True)


def main() -> None:
    user_prompt = "请生成一份中国互联网行业数据合规法律风险报告，包含建议。"
    inputs = {"messages": [{"role": "user", "content": user_prompt}]}

    for _, stream_mode, data in agent.stream(
        inputs,
        stream_mode=["messages", "updates"],
        subgraphs=True,
    ):
        if stream_mode == "messages":
            token, metadata = data
            if agent_name := metadata.get("lc_agent_name"):
                if agent_name != current_agent:
                    print(f"🤖 {agent_name}: ")
                    current_agent = agent_name  
            if isinstance(token, AIMessage):
                print(f"[messages][{agent_name}] {token.content}", end="", flush=True)
        if stream_mode == "updates":
            for source, update in data.items():
                if source in ("model", "tools"):
                    print(update["messages"][-1])


if __name__ == "__main__":
    main()
