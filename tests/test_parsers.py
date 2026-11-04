import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from update import parse_feed, parse_papers, parse_hf_trending, parse_trending, parse_aa_leaderboard, parse_publisher_page  # noqa: E402


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
        raw = ('[{"publishedAt":"2026-11-04T01:00:00Z","paper":{"id":"2611.00001","title":"A Paper","summary":"' + 'A long abstract. ' * 50 + '"}}]').encode()
        result = parse_papers(raw)
        self.assertEqual(result[0]["url"], "https://huggingface.co/papers/2611.00001")
        self.assertGreater(len(result[0]["description"]), 500)

    def test_hugging_face_ranked_dataset_link(self):
        raw = b'[{"id":"org/dataset","trendingScore":88,"description":"A public dataset for evaluation."}]'
        result = parse_hf_trending(raw, "datasets")
        self.assertEqual(result[0]["url"], "https://huggingface.co/datasets/org/dataset")
        self.assertEqual(result[0]["description"], "A public dataset for evaluation.")

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

    def test_feed_uses_publisher_summary_without_html_or_arxiv_metadata(self):
        raw = b'<rss><channel><item><title>Paper</title><link>https://arxiv.org/abs/1234</link><description>arXiv:1234v1 Announce Type: new Abstract: &lt;p&gt;A useful research result.&lt;/p&gt;</description></item></channel></rss>'
        self.assertEqual(parse_feed(raw, "https://arxiv.org")[0]["description"], "A useful research result.")

    def test_feed_skips_image_caption_and_uses_publisher_description(self):
        raw = b'<rss><channel><item><title>Update</title><link>https://example.org/update</link><description>Animated carousel of images</description><description>&lt;img src="x"&gt;A useful publisher introduction.</description></item></channel></rss>'
        self.assertEqual(parse_feed(raw, "https://example.org")[0]["description"], "A useful publisher introduction.")

    def test_publisher_page_requires_dates_and_original_links(self):
        raw = b'<article class="nUeDbq_article"><header><p><a href="/research/team/safety">Safety</a></p><time>Nov 2, 2026</time></header><h3><a class="nUeDbq_metaTitleLink" href="/research/a">Research A</a></h3><p class="nUeDbq_metaDescription">Publisher summary.</p></article>'
        result = parse_publisher_page(raw, "anthropic-research", "https://www.anthropic.com/research")
        self.assertEqual(result[0]["description"], "Publisher summary.")
        self.assertEqual(result[0]["categories"], ["Safety"])
        self.assertEqual(result[0]["url"], "https://www.anthropic.com/research/a")

    def test_openai_developer_blog_uses_native_topic_and_teaser(self):
        raw = b'<a class="resource-item not-prose" href="/blog/example"><div class="px-4 pt-4 text-secondary">Oct 14</div><div class="flex-1 line-clamp-2">Example</div><p class="px-4 text-default line-clamp-3">Publisher teaser.</p><div class="px-4 pt-2 text-sm text-secondary">Codex</div></a>'
        result = parse_publisher_page(raw, "openai-dev-blog", "https://developers.openai.com/blog")
        self.assertEqual(result[0]["categories"], ["Codex"])
        self.assertEqual(result[0]["description"], "Publisher teaser.")
        self.assertTrue(result[0]["publishedAt"])


if __name__ == "__main__":
    unittest.main()
