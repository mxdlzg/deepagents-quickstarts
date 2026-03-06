import asyncio
from typing import Annotated, Dict, Any, List
import os
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph, START
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel
from typing_extensions import TypedDict

def create_model() -> ChatOpenAI:
    """Create OpenAI-compatible chat model from environment variables."""
    api_key = "sk-h9zJFSDzpxml1WZKuPGgig"
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set")

    model_name = "alb_model_2601"
    base_url = "http://192.168.195.195:18011/v1"
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


llm = create_model()

# 定义状态类型


class State(TypedDict):
    messages: Annotated[list, add_messages]  # 对话历史
    data: Dict[str, Any]  # 存储工具调用结果的数据

# 定义异步工具函数

@tool
async def fetch_weather(location: str) -> Dict[str, Any]:
    """
   指定位置的天气信息

    Args:
        location: 位置名称，如城市名

    Returns:
        包含天气信息的字典
    """
    # 模拟异步API调用
    print(f"正在获取 {location} 的天气数据...")
    await asyncio.sleep(5)  # 模拟网络延迟

    # 返回模拟的天气数据
    weather_data = {
        "location": location,
        "temperature": 25,
        "condition": "晴天",
        "humidity": 60,
        "wind": "东北风3级"
    }
    
    print(f"获取天气数据: {weather_data}")
    return weather_data



# 定义所有工具列表
all_tools = [fetch_weather]

# 定义处理节点

async def process_query(state: State):
    """处理用户查询，决定是否需要调用工具"""
    # 获取最新的用户消息
    user_message = state["messages"][-1].content
    llm_with_tools = llm.bind_tools(all_tools)
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是一个智能助手。"),
        ("human", "{input}"),
    ])
    chain = prompt | llm_with_tools
    response = await chain.ainvoke({"input": user_message})
    print(f"LLM响应: {response}")
    print("_________________________________")
    return {"messages": response}

def create_async_tools_graph():
    """创建使用异步工具的状态图"""
    builder = StateGraph(State)

    # 添加节点
    builder.add_node("process_query", process_query)
    builder.add_node("tools", ToolNode(tools=all_tools))
    # 添加边
    builder.add_edge(START, "process_query")
    
    # 添加条件边，使用自定义路由函数
    builder.add_conditional_edges(
        "process_query",
        tools_condition,
    )
    builder.add_edge("tools", "process_query")
    # 编译图
    return builder.compile()

# 主函数


async def main():
    # 创建图实例
    graph = create_async_tools_graph()

    # 示例1：询问天气
    print("\n示例1：询问天气")
    state1 = {"messages": [HumanMessage(content="北京今天天气怎么样？")], "data": {}}
    response1 = await graph.invoke(state1)
    print(f"回答: {response1}")


# 运行主函数
if __name__ == "__main__":
    asyncio.run(main())
