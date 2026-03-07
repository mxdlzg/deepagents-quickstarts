"""Utility functions for displaying messages and prompts in Jupyter notebooks."""

from contextvars import ContextVar
from functools import wraps
import inspect
import json

from langgraph.config import get_stream_writer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()
_CURRENT_TOOL_EVENT_SOURCE: ContextVar[str] = ContextVar("deep_research_tool_event_source", default="unknown")


def _get_stream_writer():
    """Get LangGraph custom stream writer when available."""
    try:
        return get_stream_writer()
    except Exception:
        return None


def _emit_custom(event: dict[str, object]) -> None:
    """Emit custom stream events; no-op if runtime does not support it."""
    writer = _get_stream_writer()
    if writer is None:
        return
    try:
        writer(event)
    except Exception:
        return


def _infer_tool_event_source() -> str:
    tool_name = _CURRENT_TOOL_EVENT_SOURCE.get()
    if tool_name != "unknown":
        return tool_name

    for frame_info in inspect.stack()[2:]:
        candidate = frame_info.function
        if candidate.startswith("_"):
            continue
        return candidate
    return "unknown"


def bind_tool_event_source(func):
    """Bind emitted events to the current tool function without passing tool names manually."""
    if inspect.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            token = _CURRENT_TOOL_EVENT_SOURCE.set(func.__name__)
            try:
                return await func(*args, **kwargs)
            finally:
                _CURRENT_TOOL_EVENT_SOURCE.reset(token)

        return async_wrapper

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        token = _CURRENT_TOOL_EVENT_SOURCE.set(func.__name__)
        try:
            return func(*args, **kwargs)
        finally:
            _CURRENT_TOOL_EVENT_SOURCE.reset(token)

    return sync_wrapper


def emit_tool_event(event_type: str, content: str = "", **data: object) -> None:
    """Emit a normalized tool event with stable top-level fields and text in content."""
    event: dict[str, object] = {
        "type": "tool_event",
        "event_type": event_type,
        "tool": _infer_tool_event_source(),
        "content": content,
        "data": data,
    }
    _emit_custom(event)


def format_message_content(message):
    """Convert message content to displayable string."""
    parts = []
    tool_calls_processed = False

    # Handle main content
    if isinstance(message.content, str):
        parts.append(message.content)
    elif isinstance(message.content, list):
        # Handle complex content like tool calls (Anthropic format)
        for item in message.content:
            if item.get("type") == "text":
                parts.append(item["text"])
            elif item.get("type") == "tool_use":
                parts.append(f"\n🔧 Tool Call: {item['name']}")
                parts.append(f"   Args: {json.dumps(item['input'], indent=2)}")
                parts.append(f"   ID: {item.get('id', 'N/A')}")
                tool_calls_processed = True
    else:
        parts.append(str(message.content))

    # Handle tool calls attached to the message (OpenAI format) - only if not already processed
    if (
        not tool_calls_processed
        and hasattr(message, "tool_calls")
        and message.tool_calls
    ):
        for tool_call in message.tool_calls:
            parts.append(f"\n🔧 Tool Call: {tool_call['name']}")
            parts.append(f"   Args: {json.dumps(tool_call['args'], indent=2)}")
            parts.append(f"   ID: {tool_call['id']}")

    return "\n".join(parts)


def format_messages(messages):
    """Format and display a list of messages with Rich formatting."""
    for m in messages:
        msg_type = m.__class__.__name__.replace("Message", "")
        content = format_message_content(m)

        if msg_type == "Human":
            console.print(Panel(content, title="🧑 Human", border_style="blue"))
        elif msg_type == "Ai":
            console.print(Panel(content, title="🤖 Assistant", border_style="green"))
        elif msg_type == "Tool":
            console.print(Panel(content, title="🔧 Tool Output", border_style="yellow"))
        else:
            console.print(Panel(content, title=f"📝 {msg_type}", border_style="white"))


def format_message(messages):
    """Alias for format_messages for backward compatibility."""
    return format_messages(messages)


def show_prompt(prompt_text: str, title: str = "Prompt", border_style: str = "blue"):
    """Display a prompt with rich formatting and XML tag highlighting.

    Args:
        prompt_text: The prompt string to display
        title: Title for the panel (default: "Prompt")
        border_style: Border color style (default: "blue")
    """
    # Create a formatted display of the prompt
    formatted_text = Text(prompt_text)
    formatted_text.highlight_regex(r"<[^>]+>", style="bold blue")  # Highlight XML tags
    formatted_text.highlight_regex(
        r"##[^#\n]+", style="bold magenta"
    )  # Highlight headers
    formatted_text.highlight_regex(
        r"###[^#\n]+", style="bold cyan"
    )  # Highlight sub-headers

    # Display in a panel for better presentation
    console.print(
        Panel(
            formatted_text,
            title=f"[bold green]{title}[/bold green]",
            border_style=border_style,
            padding=(1, 2),
        )
    )
