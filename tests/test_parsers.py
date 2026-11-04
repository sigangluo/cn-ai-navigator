import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from update import parse_feed, parse_youtube_feed, parse_papers, parse_hf_trending, parse_trending, parse_aa_leaderboard, parse_publisher_page, parse_kimi_page, parse_cohere_page, parse_llamaindex_page, parse_infoq_ai_feed, parse_metr_feed, parse_deepseek_updates, parse_github_topic, parse_aa_articles, parse_openai_cookbook, _MetaDescription  # noqa: E402


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

    def test_arxiv_atom_uses_publication_date_and_creator_list(self):
        raw = b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/"><entry><title>Paper</title><updated>2026-11-05T00:00:00Z</updated><link href="https://arxiv.org/abs/2611.00001"/><published>2026-11-04T05:00:00Z</published><dc:creator>Alice Chen, Bob Smith</dc:creator><summary>arXiv:2611.00001v1 Announce Type: new Abstract: A result.</summary></entry></feed>'
        item = parse_feed(raw, "https://arxiv.org/list/cs.AI/new")[0]
        self.assertEqual(item["publishedAt"], "2026-11-04T05:00:00Z")
        self.assertEqual(item["authors"], ["Alice Chen", "Bob Smith"])

    def test_daily_papers_api(self):
        raw = ('[{"publishedAt":"2026-11-04T01:00:00Z","paper":{"id":"2611.00001","title":"A Paper","upvotes":28,"summary":"' + 'A long abstract. ' * 50 + '"}}]').encode()
        result = parse_papers(raw)
        self.assertEqual(result[0]["url"], "https://huggingface.co/papers/2611.00001")
        self.assertGreater(len(result[0]["description"]), 500)
        self.assertEqual(result[0]["metrics"]["upvotes"], 28)

    def test_daily_papers_author_and_linked_organization_are_distinct(self):
        raw = b'[{"publishedAt":"2026-11-04T01:00:00Z","paper":{"id":"2611.00002","title":"Paper","authors":[{"name":"Alice Chen"},{"name":"Bob Smith"}],"organization":{"name":"ExampleLab","fullname":"Example Lab"}}}]'
        item = parse_papers(raw)[0]
        self.assertEqual(item["authors"], ["Alice Chen", "Bob Smith"])
        self.assertEqual(item["linkedOrganization"], "Example Lab")
        self.assertNotIn("affiliations", item)

    def test_hugging_face_ranked_dataset_link(self):
        raw = b'[{"id":"org/dataset","trendingScore":88,"likes":32,"downloads":1200,"description":"A public dataset for evaluation."}]'
        result = parse_hf_trending(raw, "datasets")
        self.assertEqual(result[0]["url"], "https://huggingface.co/datasets/org/dataset")
        self.assertEqual(result[0]["description"], "A public dataset for evaluation.")
        self.assertEqual(result[0]["metrics"], {"likes": 32, "downloads": 1200})

    def test_trending_rows_and_void_tags(self):
        raw = b'<article class="Box-row"><h2><a href="/org/repo"> org / repo </a></h2><p>A useful repo<img src="x"></p><a href="/org/repo/stargazers"><svg></svg>12,345</a><span>678 stars today</span></article>'
        result = parse_trending(raw)
        self.assertEqual(result[0]["title"], "org/repo")
        self.assertEqual(result[0]["description"], "A useful repo")
        self.assertEqual(result[0]["metrics"], {"stars": 12345, "starsToday": 678})

    def test_aa_leaderboard_requires_sorted_intelligence_scores(self):
        rows = ''.join(f'<tr><td><div>Model {i}</div></td><td>1M</td><td>Lab</td><td><div>{10-i}</div></td><td><a href="/models/model-{i}">Model</a></td></tr>' for i in range(6))
        result = parse_aa_leaderboard(f'<table><tbody>{rows}</tbody></table>'.encode())
        self.assertEqual(result[0]["title"], "Model 0")
        self.assertEqual(result[0]["metrics"], {"intelligenceIndex": 10})
        self.assertEqual(result[0]["provider"], "Lab")

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

    def test_feed_omits_repeated_publisher_footer(self):
        raw = b'<rss><channel><item><title>Model update</title><link>https://example.org/model</link><description>What the model can do. The article Model update appeared first on Example Site.</description></item></channel></rss>'
        self.assertEqual(parse_feed(raw, "https://example.org")[0]["description"], "What the model can do.")

    def test_full_article_feed_uses_publisher_paragraph_instead_of_styles(self):
        article = "<style>h1 { color: red; }</style><p>First paragraph with a useful explanation of the author's experiment.</p><p>Second paragraph with enough detail to judge whether to read.</p>" + '<p>More text.</p>' * 100
        raw = f'<rss><channel><item><title>Experiment</title><link>https://example.org/post</link><description><![CDATA[{article}]]></description></item></channel></rss>'.encode()
        description = parse_feed(raw, "https://example.org")[0]["description"]
        self.assertTrue(description.startswith("First paragraph"))
        self.assertIn("Second paragraph", description)
        self.assertNotIn("color: red", description)

    def test_youtube_feed_uses_published_date_video_description_and_views(self):
        raw = b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/"><entry><title>Model walkthrough</title><link rel="alternate" href="https://www.youtube.com/watch?v=abc"/><published>2026-11-03T01:00:00Z</published><updated>2026-11-04T01:00:00Z</updated><media:group><media:description>A practical explanation of the model.</media:description><media:thumbnail url="https://i.ytimg.com/vi/abc/hqdefault.jpg"/><media:community><media:statistics views="12345"/></media:community></media:group></entry></feed>'
        item = parse_youtube_feed(raw, "https://www.youtube.com/")[0]
        self.assertEqual(item["publishedAt"], "2026-11-03T01:00:00Z")
        self.assertEqual(item["description"], "A practical explanation of the model.")
        self.assertEqual(item["metrics"], {"views": 12345})
        self.assertEqual(item["thumbnail"], "https://i.ytimg.com/vi/abc/hqdefault.jpg")

    def test_youtube_feed_omits_promotion_only_description(self):
        raw = b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/"><entry><title>Paper</title><link href="https://www.youtube.com/watch?v=abc"/><published>2026-11-03T01:00:00Z</published><media:group><media:description>Check out our sponsor: https://example.org\nPaper available here:\nhttps://example.org/paper\nThanks to our Patreon supporters:\nA, B, C, D, E, F</media:description></media:group></entry></feed>'
        self.assertEqual(parse_youtube_feed(raw, "https://www.youtube.com/")[0]["description"], "")

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

    def test_kimi_cards_are_deduplicated_and_dated(self):
        card = '<div class="menu-card group"><a href="/blog/kimi-k3" aria-label="Kimi"></a><h4 class="card-title m-0">Kimi K3</h4><p class="card-date">2026-07-16</p></div>'
        result = parse_kimi_page((card + card).encode(), "https://www.kimi.ai/zh-hans/blog/")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["url"], "https://www.kimi.ai/blog/kimi-k3")

    def test_cohere_cards_use_teaser_and_date(self):
        raw = b'<a class="flex flex-1 flex-col outline-none" href="/blog/model"><p class="title">Model update</p><p class="teaser">Useful publisher teaser.</p><p class="date">Oct 21, 2026</p><p>8 min read</p></a>'
        item = parse_cohere_page(raw, "https://cohere.com/blog")[0]
        self.assertEqual(item["description"], "Useful publisher teaser.")
        self.assertEqual(item["publishedAt"], "2026-10-21T00:00:00Z")

    def test_cohere_does_not_use_date_as_teaser(self):
        raw = b'<a class="flex flex-1 flex-col outline-none" href="/blog/model"><p>Model update</p><p>Oct 21, 2026</p><p>8 min read</p></a>'
        self.assertEqual(parse_cohere_page(raw, "https://cohere.com/blog")[0]["description"], "")

    def test_llamaindex_skips_undated_search_records(self):
        raw = b'<script id="BlogSearchData" type="application/json">[{"title":"Undated","publishedDate":null,"slug":"undated"},{"title":"Dated","publishedDate":"2026-11-04","slug":"dated","excerpt":"Publisher excerpt.","tags":["Engineering"]}]</script>'
        item = parse_llamaindex_page(raw, "https://www.llamaindex.ai/blog")[0]
        self.assertEqual(item["title"], "Dated")
        self.assertEqual(item["categories"], ["Engineering"])
        self.assertEqual(item["description"], "Publisher excerpt.")

    def test_infoq_filters_unrelated_feed_entries_and_strips_tracking(self):
        raw = b'<rss><channel><item><title>Cloud storage update</title><link>https://www.infoq.cn/article/a</link></item><item><title>AI agent update</title><link>https://www.infoq.cn/article/b?utm_source=rss</link></item></channel></rss>'
        item = parse_infoq_ai_feed(raw, "https://www.infoq.cn")[0]
        self.assertEqual(item["url"], "https://www.infoq.cn/article/b")

    def test_infoq_rss_gmt_label_is_beijing_wall_time(self):
        raw = b'<rss><channel><item><title>AI agent update</title><link>https://www.infoq.cn/article/a</link><pubDate>Wed, 04 Nov 2026 20:01:56 GMT</pubDate></item></channel></rss>'
        item = parse_infoq_ai_feed(raw, "https://www.infoq.cn")[0]
        self.assertEqual(item["publishedAt"], "2026-11-04T12:01:56Z")

    def test_publisher_meta_description_is_decoded(self):
        parser = _MetaDescription()
        parser.feed('<meta name="description" content="Publisher &amp; original summary.">')
        self.assertEqual(parser.description, "Publisher & original summary.")

    def test_metr_full_feed_keeps_only_short_intro(self):
        raw = b'<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item><title>Evaluation</title><link>https://metr.org/blog/evaluation/</link><pubDate>Wed, 04 Nov 2026 00:00:00 GMT</pubDate><content:encoded><![CDATA[<p>Publisher intro with details about the evaluation and its scope.</p><p>Full article text should not be stored.</p>]]></content:encoded></item></channel></rss>'
        item = parse_metr_feed(raw, "https://metr.org/blog/")[0]
        self.assertIn("Publisher intro", item["description"])
        self.assertNotIn("Full article", item["description"])

    def test_deepseek_sections_link_to_their_own_anchor(self):
        raw = b'<h2 class="anchor anchorWithStickyNavbar" id="date">Date: 2026-10-17</h2><h3 class="anchor anchorWithStickyNavbar" id="search-api">Search API<a href="#search-api">\xe2\x80\x8b</a></h3><p>Official release summary.</p>'
        item = parse_deepseek_updates(raw, "https://api-docs.deepseek.com/updates/")[0]
        self.assertEqual(item["url"], "https://api-docs.deepseek.com/updates/#search-api")
        self.assertEqual(item["title"], "Search API")
        self.assertEqual(item["description"], "Official release summary.")

    def test_github_topic_keeps_star_count_and_description(self):
        raw = b'<article class="border rounded color-shadow-small color-bg-subtle"><h3><a href="/org">org</a> / <a href="/org/repo">repo</a></h3><span aria-label="12,345 users starred this repository"></span><p class="color-fg-muted mb-0">A useful project.</p></article>'
        item = parse_github_topic(raw)[0]
        self.assertEqual(item["title"], "org/repo")
        self.assertEqual(item["metrics"], {"stars": 12345})
        self.assertEqual(item["description"], "A useful project.")

    def test_artificial_analysis_cards_require_dates(self):
        raw = b'<a class="relative flex flex-col gap-4 group" href="/articles/example"><h3>Index update</h3><p class="text-xs text-muted-foreground">October 28, 2026</p></a>'
        item = parse_aa_articles(raw, "https://artificialanalysis.ai/articles")[0]
        self.assertEqual(item["url"], "https://artificialanalysis.ai/articles/example")
        self.assertEqual(item["publishedAt"], "2026-10-28T00:00:00Z")

    def test_artificial_analysis_uses_its_embedded_teaser(self):
        raw = b'<script>\\"articles\\":[{\\"slug\\":\\"example\\",\\"title\\":\\"Index update\\",\\"subtitle\\":\\"Publisher summary.\\",\\"date\\":\\"October 28, 2026\\"}]</script>'
        item = parse_aa_articles(raw, "https://artificialanalysis.ai/articles")[0]
        self.assertEqual(item["description"], "Publisher summary.")

    def test_openai_cookbook_uses_dated_official_entries(self):
        raw = b'<astro-island component-url="/_astro/CookbookHomepage.react.js" props="{&quot;entries&quot;:[1,[[0,{&quot;internal_path&quot;:[0,&quot;articles/example&quot;],&quot;title&quot;:[0,&quot;Example recipe&quot;],&quot;date&quot;:[0,&quot;2026-10-23&quot;],&quot;tags&quot;:[1,[[0,&quot;agents&quot;]]]}]]]}"></astro-island>'
        item = parse_openai_cookbook(raw, "https://developers.openai.com/cookbook")[0]
        self.assertEqual(item["url"], "https://developers.openai.com/cookbook/articles/example")
        self.assertEqual(item["categories"], ["agents"])
        self.assertEqual(item["publishedAt"], "2026-10-23T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
