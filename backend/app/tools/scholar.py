"""Scholarly search through OpenAlex, an open index of research papers.

OpenAlex needs no key (``SW_OPENALEX_API_KEY`` is optional and sent as a
Bearer token). Each result is a paper with its authors, year, venue, DOI,
citation count and the start of its abstract, so the assistant can cite
real literature instead of whatever a general web search returns.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable

from workflow_core import ToolDefinition

from .research import SearchError, _http

OPENALEX_WORKS = "https://api.openalex.org/works"
ScholarFunction = Callable[..., list[dict]]
MAX_AUTHORS = 5
ABSTRACT_CHARS = 600
SORTS = {"relevance": None, "cited": "cited_by_count:desc", "recent": "publication_date:desc"}
# OpenAlex allows a few requests per second; space them out across the process.
MIN_INTERVAL_SECONDS = 0.2
# After the daily budget is used up, stop asking for this long (it resets daily).
QUOTA_PAUSE_SECONDS = 3600
MAX_RETRY_AFTER_SECONDS = 5
QUOTA_MESSAGE = (
    "OpenAlex refused the request: the free daily budget {who} is used up (HTTP 429). "
    "Do not call scholar_search again in this task. Tell the user {fix}, and continue "
    "with web_search or the sources found so far."
)

SCHOLAR_SEARCH = ToolDefinition(
    "scholar.search",
    "low",
    description=(
        "Search academic literature (OpenAlex): papers with authors, year, venue, DOI, "
        "citation count and the start of the abstract. Use it to find sources for "
        "surveys and literature reviews. Searches titles, abstracts and full text; "
        "English keywords find the most."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 300},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 25},
            "from_year": {"type": "integer", "minimum": 1800, "maximum": 2100},
            "to_year": {"type": "integer", "minimum": 1800, "maximum": 2100},
            "sort": {"enum": sorted(SORTS)},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)


def _abstract(inverted: dict | None) -> str:
    """OpenAlex stores abstracts as word -> positions; rebuild the text."""
    if not inverted:
        return ""
    words = sorted(
        (position, word) for word, positions in inverted.items() for position in positions
    )
    text = " ".join(word for _, word in words)
    return text if len(text) <= ABSTRACT_CHARS else text[: ABSTRACT_CHARS - 1] + "…"


def _paper(work: dict) -> dict:
    names = [
        ((authorship.get("author") or {}).get("display_name") or "").strip()
        for authorship in work.get("authorships") or []
    ]
    names = [name for name in names if name]
    location = work.get("primary_location") or {}
    doi = work.get("doi")
    return {
        "title": (work.get("display_name") or work.get("title") or "").strip(),
        "authors": names[:MAX_AUTHORS] + (["et al."] if len(names) > MAX_AUTHORS else []),
        "year": work.get("publication_year"),
        "venue": ((location.get("source") or {}).get("display_name")) or None,
        "doi": doi,
        "url": doi or location.get("landing_page_url") or work.get("id"),
        "open_access_url": (work.get("open_access") or {}).get("oa_url"),
        "cited_by": work.get("cited_by_count"),
        "abstract": _abstract(work.get("abstract_inverted_index")),
    }


class _Pacer:
    """Spacing between requests, and pauses after a budget ran out (process-wide).

    Budgets are per API key (and one shared budget without a key), so a pause
    applies only to the key whose budget is used up.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.last = 0.0
        self.paused: dict[str, tuple[float, str]] = {}  # budget -> (until, message)

    def wait(self) -> None:
        with self.lock:
            delay = self.last + MIN_INTERVAL_SECONDS - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.last = time.monotonic()


PACER = _Pacer()


def _budget(api_key: str | None) -> str:
    if not api_key:
        return "shared"
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def openalex_search(default_key: str | None = None) -> ScholarFunction:
    """OpenAlex search. A key passed per call (``api_key``) replaces ``default_key``."""

    def request(params, headers, api_key):
        budget = _budget(api_key)
        until, message = PACER.paused.get(budget, (0.0, ""))
        if time.monotonic() < until:
            raise SearchError(message)
        for attempt in range(2):
            PACER.wait()
            try:
                return _http("GET", OPENALEX_WORKS, api_key or "", params=params, headers=headers)
            except SearchError as exc:
                if exc.status != 429:
                    raise
                if "budget" in exc.body.lower() or "daily" in exc.body.lower():
                    if api_key:
                        who = "of this API key"
                        fix = "that the OpenAlex key's budget ran out for today"
                    else:
                        who = (
                            "shared by everyone on this server's IP address "
                            "(no API key is configured)"
                        )
                        fix = (
                            "to get a free OpenAlex API key and enter it in the settings "
                            "panel or set SW_OPENALEX_API_KEY"
                        )
                    message = QUOTA_MESSAGE.format(who=who, fix=fix)
                    PACER.paused[budget] = (time.monotonic() + QUOTA_PAUSE_SECONDS, message)
                    raise SearchError(message) from None
                if attempt == 1:
                    raise SearchError(
                        "OpenAlex is limiting requests (HTTP 429). Wait before searching again."
                    ) from None
                time.sleep(min(exc.retry_after or 1.0, MAX_RETRY_AFTER_SECONDS))
        raise AssertionError("unreachable")

    def search(query, max_results=10, from_year=None, to_year=None, sort="relevance", api_key=None):
        api_key = api_key or default_key
        params = {"search": query, "per-page": max_results}
        filters = []
        if from_year:
            filters.append(f"from_publication_date:{from_year}-01-01")
        if to_year:
            filters.append(f"to_publication_date:{to_year}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        if SORTS.get(sort):
            params["sort"] = SORTS[sort]
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        data = request(params, headers, api_key)
        return [_paper(work) for work in data.get("results") or []]

    return search


def scholar_search_handler(search: ScholarFunction, key_for: Callable | None = None):
    """``key_for(context)`` returns the request's own OpenAlex key, if the user gave one."""

    def handler(context, tool, args):
        options = {}
        key = key_for(context) if key_for is not None else None
        if key:
            options["api_key"] = key
        if args.get("from_year") and args.get("to_year") and args["from_year"] > args["to_year"]:
            raise ValueError("from_year must not be after to_year.")
        papers = search(
            args["query"].strip(),
            max_results=args.get("max_results", 10),
            from_year=args.get("from_year"),
            to_year=args.get("to_year"),
            sort=args.get("sort", "relevance"),
            **options,
        )
        reply = {"query": args["query"], "source": "openalex", "papers": papers}
        if not papers:
            reply["note"] = "No papers found. Try English keywords or a broader query."
        return reply

    return handler
