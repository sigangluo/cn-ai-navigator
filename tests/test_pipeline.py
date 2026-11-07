"""Tests for the refresh pipeline, registry validation, health checks and build stamping."""

import copy
import io
import json
import re
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import build  # noqa: E402
import health  # noqa: E402
import update  # noqa: E402

ROOT = SCRIPTS.parent
REGISTRY = json.loads((ROOT / "data" / "sources.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((ROOT / "data" / "schema.json").read_text(encoding="utf-8"))


class RegistryTests(unittest.TestCase):
    def test_real_registry_is_valid(self):
        self.assertEqual(len(build.validate(REGISTRY, SCHEMA)), len(REGISTRY["sources"]))

    def test_every_declared_mode_has_a_fetcher(self):
        missing = set(SCHEMA["modes"]) - {"link"} - set(update.FETCHERS)
        self.assertFalse(missing, f"modes without a fetcher: {sorted(missing)}")
        self.assertFalse(set(update.FETCHERS) - set(SCHEMA["modes"]), "fetcher without a schema entry")

    def mutated(self, source_id, **changes):
        data = copy.deepcopy(REGISTRY)
        source = next(s for s in data["sources"] if s["id"] == source_id)
        for key, value in changes.items():
            if value is None:
                source.pop(key, None)
            else:
                source[key] = value
        return data

    def test_validation_rejects_common_mistakes(self):
        cases = {
            "bad language": self.mutated("qbitai", language="zh_CN"),
            "labs without kind": self.mutated("openai-news", kind=None),
            "link without reason": self.mutated("jiqizhixin", directoryReason=None),
            "typo in a field": self.mutated("qbitai", feeed="https://example.org/x"),
            "feed mode without feed": self.mutated("qbitai", feed=None),
            "unknown cadence": self.mutated("qbitai", cadence="hourly"),
            "http url": self.mutated("qbitai", url="http://www.qbitai.com/"),
            "feedCategories outside sections": self.mutated("openai-research", sections=["Publication"]),
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(AssertionError):
                build.validate(data, SCHEMA)

    def test_site_copy_flags_ranking_modes(self):
        derived = {s["id"]: s["ranking"] for s in build.with_derived_fields(REGISTRY, SCHEMA)["sources"]}
        self.assertTrue(derived["github-trending"])
        self.assertFalse(derived["qbitai"])

    def test_asset_versions_follow_file_content(self):
        with tempfile.TemporaryDirectory() as temp:
            site = Path(temp)
            (site / "assets").mkdir()
            (site / "assets" / "app.js").write_text("one")
            index = site / "index.html"
            index.write_text('<script src="assets/app.js?v=old" defer></script><link href="assets/missing.css">')
            with patch.object(build, "SITE", site), patch.object(build, "INDEX", index):
                build.stamp_asset_versions()
                first = index.read_text()
                (site / "assets" / "app.js").write_text("two")
                build.stamp_asset_versions()
            self.assertNotIn("v=old", first)
            self.assertNotEqual(first, index.read_text())
            self.assertIn('href="assets/missing.css"', index.read_text())

    def test_index_references_only_existing_assets(self):
        html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        for name in re.findall(r'(?:href|src)="(assets/[^"?]+)', html):
            self.assertTrue((ROOT / "site" / name).exists(), name)


class RefreshTests(unittest.TestCase):
    """update.update() with fake fetchers: failure handling and empty feeds, no real network."""

    SOURCE = {"id": "s", "mode": "feed", "feed": "https://example.org/feed", "url": "https://example.org/"}
    HEALTHY = {"status": "ok", "lastSuccessAt": "2026-11-06T00:00:00Z",
               "items": [{"title": "Old", "url": "https://example.org/old"}]}

    def run_update(self, source, fetcher, previous=None):
        with tempfile.TemporaryDirectory() as temp:
            registry, output = Path(temp) / "sources.json", Path(temp) / "latest.json"
            registry.write_text(json.dumps({"sources": [source]}))
            if previous is not None:
                output.write_text(json.dumps({"results": {source["id"]: previous}}))
            with patch.object(update, "ROOT", Path(temp)), patch.object(update, "REGISTRY", registry), \
                    patch.object(update, "OUTPUT", output), patch.dict(update.FETCHERS, {source["mode"]: fetcher}):
                return update.update()["results"][source["id"]]

    @staticmethod
    def broken(run):
        raise OSError("connection reset")

    @staticmethod
    def ok(run):
        return [{"title": "New", "url": "https://example.org/new"}]

    def test_first_failure_of_a_healthy_source_stays_ok_and_keeps_items(self):
        result = self.run_update(self.SOURCE, self.broken, self.HEALTHY)
        self.assertEqual((result["status"], result["failures"]), ("ok", 1))
        self.assertEqual(result["items"], self.HEALTHY["items"])
        self.assertEqual(result["lastSuccessAt"], self.HEALTHY["lastSuccessAt"])

    def test_repeated_failure_becomes_error(self):
        result = self.run_update(self.SOURCE, self.broken, {**self.HEALTHY, "failures": 1})
        self.assertEqual((result["status"], result["failures"]), ("error", 2))
        self.assertEqual(result["items"], self.HEALTHY["items"])

    def test_never_synced_source_is_an_error_immediately(self):
        self.assertEqual(self.run_update(self.SOURCE, self.broken)["status"], "error")

    def test_success_clears_failure_state(self):
        result = self.run_update(self.SOURCE, self.ok, {**self.HEALTHY, "status": "error", "failures": 3, "error": "x"})
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("failures", result)
        self.assertNotIn("error", result)

    def test_failure_keeps_http_validators_for_the_next_conditional_request(self):
        previous = {**self.HEALTHY, "httpValidators": {"etag": '"a"'}, "feedParserVersion": 4}
        result = self.run_update(self.SOURCE, self.broken, previous)
        self.assertEqual(result["httpValidators"], {"etag": '"a"'})

    def run_empty_feed(self, source, previous):
        empty = b"<?xml version='1.0'?><rss><channel><title>cs.AI</title></channel></rss>"
        with patch.object(update, "fetch_feed", return_value=(empty, {})):
            return self.run_update(source, update.FETCHERS["feed"], previous)

    def test_empty_feed_with_allow_empty_keeps_previous_items_without_alarm(self):
        result = self.run_empty_feed({**self.SOURCE, "allowEmpty": True}, {**self.HEALTHY, "failures": 1})
        self.assertEqual((result["status"], result["items"]), ("ok", self.HEALTHY["items"]))
        self.assertNotIn("failures", result)

    def test_empty_feed_without_allow_empty_is_a_failure(self):
        self.assertEqual(self.run_empty_feed(self.SOURCE, self.HEALTHY)["failures"], 1)

    def test_ai_only_feed_keeps_only_ai_titles_and_fails_when_none_match(self):
        def feed(*titles):
            entries = "".join(f"<item><title>{t}</title><link>https://example.org/{i}</link></item>" for i, t in enumerate(titles))
            return f"<rss><channel>{entries}</channel></rss>".encode()
        source = {**self.SOURCE, "aiOnly": True}
        with patch.object(update, "fetch_feed", return_value=(feed("大模型发布", "汽车停产"), {})):
            result = self.run_update(source, update.FETCHERS["feed"], self.HEALTHY)
        self.assertEqual([i["title"] for i in result["items"]], ["大模型发布"])
        with patch.object(update, "fetch_feed", return_value=(feed("汽车停产"), {})):
            self.assertEqual(self.run_update(source, update.FETCHERS["feed"], self.HEALTHY)["failures"], 1)

    def test_partial_refresh_keeps_the_snapshot_time(self):
        with tempfile.TemporaryDirectory() as temp:
            registry, output = Path(temp) / "sources.json", Path(temp) / "latest.json"
            registry.write_text(json.dumps({"sources": [self.SOURCE]}))
            output.write_text(json.dumps({"generatedAt": "2026-01-01T00:00:00Z", "results": {}}))
            with patch.object(update, "ROOT", Path(temp)), patch.object(update, "REGISTRY", registry), \
                    patch.object(update, "OUTPUT", output), patch.dict(update.FETCHERS, {"feed": self.ok}):
                self.assertEqual(update.update(only={"s"})["generatedAt"], "2026-01-01T00:00:00Z")
                self.assertNotEqual(update.update()["generatedAt"], "2026-01-01T00:00:00Z")

    def test_link_and_unavailable_social_modes_are_directory_entries(self):
        for source in ({"id": "l", "mode": "link"}, {"id": "r", "mode": "reddit", "subreddit": "x"}):
            with patch.dict("os.environ", {"REDDIT_CLIENT_ID": "", "REDDIT_CLIENT_SECRET": "", "REDDIT_USER_AGENT": ""}):
                self.assertEqual(self.run_update(source, self.broken)["status"], "directory")


class TeaserReuseTests(unittest.TestCase):
    def test_known_teasers_are_reused_and_only_new_articles_are_fetched(self):
        items = [{"title": "Seen", "url": "https://e.org/seen", "description": ""},
                 {"title": "New", "url": "https://e.org/new", "description": ""}]
        page = b'<meta name="description" content="A fresh teaser for the new article, long enough to keep.">'
        with patch.object(update, "fetch", return_value=page) as fetched:
            update.enrich_descriptions(items, {"https://e.org/seen": "Saved teaser"})
        self.assertEqual(items[0]["description"], "Saved teaser")
        self.assertIn("fresh teaser", items[1]["description"])
        self.assertEqual([call.args[0] for call in fetched.call_args_list], ["https://e.org/new"])

    def test_stanford_known_dates_skip_article_requests(self):
        card = ('<div class="ContentItem_root__x"><a class="ContentItem_titleLink__y" href="/news/a">Story</a>'
                '<span class="Typography_mono-small__z">Oct 7</span></div>').encode()

        def no_network(url):
            raise AssertionError("article page should not be fetched")
        items = update.parse_stanford_listing(card, "https://hai.stanford.edu/news", no_network,
                                              {"https://hai.stanford.edu/news/a": "2026-10-07T00:00:00Z"})
        self.assertEqual(items[0]["publishedAt"], "2026-10-07T00:00:00Z")

    def test_hn_metrics_are_attached_and_failures_are_skipped(self):
        items = [{"url": "https://news.ycombinator.com/item?id=1"}, {"url": "https://news.ycombinator.com/item?id=2"}]

        def fake(url, **kwargs):
            if url.endswith("/1.json"):
                return b'{"id":1,"score":10,"descendants":3}'
            raise OSError("down")
        with patch.object(update, "fetch", side_effect=fake):
            update.enrich_hn_metrics(items)
        self.assertEqual(items[0]["metrics"], {"upvotes": 10, "comments": 3})
        self.assertNotIn("metrics", items[1])


class ParserRegressionTests(unittest.TestCase):
    def test_infoq_filter_needs_whole_words_for_english_terms(self):
        match = update.AI_TITLE_TERMS.search
        for title in ("How to maintain a Rails container", "Said the mail chain", "纯前端框架发布"):
            self.assertIsNone(match(title), title)
        for title in ("AICon 北京", "大模型推理优化", "Building agents with MCP", "OpenAI 发布新模型"):
            self.assertTrue(match(title), title)

    def test_baai_bulk_import_timestamps_are_not_treated_as_publication_dates(self):
        raw = json.dumps({"items": [
            {"category": "media", "title": "Imported", "source_url": "https://e.org/a", "is_published": True,
             "published_at": "2026-09-13T08:20:07.536172Z"},
            {"category": "media", "title": "Real", "source_url": "https://e.org/b", "is_published": True,
             "published_at": "2026-07-15T04:00:00Z"},
        ]}).encode()
        items = update.parse_baai_news(raw, "media")
        self.assertEqual([(i["title"], i["publishedAt"]) for i in items],
                         [("Real", "2026-07-15T04:00:00Z"), ("Imported", "")])


class RequestTests(unittest.TestCase):
    @staticmethod
    def response(body=b"ok"):
        response = io.BytesIO(body)
        response.headers = Message()
        return response

    def test_transient_network_error_is_retried_once(self):
        outcomes = [urllib.error.URLError("EOF occurred in violation of protocol"), self.response()]

        def opener(request, timeout):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        with patch("update.urllib.request.urlopen", side_effect=opener), patch.object(update.time, "sleep"):
            self.assertEqual(update.fetch("https://example.org/x"), b"ok")

    def test_extra_retry_status_is_honoured_and_others_are_not(self):
        def not_found(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", Message(), None)
        with patch("update.urllib.request.urlopen", side_effect=not_found) as opened, patch.object(update.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                update.fetch_feed("https://example.org/f", {})
            self.assertEqual(opened.call_count, 1)
            with self.assertRaises(urllib.error.HTTPError):
                update.fetch_feed("https://example.org/f", {}, retry_statuses=update.TRANSIENT_STATUSES | {404})
            self.assertEqual(opened.call_count, 3)

    def test_slow_response_is_not_retried(self):
        with patch.object(update, "read_response", side_effect=update.DeadlineExceeded("slow")), \
                patch("update.urllib.request.urlopen", return_value=self.response()) as opened:
            with self.assertRaises(TimeoutError):
                update.fetch("https://example.org/slow")
        self.assertEqual(opened.call_count, 1)


class HealthTests(unittest.TestCase):
    NOW = "2026-11-07T07:00:00Z"

    def report(self, source, result):
        registry = {"sources": [{"id": "s", "language": "non_zh", "cadence": "weekly", "mode": "feed", **source}]}
        snapshot = {"generatedAt": self.NOW, "results": {"s": {"status": "ok", "items": [], **result}}}
        return health.check(registry, SCHEMA, snapshot)

    def test_stale_source_is_flagged_against_its_cadence(self):
        findings, _ = self.report({}, {"items": [{"title": "Old", "publishedAt": "2026-09-01T00:00:00Z"}]})
        self.assertEqual(findings[0][0], "WARN")
        self.assertIn("cadence=weekly", findings[0][2])

    def test_dormant_cadence_is_never_stale(self):
        findings, _ = self.report({"cadence": "dormant"}, {"items": [{"title": "Old", "publishedAt": "2020-01-01T00:00:00Z"}]})
        self.assertEqual(findings, [])

    def test_language_label_must_match_content(self):
        english = [{"title": f"Post {i}", "description": "English", "publishedAt": "2026-11-06T00:00:00Z"} for i in range(5)]
        findings, _ = self.report({"language": "zh"}, {"items": english})
        self.assertTrue(any("zh" in text for _, _, text in findings))
        chinese = [{"title": f"文章 {i}", "publishedAt": "2026-11-06T00:00:00Z"} for i in range(5)]
        findings, _ = self.report({"language": "non_zh"}, {"items": chinese})
        self.assertTrue(any("non_zh" in text for _, _, text in findings))

    def test_repeated_failure_is_an_error_and_counts_toward_the_gate(self):
        findings, stats = self.report({}, {"status": "error", "failures": 3, "error": "HTTP 500"})
        self.assertEqual(findings[0][0], "ERROR")
        self.assertEqual((stats["errors"], stats["fetchable"]), (1, 1))

    def test_single_failure_is_only_informational(self):
        findings, stats = self.report({}, {"failures": 1, "error": "timeout"})
        self.assertEqual([level for level, _, _ in findings], ["INFO"])
        self.assertEqual(stats["errors"], 0)

    def test_old_link_verification_is_reported(self):
        registry = {"sources": [{"id": "l", "language": "zh", "cadence": "weekly", "mode": "link",
                                 "directoryReason": "403", "verifiedAt": "2026-03"}]}
        findings, _ = health.check(registry, SCHEMA, {"generatedAt": self.NOW, "results": {}})
        self.assertEqual(findings[0][0], "INFO")

    def test_categories_missing_from_sections_are_reported_but_generic_ones_are_not(self):
        items = [{"title": "T", "publishedAt": "2026-11-06T00:00:00Z", "categories": ["Policy", "综合", "product"]}]
        findings, _ = self.report({"sections": ["Product"]}, {"items": items})
        text = " ".join(message for _, _, message in findings)
        self.assertIn("Policy", text)
        self.assertNotIn("综合", text)
        self.assertNotIn("product", text)

    def test_current_snapshot_report_renders(self):
        snapshot = json.loads((ROOT / "site" / "data" / "latest.json").read_text(encoding="utf-8"))
        findings, stats = health.check(REGISTRY, SCHEMA, snapshot)
        self.assertIn("来源健康报告", health.render(findings, stats))


if __name__ == "__main__":
    unittest.main()
