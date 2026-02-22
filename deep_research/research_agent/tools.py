"""Research Tools.

This module provides search and content processing utilities for the research agent,
using Tavily for URL discovery and fetching full webpage content.
"""

import os
import httpx
import json
import hashlib
import time
from urllib.parse import urlparse, urlunparse
from enum import Enum
from langchain_core.tools import InjectedToolArg, tool
from langchain.tools import ToolRuntime
from markdownify import markdownify
from tavily.tavily import TavilyClient
from typing_extensions import Annotated, Literal
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from dataclasses import dataclass

from research_agent.backend_factory import create_tenant_backend
from research_agent.memory_paths import MemoryPathManager
from research_agent.runtime_metadata import extract_metadata, require_tenant_ids, resolve_config_like

tavily_client: TavilyClient | None = None

ALB_MCP = "alb"
ALLOWED_METADATA_KEYS = {"user_id", "thread_id", "mission_id", "tenant_role", "tenant_id"}
PUBLIC_FINAL_REPORT_PATH = "/final_report.md"
PUBLIC_SOURCES_APPENDIX_PATH = "/sources_appendix.md"


class RetrievalRoute(str, Enum):
    INTERNAL = "internal_kb"
    EXTERNAL = "external_web"
    HYBRID = "hybrid"


class SourceChannel(str, Enum):
    WEB = "web"
    ALB_MCP = "alb_mcp"


def _normalize_source_channel(channel_value: str | None) -> SourceChannel:
    if channel_value is None:
        return SourceChannel.WEB

    normalized = str(channel_value).strip().lower()

    web_aliases = {
        "web",
        "web_search",
        "tavily",
        "tavily_search",
        "search",
        "external_web",
    }
    mcp_aliases = {
        "alb_mcp",
        "mcp",
        "internal_kb",
        "internal",
        "lightrag",
    }

    if normalized in web_aliases:
        return SourceChannel.WEB
    if normalized in mcp_aliases:
        return SourceChannel.ALB_MCP

    try:
        return SourceChannel(normalized)
    except ValueError:
        return SourceChannel.WEB


def get_tavily_client() -> TavilyClient:
    global tavily_client
    if tavily_client is None:
        tavily_client = TavilyClient()
    return tavily_client


def _is_transient_tavily_error(error: Exception) -> bool:
    error_name = error.__class__.__name__.lower()
    message = str(error).lower()
    return (
        "timeout" in error_name
        or "timed out" in message
        or "tempor" in message
        or "connection" in message
        or "rate limit" in message
    )


def _search_tavily_with_retry(
    query: str,
    *,
    max_results: int,
    topic: Literal["general", "news", "finance"],
    retries: int = 2,
    base_backoff_seconds: float = 1.2,
) -> dict:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return get_tavily_client().search(
                query,
                max_results=max_results,
                topic=topic,
            )
        except Exception as error:
            last_error = error
            is_last_attempt = attempt >= retries
            if is_last_attempt or not _is_transient_tavily_error(error):
                break
            time.sleep(base_backoff_seconds * (2**attempt))

    if last_error is None:
        raise RuntimeError("Tavily search failed for unknown reason")
    raise last_error


def _canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    normalized = parsed._replace(fragment="")
    return urlunparse(normalized)


def _stable_source_fingerprint(channel: SourceChannel, title: str, url: str, raw_citation: str) -> str:
    normalized_url = _canonicalize_url(url)
    base = f"{channel.value}|{title.strip().lower()}|{normalized_url}|{raw_citation.strip()}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def _new_citation_id(channel: SourceChannel, index: int) -> str:
    prefix = "WEB" if channel == SourceChannel.WEB else "MCP"
    return f"{prefix}-{index}"


def _extract_existing_max_index(ledger: dict, channel: SourceChannel) -> int:
    prefix = "WEB-" if channel == SourceChannel.WEB else "MCP-"
    max_index = 0
    for item in ledger.get("sources", []):
        citation_id = str(item.get("citation_id", ""))
        if citation_id.startswith(prefix):
            suffix = citation_id.split("-")[-1]
            if suffix.isdigit():
                max_index = max(max_index, int(suffix))
    return max_index


def _get_path_manager_from_runtime(runtime: ToolRuntime) -> MemoryPathManager:
    user_id, thread_id = require_tenant_ids(resolve_config_like(runtime))
    return MemoryPathManager(user_id=user_id, thread_id=thread_id)


def _merge_state_file_updates(runtime: ToolRuntime, files_update: dict | None) -> None:
    if not files_update:
        return
    existing_files = dict(runtime.state.get("files", {}))
    existing_files.update(files_update)
    runtime.state["files"] = existing_files


def _safe_tool_error(tool_name: str, error: Exception, **extra: object) -> str:
    payload: dict[str, object] = {
        "status": "error",
        "tool": tool_name,
        "error_type": error.__class__.__name__,
        "message": str(error),
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _upsert_text_file(runtime: ToolRuntime, file_path: str, content: str) -> str:
    backend = create_tenant_backend(runtime)

    download = backend.download_files([file_path])[0]
    if download.error == "file_not_found":
        write_result = backend.write(file_path, content)
        if write_result.error:
            raise ValueError(write_result.error)
        _merge_state_file_updates(runtime, write_result.files_update)
        return "created"

    if download.error is not None:
        raise ValueError(f"Failed to access {file_path}: {download.error}")

    previous_content = (download.content or b"").decode("utf-8")
    if previous_content == content:
        return "unchanged"

    edit_result = backend.edit(
        file_path=file_path,
        old_string=previous_content,
        new_string=content,
        replace_all=False,
    )
    if edit_result.error:
        raise ValueError(edit_result.error)
    _merge_state_file_updates(runtime, edit_result.files_update)
    return "updated"


def _read_text_file(runtime: ToolRuntime, file_path: str) -> str:
    backend = create_tenant_backend(runtime)
    download = backend.download_files([file_path])[0]

    if download.error == "file_not_found":
        return ""

    if download.error is not None:
        raise ValueError(f"Failed to access {file_path}: {download.error}")

    return (download.content or b"").decode("utf-8")


def _upsert_dual_artifact(
    runtime: ToolRuntime,
    private_path: str,
    public_path: str,
    content: str,
) -> dict:
    private_status = _upsert_text_file(runtime=runtime, file_path=private_path, content=content)
    public_status = _upsert_text_file(runtime=runtime, file_path=public_path, content=content)
    return {
        "private_path": private_path,
        "private_status": private_status,
        "public_path": public_path,
        "public_status": public_status,
    }


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[Truncated for context budget]"

@dataclass
class CustomContext:
    user_id: str = ""
    alb_mcp_token: str = ""

async def inject_user_context(
    request: MCPToolCallRequest,
    handler,
):
    # TODO:: 在后期通过用户token来调用，目前先使用静态token（admin）
    """Inject user credentials into MCP tool calls."""
    # token = request.runtime.config['metadata']['user_id']

    # Add user context to tool arguments
    # modified_request = request.override(
        # headers={"Authorization": f"Bearer {token}"}  
    # )
    metadata = extract_metadata(resolve_config_like(request.runtime))

    token = metadata.get("alb_mcp_token", "alb-sk-devtoken")
    new_args = dict(request.args or {})
    for key in ALLOWED_METADATA_KEYS:
        value = metadata.get(key)
        if value is not None:
            new_args[key] = value

    request = request.override(args=new_args, headers={"Authorization": f"Bearer {token}"})

    return await handler(request)

alb_mcp_client = MultiServerMCPClient({
    ALB_MCP: {
        "transport": "http",
        "url": os.getenv("ALB_MCP_URL", "http://localhost:3000/api/mcp"),
        "headers": {
            "Authorization": f"Bearer {os.getenv('ALB_MCP_TOKEN', 'alb_sk-xxxx')}",
        }
    }
}, tool_interceptors=[inject_user_context])
ALB_MCP_CLIENT = alb_mcp_client


def _decide_retrieval_route(
    query: str,
    need_freshness: bool,
    prefer_internal: bool,
) -> tuple[RetrievalRoute, str]:
    query_lower = query.lower()
    freshness_signals = (
        "latest",
        "today",
        "current",
        "news",
        "recent",
        "实时",
        "最新",
        "近况",
    )
    internal_signals = (
        "internal",
        "private",
        "policy",
        "playbook",
        "history",
        "内部",
        "私有",
        "制度",
        "知识库",
    )

    has_freshness_signal = any(k in query_lower for k in freshness_signals)
    has_internal_signal = any(k in query_lower for k in internal_signals)

    if prefer_internal and (not need_freshness and not has_freshness_signal):
        return RetrievalRoute.INTERNAL, "Explicit internal preference with no freshness requirement"
    if need_freshness and (not prefer_internal and not has_internal_signal):
        return RetrievalRoute.EXTERNAL, "Freshness required without private-knowledge dependency"
    if has_internal_signal and has_freshness_signal:
        return RetrievalRoute.HYBRID, "Query mixes internal-depth and external-freshness signals"
    if prefer_internal and need_freshness:
        return RetrievalRoute.HYBRID, "Both internal depth and external freshness are requested"
    if has_internal_signal:
        return RetrievalRoute.INTERNAL, "Internal/private knowledge signals detected"
    if has_freshness_signal:
        return RetrievalRoute.EXTERNAL, "Freshness/news signals detected"
    return RetrievalRoute.HYBRID, "Default to hybrid retrieval for balanced depth and coverage"


@tool(parse_docstring=True)
def route_research(
    query: str,
    need_freshness: bool = False,
    prefer_internal: bool = False,
) -> str:
    """Select retrieval route between ALB_MCP, web search, or hybrid.

    Args:
        query: User research question or subtask.
        need_freshness: Whether the question explicitly requires latest information.
        prefer_internal: Whether private/internal knowledge is prioritized.

    Returns:
        Routing recommendation with route, rationale, and execution plan.
    """
    try:
        route, reason = _decide_retrieval_route(
            query=query,
            need_freshness=need_freshness,
            prefer_internal=prefer_internal,
        )

        if route == RetrievalRoute.INTERNAL:
            execution_plan = [
                "Call ALB_MCP tool(s) first for internal and historical depth",
                "Preserve citation/source fields from ALB response",
            ]
        elif route == RetrievalRoute.EXTERNAL:
            execution_plan = [
                "Call tavily_search for external and latest context",
                "Use returned web citations in the draft",
            ]
        else:
            execution_plan = [
                "Call ALB_MCP tools for internal depth and private context",
                "Call tavily_search for latest external updates",
                "Merge evidence and keep citations from both channels",
            ]

        return json.dumps(
            {
                "status": "ok",
                "route": route.value,
                "reason": reason,
                "execution_plan": execution_plan,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as error:
        return _safe_tool_error("route_research", error)


@tool(parse_docstring=True)
def request_plan_approval(plan_markdown: str) -> str:
    """Create an explicit pause point for human approval of the research plan.

    Args:
        plan_markdown: Detailed outline and execution plan that requires user confirmation.

    Returns:
        Approval gate message used for HITL interruption.
    """
    return (
        "Plan approval checkpoint reached. Please review and approve or revise before execution.\n\n"
        + plan_markdown
    )


@tool(parse_docstring=True)
def build_citation_ledger(
    evidence_json: str,
    existing_ledger_json: str = "",
) -> str:
    """Build or update a structured citation ledger with deduplication.

    Expected `evidence_json` format:
    {
      "evidence": [
        {
          "channel": "web" | "alb_mcp",
          "title": "...",
          "url": "...",
          "section": "2.1 Market Overview",
          "raw_citation": "optional raw citation marker from MCP",
          "snippet": "optional supporting snippet"
        }
      ]
    }

    Expected `existing_ledger_json` format:
    {
      "sources": [...],
      "by_fingerprint": {...}
    }

    Args:
        evidence_json: JSON payload for new evidence items.
        existing_ledger_json: Previous ledger JSON string to update in-place.

    Returns:
        Updated ledger JSON with source IDs and section mappings.
    """
    try:
        payload = json.loads(evidence_json or "{}")
        evidence_items = payload.get("evidence", [])

        if existing_ledger_json.strip():
            ledger = json.loads(existing_ledger_json)
        else:
            ledger = {"sources": [], "by_fingerprint": {}, "section_map": {}}

        web_index = _extract_existing_max_index(ledger, SourceChannel.WEB)
        mcp_index = _extract_existing_max_index(ledger, SourceChannel.ALB_MCP)

        for item in evidence_items:
            channel = _normalize_source_channel(item.get("channel"))
            title = str(item.get("title", "Untitled Source"))
            url = str(item.get("url", ""))
            section = str(item.get("section", "General"))
            raw_citation = str(item.get("raw_citation", ""))
            snippet = str(item.get("snippet", ""))

            fingerprint = _stable_source_fingerprint(channel, title, url, raw_citation)
            existing_id = ledger.get("by_fingerprint", {}).get(fingerprint)

            if existing_id is None:
                if channel == SourceChannel.WEB:
                    web_index += 1
                    citation_id = _new_citation_id(channel, web_index)
                else:
                    mcp_index += 1
                    citation_id = _new_citation_id(channel, mcp_index)

                source_item = {
                    "citation_id": citation_id,
                    "channel": channel.value,
                    "title": title,
                    "url": _canonicalize_url(url),
                    "raw_citation": raw_citation,
                    "snippets": [snippet] if snippet else [],
                }
                ledger["sources"].append(source_item)
                ledger["by_fingerprint"][fingerprint] = citation_id
            else:
                citation_id = existing_id
                for source in ledger.get("sources", []):
                    if source.get("citation_id") == citation_id and snippet:
                        snippets = source.setdefault("snippets", [])
                        if snippet not in snippets:
                            snippets.append(snippet)

            section_refs = ledger.setdefault("section_map", {}).setdefault(section, [])
            if citation_id not in section_refs:
                section_refs.append(citation_id)

        return json.dumps(ledger, ensure_ascii=False, indent=2)
    except Exception as error:
        return _safe_tool_error("build_citation_ledger", error)


@tool(parse_docstring=True)
def render_sources_from_ledger(
    ledger_json: str,
    section: str = "",
) -> str:
    """Render markdown source index from a structured citation ledger.

    Args:
        ledger_json: Citation ledger returned by build_citation_ledger.
        section: Optional section name. If provided, only render sources used in that section.

    Returns:
        Markdown-formatted source list.
    """
    try:
        ledger = json.loads(ledger_json or "{}")
        all_sources = ledger.get("sources", [])

        if section:
            target_ids = set(ledger.get("section_map", {}).get(section, []))
            sources = [source for source in all_sources if source.get("citation_id") in target_ids]
        else:
            sources = all_sources

        lines = ["### Sources"]
        for source in sources:
            citation_id = source.get("citation_id", "UNK")
            title = source.get("title", "Untitled Source")
            url = source.get("url", "")
            channel = source.get("channel", "unknown")
            raw_citation = source.get("raw_citation", "")
            suffix = f" | raw_citation={raw_citation}" if raw_citation else ""
            lines.append(f"[{citation_id}] ({channel}) {title}: {url}{suffix}")

        if len(lines) == 1:
            lines.append("(No sources)")

        return "\n".join(lines)
    except Exception as error:
        return (
            "### Sources\n"
            + f"[WARN] render_sources_from_ledger degraded: {error.__class__.__name__}: {error}\n"
            + "(No sources)"
        )


@tool(parse_docstring=True)
def mission_storage_manifest(runtime: ToolRuntime) -> str:
    """Return canonical tenant-isolated artifact paths for current mission.

    Args:
        runtime: Injected tool runtime.

    Returns:
        JSON with canonical mission/user scoped storage paths.
    """
    try:
        path_manager = _get_path_manager_from_runtime(runtime)
        payload = {
            "status": "ok",
            "canonical_delivery_root": "/",
            "user_profile_preferences": str(path_manager.user_profile_preferences()),
            "thread_root": str(path_manager.thread_root()),
            "mission_root": str(path_manager.mission_root()),
            "raw_materials_dir": str(path_manager.raw_materials_dir()),
            "knowledge_graph_dir": str(path_manager.knowledge_graph_dir()),
            "drafts_dir": str(path_manager.drafts_dir()),
            "citation_ledger_path": path_manager.thread_path("knowledge_graph", "citation_ledger.json"),
            "sources_appendix_private_path": path_manager.thread_path("drafts", "sources_appendix.md"),
            "sources_appendix_public_path": PUBLIC_SOURCES_APPENDIX_PATH,
            "final_report_private_path": path_manager.thread_path("drafts", "final_report.md"),
            "final_report_public_path": PUBLIC_FINAL_REPORT_PATH,
            "final_report_path": PUBLIC_FINAL_REPORT_PATH,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as error:
        return _safe_tool_error("mission_storage_manifest", error)


@tool(parse_docstring=True)
def persist_citation_ledger(
    ledger_json: str,
    runtime: ToolRuntime,
) -> str:
    """Persist citation ledger into tenant-isolated mission knowledge graph path.

    Args:
        ledger_json: Ledger JSON generated by build_citation_ledger.
        runtime: Injected tool runtime.

    Returns:
        Status message with storage path.
    """
    try:
        path_manager = _get_path_manager_from_runtime(runtime)
        ledger_path = path_manager.thread_path("knowledge_graph", "citation_ledger.json")
        status = _upsert_text_file(runtime=runtime, file_path=ledger_path, content=ledger_json)
        return f"Citation ledger {status}: {ledger_path}"
    except Exception as error:
        return _safe_tool_error("persist_citation_ledger", error)


@tool(parse_docstring=True)
def persist_sources_appendix(
    sources_markdown: str,
    runtime: ToolRuntime,
) -> str:
    """Persist rendered source appendix into tenant-isolated mission drafts path.

    Args:
        sources_markdown: Markdown returned by render_sources_from_ledger.
        runtime: Injected tool runtime.

    Returns:
        Status message with storage path.
    """
    try:
        path_manager = _get_path_manager_from_runtime(runtime)
        private_sources_path = path_manager.thread_path("drafts", "sources_appendix.md")
        dual_status = _upsert_dual_artifact(
            runtime=runtime,
            private_path=private_sources_path,
            public_path=PUBLIC_SOURCES_APPENDIX_PATH,
            content=sources_markdown,
        )
        return json.dumps(
            {
                "status": "ok",
                "artifact": "sources_appendix",
                **dual_status,
                "delivery_path": PUBLIC_SOURCES_APPENDIX_PATH,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as error:
        return _safe_tool_error("persist_sources_appendix", error)


@tool(parse_docstring=True)
def finalize_mission_report(
    report_body_markdown: str,
    runtime: ToolRuntime,
    appendix_markdown: str = "",
) -> str:
    """Compose and persist the mission final report with sources appendix.

    If `appendix_markdown` is empty, this tool attempts to load
    `/drafts/sources_appendix.md` from the current mission scope.

    Args:
        report_body_markdown: Main report content.
        appendix_markdown: Optional appendix markdown content.
        runtime: Injected tool runtime.

    Returns:
        Status message with final report path.
    """
    try:
        path_manager = _get_path_manager_from_runtime(runtime)
        appendix_path = path_manager.thread_path("drafts", "sources_appendix.md")
        private_final_report_path = path_manager.thread_path("drafts", "final_report.md")

        appendix = appendix_markdown.strip()
        if not appendix:
            appendix = _read_text_file(runtime=runtime, file_path=appendix_path).strip()

        if appendix:
            composed = f"{report_body_markdown.rstrip()}\n\n---\n\n{appendix}\n"
        else:
            composed = report_body_markdown.rstrip() + "\n"

        dual_status = _upsert_dual_artifact(
            runtime=runtime,
            private_path=private_final_report_path,
            public_path=PUBLIC_FINAL_REPORT_PATH,
            content=composed,
        )
        return json.dumps(
            {
                "status": "ok",
                "artifact": "final_report",
                **dual_status,
                "delivery_path": PUBLIC_FINAL_REPORT_PATH,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as error:
        return _safe_tool_error("finalize_mission_report", error)


def _has_sources_section(markdown: str) -> bool:
    lowered = markdown.lower()
    return "### sources" in lowered or "## sources" in lowered


def _extract_inline_citation_ids(markdown: str) -> set[str]:
    import re

    # Matches [WEB-1], [MCP-2], [1], [2], etc.
    return set(re.findall(r"\[([A-Za-z]+-\d+|\d+)\]", markdown))


def _extract_sources_section_ids(markdown: str) -> set[str]:
    import re

    lines = markdown.splitlines()
    start_idx = -1
    for idx, line in enumerate(lines):
        if line.strip().lower() in {"### sources", "## sources"}:
            start_idx = idx
            break

    if start_idx < 0:
        return set()

    section_text = "\n".join(lines[start_idx + 1 :])
    return set(re.findall(r"\[([A-Za-z]+-\d+|\d+)\]", section_text))


@tool(parse_docstring=True)
def verify_and_repair_final_report(runtime: ToolRuntime) -> str:
    """Verify final report citation completeness and auto-repair missing Sources section.

    Repair policy:
    - If report has no Sources section, auto-append one from persisted ledger.
    - If report has Sources section but missing citation IDs referenced inline,
      append full ledger-based Sources section to restore traceability.

    Args:
        runtime: Injected tool runtime.

    Returns:
        JSON status including pass/fail fields and any repairs applied.
    """
    try:
        path_manager = _get_path_manager_from_runtime(runtime)
        ledger_path = path_manager.thread_path("knowledge_graph", "citation_ledger.json")
        private_final_report_path = path_manager.thread_path("drafts", "final_report.md")
        final_report_path = PUBLIC_FINAL_REPORT_PATH

        report_text = _read_text_file(runtime=runtime, file_path=final_report_path)
        if not report_text.strip():
            report_text = _read_text_file(runtime=runtime, file_path=private_final_report_path)
        if not report_text.strip():
            return json.dumps(
                {
                    "status": "fail",
                    "reason": "final report is empty or missing",
                    "final_report_path": final_report_path,
                },
                ensure_ascii=False,
                indent=2,
            )

        inline_ids = _extract_inline_citation_ids(report_text)
        sources_ids = _extract_sources_section_ids(report_text)
        has_sources = _has_sources_section(report_text)

        repaired = False
        repair_notes: list[str] = []

        ledger_json = _read_text_file(runtime=runtime, file_path=ledger_path)
        if ledger_json.strip():
            full_sources_markdown = render_sources_from_ledger.func(ledger_json=ledger_json, section="")
        else:
            full_sources_markdown = "### Sources\n(No sources)"

        if not has_sources:
            report_text = report_text.rstrip() + "\n\n---\n\n" + full_sources_markdown + "\n"
            repaired = True
            repair_notes.append("appended missing Sources section from ledger")
        else:
            missing_ids = inline_ids - sources_ids
            if missing_ids:
                report_text = report_text.rstrip() + "\n\n---\n\n" + full_sources_markdown + "\n"
                repaired = True
                repair_notes.append(
                    f"sources section missing citation ids: {sorted(missing_ids)}; appended full ledger sources"
                )

        if repaired:
            _upsert_dual_artifact(
                runtime=runtime,
                private_path=private_final_report_path,
                public_path=PUBLIC_FINAL_REPORT_PATH,
                content=report_text,
            )

        final_inline_ids = _extract_inline_citation_ids(report_text)
        final_sources_ids = _extract_sources_section_ids(report_text)
        final_has_sources = _has_sources_section(report_text)
        unmatched = sorted(final_inline_ids - final_sources_ids)

        status = "pass" if final_has_sources and not unmatched else "fail"
        return json.dumps(
            {
                "status": status,
                "repaired": repaired,
                "notes": repair_notes,
                "final_report_path": final_report_path,
                "final_report_private_path": private_final_report_path,
                "unmatched_inline_citations": unmatched,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as error:
        return _safe_tool_error("verify_and_repair_final_report", error)


@tool(parse_docstring=True)
def publish_final_report(
    report_body_markdown: str,
    runtime: ToolRuntime,
    appendix_markdown: str = "",
) -> str:
    """Finalize report and enforce verification gate in one atomic tool call.

    This tool is the preferred workflow endpoint to avoid skipping validation.

    Args:
        report_body_markdown: Main report body markdown.
        runtime: Injected tool runtime.
        appendix_markdown: Optional sources appendix markdown.

    Returns:
        JSON payload with finalize result, verify result, and final status.
    """
    try:
        finalize_result = finalize_mission_report.func(
            report_body_markdown=report_body_markdown,
            runtime=runtime,
            appendix_markdown=appendix_markdown,
        )
        verify_result = verify_and_repair_final_report.func(runtime=runtime)

        verify_status = "fail"
        verify_payload: dict | str
        try:
            verify_payload = json.loads(verify_result)
            verify_status = str(verify_payload.get("status", "fail")).lower()
        except Exception:
            verify_payload = verify_result

        return json.dumps(
            {
                "status": "pass" if verify_status == "pass" else "fail",
                "finalize": finalize_result,
                "verify": verify_payload,
                "delivery_path": PUBLIC_FINAL_REPORT_PATH,
                "next_action": "complete" if verify_status == "pass" else "repair_and_retry_publish",
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as error:
        return _safe_tool_error("publish_final_report", error)


def fetch_webpage_content(url: str, timeout: float = 10.0, max_chars: int = 6000) -> str:
    """Fetch and convert webpage content to markdown.

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds

    Returns:
        Webpage content as markdown
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        content = markdownify(response.text)
        return _truncate_text(content, max_chars=max_chars)
    except Exception as e:
        return f"Error fetching content from {url}: {str(e)}"


@tool(parse_docstring=True)
def tavily_search(
    query: str,
    max_results: Annotated[int, InjectedToolArg] = 5,
    max_chars_per_result: Annotated[int, InjectedToolArg] = 5000,
    max_total_chars: Annotated[int, InjectedToolArg] = 20000,
    topic: Annotated[
        Literal["general", "news", "finance"], InjectedToolArg
    ] = "general",
) -> str:
    """Search the web for information on a given query.

    Uses Tavily to discover relevant URLs, then fetches and returns full webpage content as markdown.

    Args:
        query: Search query to execute
        max_results: Maximum number of results to return (default: 1)
        max_chars_per_result: Maximum characters kept per fetched page (default: 5000)
        max_total_chars: Maximum total characters kept across all results (default: 20000)
        topic: Topic filter - 'general', 'news', or 'finance' (default: 'general')

    Returns:
        Formatted search results with full webpage content
    """
    search_error: str | None = None
    try:
        search_results = _search_tavily_with_retry(
            query,
            max_results=max_results,
            topic=topic,
        )
    except Exception as error:
        search_results = {"results": []}
        search_error = str(error)

    # Fetch full content for each URL
    result_texts = []
    source_lines = []
    accumulated_chars = 0
    for result in search_results.get("results", []):
        url = str(result.get("url", "")).strip()
        title = str(result.get("title", "Untitled Source")).strip() or "Untitled Source"
        if not url:
            continue
        citation_id = f"WEB-{len(source_lines) + 1}"

        # Fetch webpage content
        remaining_budget = max_total_chars - accumulated_chars
        if remaining_budget <= 0:
            break

        effective_max_chars = min(max_chars_per_result, remaining_budget)
        content = fetch_webpage_content(url, max_chars=effective_max_chars)
        accumulated_chars += len(content)

        result_text = f"""## {title} [{citation_id}]
**URL:** {url}

{content}

---
"""
        result_texts.append(result_text)
        source_lines.append(f"[{citation_id}] {title}: {url}")

    if len(search_results.get("results", [])) > len(source_lines):
        source_lines.append("[INFO] Additional results omitted due to context budget.")

    if search_error:
        source_lines.append(f"[WARN] Tavily search degraded: {search_error}")

    # Format final response
    response = f"""🔍 Found {len(result_texts)} result(s) for '{query}':

{chr(10).join(result_texts)}

### Sources
{chr(10).join(source_lines)}"""

    return response


@tool(parse_docstring=True)
def think_tool(reflection: str) -> str:
    """Tool for strategic reflection on research progress and decision-making.

    Use this tool after each search to analyze results and plan next steps systematically.
    This creates a deliberate pause in the research workflow for quality decision-making.

    When to use:
    - After receiving search results: What key information did I find?
    - Before deciding next steps: Do I have enough to answer comprehensively?
    - When assessing research gaps: What specific information am I still missing?
    - Before concluding research: Can I provide a complete answer now?

    Reflection should address:
    1. Analysis of current findings - What concrete information have I gathered?
    2. Gap assessment - What crucial information is still missing?
    3. Quality evaluation - Do I have sufficient evidence/examples for a good answer?
    4. Strategic decision - Should I continue searching or provide my answer?

    Args:
        reflection: Your detailed reflection on research progress, findings, gaps, and next steps

    Returns:
        Confirmation that reflection was recorded for decision-making
    """
    return f"Reflection recorded: {reflection}"
