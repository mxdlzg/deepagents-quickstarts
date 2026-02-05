from langchain_core.messages import SystemMessage
from langchain.agents.middleware.types import ModelRequest
from langchain.agents.middleware.types import AgentMiddleware

class DocMetadataMiddleware(AgentMiddleware):
    """Inject thread metadata (docid/user_id) into the system prompt."""

    def __init__(self, docid_key: str = "docId", user_id_key: str = "user_id", workspace_id_key: str = "workspaceId") -> None:
        self.docid_key = docid_key
        self.user_id_key = user_id_key
        self.workspace_id_key = workspace_id_key

    def _get_metadata(self, request: ModelRequest) -> dict:
        runtime = request.runtime
        if runtime is None:
            return {}
        config = getattr(runtime, "config", {}) or {}
        if isinstance(config, dict):
            return config.get("metadata", {}) or {}
        return getattr(config, "metadata", {}) or {}

    def _inject_system_message(self, request: ModelRequest) -> ModelRequest:
        metadata = self._get_metadata(request)
        docid = metadata.get(self.docid_key, None)
        user_id = metadata.get(self.user_id_key, None)
        workspace_id = metadata.get(self.workspace_id_key, None)
        if not docid and not user_id and not workspace_id:
            return request

        doc_context_lines = ["<thread_metadata>"]
        if user_id:
            doc_context_lines.append(f"user_id: {user_id}")
        if docid:
            doc_context_lines.append(f"docid: {docid}")
        if workspace_id:
            doc_context_lines.append(f"workspace_id: {workspace_id}")
        doc_context_lines.append("</thread_metadata>")
        doc_context = "\n".join(doc_context_lines)

        if request.system_message and doc_context in request.system_message.content:
            return request

        base_content = request.system_message.content if request.system_message else ""
        combined = f"{doc_context}\n\n{base_content}" if base_content else doc_context
        return request.override(system_message=SystemMessage(content=combined))

    def wrap_model_call(self, request: ModelRequest, handler):
        updated_request = self._inject_system_message(request)
        return handler(updated_request)

    async def awrap_model_call(self, request: ModelRequest, handler):
        updated_request = self._inject_system_message(request)
        return await handler(updated_request)
    
    async def abefore_agent(self, state, runtime):
        return await super().abefore_agent(state, runtime)
    
    async def abefore_model(self, state, runtime):
        return await super().abefore_model(state, runtime)