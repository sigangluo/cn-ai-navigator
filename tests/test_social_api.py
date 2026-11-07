import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update  # noqa: E402


class SocialApiTests(unittest.TestCase):
    def test_reddit_listing_removes_deleted_and_old_posts(self):
        current = datetime.now(timezone.utc)
        def post(title, hours_ago, **extra):
            return {"data": {"title": title, "permalink": "/r/LocalLLaMA/comments/abc/post/",
                             "created_utc": (current - timedelta(hours=hours_ago)).timestamp(),
                             "score": 42, "num_comments": 7, "link_flair_text": "Discussion", **extra}}
        raw = json.dumps({"data": {"children": [post("Recent AI post", 2),
            post("Old post", 50), post("[removed]", 1),
            post("Deleted author", 1, author="[deleted]")]}}).encode()
        items = update.parse_reddit_listing(raw, "LocalLLaMA", current - timedelta(hours=48))
        self.assertEqual([item["title"] for item in items], ["Recent AI post"])
        self.assertEqual(items[0]["metrics"], {"upvotes": 42, "comments": 7})
        self.assertEqual(items[0]["categories"], ["Discussion"])

    def test_x_posts_use_original_permalinks_and_metrics(self):
        raw = json.dumps({"data": [
            {"id": "2", "text": "Newest model update", "created_at": "2026-11-05T08:00:00Z",
             "public_metrics": {"like_count": 12, "retweet_count": 3, "reply_count": 4}},
            {"id": "1", "text": "A reply", "created_at": "2026-11-05T09:00:00Z",
             "referenced_posts": [{"type": "replied_to", "id": "2"}]},
        ], "meta": {"result_count": 2}}).encode()
        items = update.parse_x_posts(raw, "karpathy")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://x.com/karpathy/status/2")
        self.assertEqual(items[0]["metrics"], {"likes": 12, "reposts": 3, "comments": 4})
        self.assertEqual(update.parse_x_user_id(b'{"data":{"id":"123","username":"karpathy"}}', "karpathy"), "123")

    def test_opt_in_and_cached_x_id(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = Path(temp) / "sources.json"
            output = Path(temp) / "latest.json"
            registry.write_text(json.dumps({"sources": [
                {"id": "x-test", "mode": "x_api", "username": "karpathy"},
                {"id": "reddit-test", "mode": "reddit", "subreddit": "LocalLLaMA"},
            ]}))
            output.write_text(json.dumps({"results": {"x-test": {
                "status": "ok", "items": [], "userId": "123"}}}))
            with patch.object(update, "ROOT", Path(temp)), patch.object(update, "REGISTRY", registry), patch.object(update, "OUTPUT", output):
                with patch.dict(os.environ, {"X_BEARER_TOKEN": "fake", "X_API_ENABLED": "false",
                                          "REDDIT_CLIENT_ID": "", "REDDIT_CLIENT_SECRET": "",
                                          "REDDIT_USER_AGENT": ""}):
                    with patch.object(update, "fetch", side_effect=AssertionError("unexpected network call")):
                        update.update()
                    self.assertEqual(json.loads(output.read_text())["results"]["x-test"]["status"], "directory")
                output.write_text(json.dumps({"results": {"x-test": {
                    "status": "ok", "items": [], "userId": "123"}}}))
                calls = []
                def fake_fetch(url, **kwargs):
                    calls.append((url, kwargs))
                    return b'{"data":[{"id":"456","text":"A new AI post","created_at":"2026-11-05T08:00:00Z"}],"meta":{"result_count":1}}'
                with patch.dict(os.environ, {"X_BEARER_TOKEN": "fake", "X_API_ENABLED": "true",
                                          "REDDIT_CLIENT_ID": "", "REDDIT_CLIENT_SECRET": "",
                                          "REDDIT_USER_AGENT": ""}):
                    with patch.object(update, "fetch", side_effect=fake_fetch):
                        update.update()
                result = json.loads(output.read_text())["results"]
                self.assertEqual(result["x-test"]["userId"], "123")
                self.assertEqual(result["reddit-test"]["status"], "directory")
                self.assertEqual(len(calls), 1)
                self.assertIn("/users/123/tweets?", calls[0][0])
                self.assertIn("max_results=5", calls[0][0])

    def test_social_content_is_removed_before_snapshot_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = Path(temp) / "sources.json"
            output = Path(temp) / "latest.json"
            registry.write_text(json.dumps({"sources": [
                {"id": "reddit-test", "mode": "reddit"},
                {"id": "x-test", "mode": "x_api"},
            ]}))
            output.write_text(json.dumps({"results": {
                "reddit-test": {"status": "ok", "items": [{"title": "User post"}]},
                "x-test": {"status": "ok", "items": [], "userId": "123"},
            }}))
            with patch.object(update, "REGISTRY", registry), patch.object(update, "OUTPUT", output):
                update.redact_social_snapshot()
            result = json.loads(output.read_text())["results"]
            self.assertEqual(result["reddit-test"], {"status": "directory", "items": []})
            self.assertEqual(result["x-test"], {"status": "directory", "items": [], "userId": "123"})


if __name__ == "__main__":
    unittest.main()
