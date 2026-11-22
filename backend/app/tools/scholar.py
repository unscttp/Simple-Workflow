"""Scholarly search through OpenAlex, an open index of research papers.

OpenAlex needs no key (``SW_OPENALEX_API_KEY`` is optional and sent as a
Bearer token). Each result is a paper with its authors, year, venue, DOI,
citation count and the start of its abstract, so the assistant can cite
real literature instead of whatever a general web search returns.
"""

from __future__ import annotations

from collections.abc import Callable

from workflow_core import ToolDefinition

from .research import _http

OPENALEX_WORKS = "https://api.openalex.org/works"
ScholarFunction = Callable[..., list[dict]]
MAX_AUTHORS = 5
ABSTRACT_CHARS = 600
SORTS = {"relevance": None, "cited": "cited_by_count:desc", "recent": "publication_date:desc"}

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


def openalex_search(api_key: str | None = None) -> ScholarFunction:
    def search(query, max_results=10, from_year=None, to_year=None, sort="relevance"):
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
        data = _http("GET", OPENALEX_WORKS, api_key or "", params=params, headers=headers)
        return [_paper(work) for work in data.get("results") or []]

    return search


def scholar_search_handler(search: ScholarFunction):
    def handler(context, tool, args):
        if args.get("from_year") and args.get("to_year") and args["from_year"] > args["to_year"]:
            raise ValueError("from_year must not be after to_year.")
        papers = search(
            args["query"].strip(),
            max_results=args.get("max_results", 10),
            from_year=args.get("from_year"),
            to_year=args.get("to_year"),
            sort=args.get("sort", "relevance"),
        )
        reply = {"query": args["query"], "source": "openalex", "papers": papers}
        if not papers:
            reply["note"] = "No papers found. Try English keywords or a broader query."
        return reply

    return handler
