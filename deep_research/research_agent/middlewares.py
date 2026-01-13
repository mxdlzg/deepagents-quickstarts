from langchain.agents.middleware.summarization import SummarizationMiddleware

class CustomSummarizationMiddleware(SummarizationMiddleware):
    """Custom Summarization Middleware with modified parameters."""

    def __init__(self, model, trigger: tuple[str, int] = ("tokens", 150000), keep: tuple[str, int] = ("messages", 8)):
        super().__init__(model=model, trigger=trigger, keep=keep)