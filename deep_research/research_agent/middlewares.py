from typing import Any, Optional
from deepagents import MemoryMiddleware
from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain_core.runnables import RunnableConfig

from research_agent.memory_paths import MemoryPathManager
from research_agent.runtime_metadata import require_tenant_ids

class CustomSummarizationMiddleware(SummarizationMiddleware):
    """Custom Summarization Middleware with modified parameters."""

    def __init__(self, model, trigger: tuple[str, int] = ("tokens", 150000), keep: tuple[str, int] = ("messages", 8)):
        super().__init__(model=model, trigger=trigger, keep=keep)

    def before_model(self, state, runtime):
        return super().before_model(state, runtime)


class CustomMemoryMiddleware(MemoryMiddleware):
    """Custom Memory Middleware with extended functionality."""

    def __init__(self, backend, sources: Optional[list[str]] = None):
        super().__init__(backend=backend, sources=sources)

    @staticmethod
    def _path_manager_from_config(config: RunnableConfig | dict[str, Any]) -> MemoryPathManager:
        user_id, mission_id = require_tenant_ids(config)
        return MemoryPathManager(user_id=user_id, mission_id=mission_id)

    def before_agent(self, state, runtime, config):
        path_manager = self._path_manager_from_config(config)
        self.sources = [str(path_manager.user_profile_preferences())]
        return super().before_agent(state, runtime, config)
    
    # def wrap_tool_call(self, request, handler):
    #     print("CustomMemoryMiddleware: wrap_tool_call called")
    #     return super().wrap_tool_call(request, handler)

    # async def awrap_tool_call(self, request, handler):
        # 1. 执行前逻辑：例如修改参数
        # print(f"正在调用工具: {request}")
        
        # # 2. 调用原始处理器执行工具
        # try:
        #     response = await handler(request)
        # except Exception as e:
        #     # 3. 错误处理：可以在此处进行重试或返回自定义错误消息
        #     from langchain.messages import ToolMessage
        #     return ToolMessage(content=f"工具执行失败: {str(e)}", tool_call_id=request.tool_id)

        # # 4. 执行后逻辑：例如修改返回结果
        # if "sensitive_data" in response.content:
        #     response.content = response.content.replace("sensitive_data", "***")
            
        # return response

    # Add any custom methods or overrides as needed