"""Web search backends and scholarly search, against mocked HTTP."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest import mock

import httpx

from app.settings import Settings
from app.tools.research import (
    EMPTY_NOTE,
    SearchError,
    brave_search,
    duckduckgo_search,
    search_backend,
    searxng_search,
    tavily_search,
    web_search_handler,
)
from app.tools.scholar import _abstract, openalex_search, scholar_search_handler

from .support import ApiCase, answer, call, last_result


def response(status=200, payload=None, text=None):
    request = httpx.Request("GET", "https://example.com")
    if text is not None:
        return httpx.Response(status, request=request, text=text)
    return httpx.Response(status, request=request, json=payload)


class BackendTests(unittest.TestCase):
    def test_choice_follows_configuration(self):
        base = Settings()
        self.assertEqual(search_backend(base)[0], "duckduckgo")
        self.assertEqual(search_backend(replace(base, brave_api_key="b"))[0], "brave")
        self.assertEqual(
            search_backend(replace(base, brave_api_key="b", tavily_api_key="t"))[0], "tavily"
        )
        self.assertEqual(search_backend(replace(base, searxng_url="http://s"))[0], "searxng")
        self.assertEqual(
            search_backend(replace(base, search_provider="duckduckgo", brave_api_key="b"))[0],
            "duckduckgo",
        )
        with self.assertRaises(ValueError):
            search_backend(replace(base, search_provider="brave"))  # no key
        with self.assertRaises(ValueError):
            search_backend(replace(base, search_provider="bing"))

    def test_duckduckgo_is_asked_explicitly(self):
        with mock.patch("ddgs.DDGS") as ddgs:
            ddgs.return_value.text.return_value = [
                {"title": "Annales school", "href": "https://en.wikipedia.org/x", "body": "French"}
            ]
            hits = duckduckgo_search("Annales school", 3)
            self.assertEqual(
                ddgs.return_value.text.call_args.kwargs,
                {"region": "wt-wt", "max_results": 3, "backend": "duckduckgo,wikipedia"},
            )
            duckduckgo_search("年鉴学派", 3)
            self.assertEqual(ddgs.return_value.text.call_args.kwargs["region"], "cn-zh")
        self.assertEqual(
            hits[0],
            {"title": "Annales school", "url": "https://en.wikipedia.org/x", "snippet": "French"},
        )

    def test_brave_tavily_and_searxng_requests(self):
        with mock.patch("httpx.request") as request:
            request.return_value = response(
                payload={
                    "web": {"results": [{"title": "T", "url": "https://a", "description": "D"}]}
                }
            )
            self.assertEqual(brave_search("BSA-key")("q", 5)[0]["snippet"], "D")
            args = request.call_args
            self.assertEqual(args.args, ("GET", "https://api.search.brave.com/res/v1/web/search"))
            self.assertEqual(args.kwargs["params"], {"q": "q", "count": 5})
            self.assertEqual(args.kwargs["headers"]["X-Subscription-Token"], "BSA-key")

            request.return_value = response(
                payload={"results": [{"title": "T", "url": "https://b", "content": "C"}]}
            )
            self.assertEqual(tavily_search("tvly-key")("q", 4)[0]["url"], "https://b")
            args = request.call_args
            self.assertEqual(args.args, ("POST", "https://api.tavily.com/search"))
            self.assertEqual(args.kwargs["json"], {"query": "q", "max_results": 4})
            self.assertEqual(args.kwargs["headers"]["Authorization"], "Bearer tvly-key")

            request.return_value = response(payload={"results": [{"title": "S"}] * 9})
            self.assertEqual(len(searxng_search("http://searx/")("q", 3)), 3)
            self.assertEqual(request.call_args.args[1], "http://searx/search")

    def test_errors_name_the_status_without_the_key(self):
        with mock.patch("httpx.request") as request:
            request.return_value = response(401, text="bad token BSA-key")
            with self.assertRaises(SearchError) as caught:
                brave_search("BSA-key")("q", 5)
        self.assertIn("HTTP 401", str(caught.exception))
        self.assertNotIn("BSA-key", str(caught.exception))

    def test_empty_results_say_so(self):
        reply = web_search_handler(lambda q, n: [], "brave")(None, None, {"query": "x"})
        self.assertEqual(
            (reply["results"], reply["note"], reply["source"]), ([], EMPTY_NOTE, "brave")
        )
        keyless = web_search_handler(lambda q, n: [], "duckduckgo")(None, None, {"query": "x"})
        self.assertIn("SW_TAVILY_API_KEY", keyless["note"])

    def test_duckduckgo_errors(self):
        from ddgs.exceptions import DDGSException, RatelimitException

        with mock.patch("ddgs.DDGS") as ddgs:
            ddgs.return_value.text.side_effect = DDGSException("No results found.")
            self.assertEqual(duckduckgo_search("x", 3), [])
            ddgs.return_value.text.side_effect = RatelimitException("202 Ratelimit")
            with self.assertRaisesRegex(SearchError, "limiting requests.*SW_BRAVE_API_KEY"):
                duckduckgo_search("x", 3)
        backend = search_backend(replace(Settings(), ddgs_backends="duckduckgo"))[1]
        with mock.patch("ddgs.DDGS") as ddgs:
            ddgs.return_value.text.return_value = []
            backend("x", 2)
            self.assertEqual(ddgs.return_value.text.call_args.kwargs["backend"], "duckduckgo")


class ScholarTests(unittest.TestCase):
    WORK = {
        "id": "https://openalex.org/W1",
        "display_name": "The Annales School",
        "publication_year": 1990,
        "doi": "https://doi.org/10.1/abc",
        "authorships": [{"author": {"display_name": f"Author {i}"}} for i in range(7)],
        "primary_location": {"source": {"display_name": "History and Theory"}},
        "cited_by_count": 120,
        "open_access": {"oa_url": "https://oa"},
        "abstract_inverted_index": {"French": [1], "The": [0], "historians": [2]},
    }

    def test_request_and_parsing(self):
        with mock.patch("httpx.request") as request:
            request.return_value = response(payload={"results": [self.WORK]})
            (paper,) = openalex_search()(
                "Annales school", max_results=5, from_year=1980, to_year=2000, sort="cited"
            )
            args = request.call_args
        self.assertEqual(args.args, ("GET", "https://api.openalex.org/works"))
        self.assertEqual(
            args.kwargs["params"],
            {
                "search": "Annales school",
                "per-page": 5,
                "filter": "from_publication_date:1980-01-01,to_publication_date:2000-12-31",
                "sort": "cited_by_count:desc",
            },
        )
        self.assertEqual(args.kwargs["headers"], {})
        self.assertEqual(paper["title"], "The Annales School")
        self.assertEqual(paper["authors"][-1], "et al.")
        self.assertEqual(len(paper["authors"]), 6)
        self.assertEqual(
            (paper["year"], paper["venue"], paper["cited_by"]), (1990, "History and Theory", 120)
        )
        self.assertEqual(paper["url"], "https://doi.org/10.1/abc")
        self.assertEqual(paper["abstract"], "The French historians")
        self.assertEqual(_abstract(None), "")

    def test_handler_checks_years_and_reports_empty(self):
        handler = scholar_search_handler(lambda query, **options: [])
        with self.assertRaises(ValueError):
            handler(None, None, {"query": "x", "from_year": 2020, "to_year": 2000})
        self.assertIn("note", handler(None, None, {"query": "x"}))


class OpenAlexLimitTests(unittest.TestCase):
    def setUp(self):
        from app.tools import scholar

        scholar.PACER.paused.clear()
        self.addCleanup(scholar.PACER.paused.clear)
        sleeper = mock.patch("app.tools.scholar.time.sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_used_up_budget_pauses_searches_with_a_clear_message(self):
        budget = response(
            429,
            text='{"error": "Rate limit exceeded", '
            '"message": "Insufficient budget. This request has no API key"}',
        )
        with mock.patch("httpx.request", return_value=budget) as request:
            with self.assertRaises(SearchError) as caught:
                openalex_search()("Annales")
            self.assertIn("SW_OPENALEX_API_KEY", str(caught.exception))
            self.assertIn("Do not call scholar_search again", str(caught.exception))
            with self.assertRaises(SearchError):
                openalex_search()("again")  # paused: no request at all
            self.assertEqual(request.call_count, 1)
        ok = response(payload={"results": []})
        with mock.patch("httpx.request", return_value=ok) as request:
            openalex_search()("with a key", api_key="user-key")  # its own budget
            self.assertEqual(request.call_count, 1)
            self.assertEqual(
                request.call_args.kwargs["headers"], {"Authorization": "Bearer user-key"}
            )

    def test_short_rate_limits_are_waited_out_once(self):
        busy = response(429, text="Too Many Requests")
        busy.headers["retry-after"] = "2"
        ok = response(payload={"results": [ScholarTests.WORK]})
        with mock.patch("httpx.request", side_effect=[busy, ok]) as request:
            self.assertEqual(len(openalex_search("key")("x")), 1)
        self.assertEqual(request.call_count, 2)
        self.assertIn(mock.call(2.0), self.sleep.call_args_list)
        with (
            mock.patch("httpx.request", side_effect=[busy, busy]),
            self.assertRaisesRegex(SearchError, "limiting requests"),
        ):
            openalex_search("key")("x")


class ScholarToolTests(ApiCase):
    def test_the_assistant_can_search_literature(self):
        turn = self.send(
            "找几篇 RAG 的论文",
            call(
                "scholar_search",
                query="retrieval augmented generation",
                sort="cited",
                max_results=3,
            ),
            lambda t: answer(last_result(t)["papers"][0]["title"]),
        )
        self.assertEqual(
            turn["answer"], "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
        )
        self.assertEqual(self.scholar.calls[0]["sort"], "cited")
        self.assertIn("scholar_search", self.model.tool_names())
        self.assertEqual(self.client.get("/health").json()["search"], "web")
        search = self.client.get("/api/providers").json()["search"]
        self.assertEqual(search, {"web": "web", "scholar_key": False})


class RequestKeyTests(ApiCase):
    def test_a_key_from_the_browser_is_used_for_that_request_only(self):
        self.send(
            "找论文",
            call("scholar_search", query="Annales school"),
            answer("好"),
            openalex_api_key="  user-openalex-key  ",
        )
        self.assertEqual(self.scholar.calls[-1]["api_key"], "user-openalex-key")
        self.send("再找", call("scholar_search", query="Braudel"), answer("好"))
        self.assertNotIn("api_key", self.scholar.calls[-1])  # next request: server default
        stored = "".join(
            p.read_text("utf-8", "ignore") for p in self.root.rglob("*") if p.is_file()
        )
        self.assertNotIn("user-openalex-key", stored)
        self.assertEqual(self.app.state.agent._request_keys, {})

    def test_graph_stages_get_the_key_too(self):
        self.set_risk("high")
        stages = [
            answer("RQ1"),
            call("scholar_search", query="Annales school"),
            answer("[1] ..."),
            answer("taxonomy"),
            answer("not saved"),
            answer("done"),
        ]
        turn = self.send(
            "综述",
            call("skill_survey-creator_invoke"),
            lambda t: call(
                "skill_survey-creator_run_graph",
                invoke_id=json.loads(t.steps[0].output)["invoke_id"],
                graph_id="survey-pipeline",
            ),
            openalex_api_key="stage-key",
        )
        self.model.steps.extend(stages)
        response = self.client.post(
            f"/api/sessions/{self.session['id']}/approvals/{turn['pending_approvals'][0]['id']}",
            json={"api_key": "sk-test", "approved": True, "openalex_api_key": "stage-key"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.scholar.calls[-1]["api_key"], "stage-key")


if __name__ == "__main__":
    unittest.main()
