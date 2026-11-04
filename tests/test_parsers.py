import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from update import parse_feed, parse_papers, parse_hf_trending, parse_trending, parse_aa_leaderboard  # noqa: E402


class ParserTests(unittest.TestCase):
    def test_rss_keeps_publisher_category_and_original_url(self):
        raw = b'<rss><channel><item><title>Research update</title><link>https://example.org/post</link><pubDate>Wed, 04 Nov 2026 00:00:00 GMT</pubDate><category>Research</category></item></channel></rss>'
        result = parse_feed(raw, "https://example.org")
        self.assertEqual(result[0]["categories"], ["Research"])
        self.assertEqual(result[0]["url"], "https://example.org/post")
        self.assertEqual(result[0]["publishedAt"], "2026-11-04T00:00:00Z")

    def test_atom_link_and_unsafe_url(self):
        raw = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Good</title><link href="/good"/><updated>2026-11-04T01:00:00Z</updated></entry><entry><title>Bad</title><link href="javascript:alert(1)"/></entry></feed>'
        result = parse_feed(raw, "https://example.org")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["url"], "https://example.org/good")

    def test_daily_papers_api(self):
        raw = b'[{"publishedAt":"2026-11-04T01:00:00Z","paper":{"id":"2611.00001","title":"A Paper"}}]'
        result = parse_papers(raw)
        self.assertEqual(result[0]["url"], "https://huggingface.co/papers/2611.00001")

    def test_hugging_face_ranked_dataset_link(self):
        raw = b'[{"id":"org/dataset","trendingScore":88}]'
        result = parse_hf_trending(raw, "datasets")
        self.assertEqual(result[0]["url"], "https://huggingface.co/datasets/org/dataset")

    def test_trending_rows_and_void_tags(self):
        raw = b'<article class="Box-row"><h2><a href="/org/repo"> org / repo </a></h2><p>A useful repo<img src="x"></p></article>'
        result = parse_trending(raw)
        self.assertEqual(result[0]["title"], "org/repo")
        self.assertEqual(result[0]["description"], "A useful repo")

    def test_aa_leaderboard_requires_sorted_intelligence_scores(self):
        rows = ''.join(f'<tr><td><div>Model {i}</div></td><td>1M</td><td>Lab</td><td><div>{10-i}</div></td><td><a href="/models/model-{i}">Model</a></td></tr>' for i in range(6))
        result = parse_aa_leaderboard(f'<table><tbody>{rows}</tbody></table>'.encode())
        self.assertEqual(result[0]["title"], "Model 0")
        self.assertEqual(result[0]["description"], "Intelligence Index 10 · Lab")

    def test_missing_feed_date_uses_publishers_dated_link(self):
        raw = b'<rss><channel><item><title>Post</title><link>https://tech.meituan.com/2026/11/02/post.html</link></item></channel></rss>'
        result = parse_feed(raw, "https://tech.meituan.com")
        self.assertEqual(result[0]["publishedAt"], "2026-11-01T16:00:00Z")


if __name__ == "__main__":
    unittest.main()
