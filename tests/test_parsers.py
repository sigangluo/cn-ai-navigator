import gzip
import io
import json
import sys
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from update import fetch_feed, parse_feed, parse_aiera_feed, enrich_deepmind_categories, parse_qwen_blog, parse_hn_feed, parse_bluesky_feed, parse_youtube_feed, parse_papers, parse_hf_trending, parse_trending, parse_aa_leaderboard, parse_publisher_page, parse_official_listing, parse_stanford_listing, parse_kimi_page, parse_cohere_page, parse_llamaindex_page, parse_infoq_ai_feed, parse_metr_feed, parse_deepseek_updates, parse_github_topic, parse_aa_articles, parse_openai_cookbook, parse_modelscope_models, parse_baai_news, _MetaDescription  # noqa: E402


class ParserTests(unittest.TestCase):
    def test_aiera_rss_keeps_valid_items_when_a_teaser_ends_mid_character(self):
        raw = (b'<?xml version="1.0" encoding="UTF-8"?><rss><channel><item>'
               b'<title>Research news</title><link>https://aiera.com.cn/post</link>'
               b'<description><![CDATA[Official teaser \xe6\x9c\xe2\x80\xa6]]></description>'
               b'</item></channel></rss>')
        with self.assertRaises(ET.ParseError):
            parse_feed(raw, "https://aiera.com.cn/")
        item = parse_aiera_feed(raw, "https://aiera.com.cn/")[0]
        self.assertEqual(item["url"], "https://aiera.com.cn/post")
        self.assertEqual(item["description"], "Official teaser …")

    def test_modelscope_download_ranking_keeps_public_original_links_and_metrics(self):
        raw = json.dumps({"success": True, "data": {"models": [
            {"id": "iic/model-a", "display_name": "模型 A", "description": "官方简介", "downloads": 123,
             "likes": 4, "tasks": ["text-generation"], "private": False,
             "created_at": "2026-10-01T00:00:00Z", "last_modified": "2026-11-01T00:00:00Z", "license": "Apache-2.0"},
            {"id": "owner/private", "downloads": 200, "private": True}
        ]}}).encode()
        items = parse_modelscope_models(raw)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://www.modelscope.cn/models/iic/model-a")
        self.assertEqual(items[0]["metrics"], {"downloads": 123, "likes": 4})
        self.assertEqual(items[0]["categories"], ["text-generation"])
        self.assertEqual((items[0]["modelId"], items[0]["createdAt"], items[0]["updatedAt"], items[0]["license"]),
                         ("iic/model-a", "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z", "Apache-2.0"))
        self.assertNotIn("publishedAt", items[0])

    def test_baai_news_preserves_publisher_category_and_original_destination(self):
        raw = json.dumps({"items": [
            {"category": "media", "title": "媒体报道", "description": "媒体简介", "source_url": "https://news.example.org/story",
             "published_at": "2026-11-05T02:00:00Z", "is_published": True},
            {"category": "news", "title": "智源新闻", "description": "智源简介", "source_url": "https://mp.weixin.qq.com/s/abc",
             "published_at": "2026-11-06T02:00:00Z", "is_published": True}
        ]}).encode()
        media = parse_baai_news(raw, "media")
        news = parse_baai_news(raw, "news")
        self.assertEqual((media[0]["url"], media[0]["categories"]), ("https://news.example.org/story", ["媒体报道"]))
        self.assertEqual((news[0]["url"], news[0]["categories"]), ("https://mp.weixin.qq.com/s/abc", ["最新新闻"]))
        self.assertEqual(news[0]["publishedAt"], "2026-11-06T02:00:00Z")

    def test_official_listings_keep_dated_original_cards(self):
        meta = b'<div class="_8034"><p class="_8w6f _8wl0 _8w6h">Jul 27, 2026</p><h4 class="_8w6a _8w6e _8w60 _8xok _8x78">Research</h4><h4 class="_8w6a _8w6e _8w61">Model update</h4><a class="_8xc5 more" href="/blog/model-update/">Read</a></div>'
        self.assertEqual(parse_official_listing(meta, "meta-ai", "https://ai.meta.com/blog/")[0]["categories"], ["Research"])
        shlab = b'<a href="/news/123"><h2>Research update</h2><p class="u-time">2026-11-06</p></a>'
        self.assertEqual(parse_official_listing(shlab, "shlab", "https://www.shlab.org.cn/info")[0]["url"], "https://www.shlab.org.cn/news/123")
        amazon = b'<li class="SearchResultsModule-results-item"><div class="PromoF-title"><a href="https://www.amazon.science/blog/post">Post</a></div><div class="PromoF-authors"><a>Alice</a></div><div class="PromoF-date">November 5, 2026</div><div class="PromoF-description">Official teaser.</div><div class="PromoF-category"><a>Machine learning</a></div></li>'
        item = parse_official_listing(amazon, "amazon-science", "https://www.amazon.science/blog/")[0]
        self.assertEqual((item["authors"], item["categories"], item["description"]), (["Alice"], ["Machine learning"], "Official teaser."))
        ibm = b'<article data-testid="blog-post-card"><h2><a href="/blog/post"><span>Post</span></a></h2><time dateTime="2026-10-27T12:00:00Z">27 Oct</time><span class="cds--tag__label">AI</span><span class="cds--tag__label">Research</span></article>'
        self.assertEqual(parse_official_listing(ibm, "ibm-research", "https://research.ibm.com/")[0]["categories"], ["AI", "Research"])

    def test_stanford_listing_confirms_year_on_original_article(self):
        listing = b'<div class="ContentItem_root__a"><a class="ContentItem_titleLink__b" href="/news/post">Research post</a><span class="Typography_mono-small__c">Sep 25</span></div>'
        detail = lambda url: b'children\\":\\"Sep 25, 2026\\"'
        item = parse_stanford_listing(listing, "https://hai.stanford.edu/news", detail)[0]
        self.assertEqual(item["publishedAt"], "2026-09-25T00:00:00Z")

    def test_hn_feed_selects_ai_discussions_and_keeps_hn_permalinks(self):
        raw = b'<rss><channel><item><title>AI model release</title><link>https://example.org/release</link><comments>https://news.ycombinator.com/item?id=123</comments><pubDate>Wed, 04 Nov 2026 00:00:00 GMT</pubDate><description>Comments</description></item><item><title>New spreadsheet feature</title><comments>https://news.ycombinator.com/item?id=456</comments><pubDate>Wed, 04 Nov 2026 00:00:00 GMT</pubDate></item></channel></rss>'
        items = parse_hn_feed(raw)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://news.ycombinator.com/item?id=123")
        self.assertEqual(items[0]["publishedAt"], "2026-11-04T00:00:00Z")
        self.assertEqual(items[0]["description"], "")

    def test_hn_feed_orders_newest_posts_first(self):
        raw = b'<rss><channel><item><title>AI discussion yesterday</title><comments>https://news.ycombinator.com/item?id=123</comments><pubDate>Wed, 04 Nov 2026 10:00:00 GMT</pubDate></item><item><title>LLM discussion today</title><comments>https://news.ycombinator.com/item?id=456</comments><pubDate>Thu, 05 Nov 2026 01:00:00 GMT</pubDate></item></channel></rss>'
        self.assertEqual([item["url"] for item in parse_hn_feed(raw)], [
            "https://news.ycombinator.com/item?id=456",
            "https://news.ycombinator.com/item?id=123",
        ])

    def test_bluesky_feed_keeps_only_original_author_posts_and_public_metrics(self):
        post = {"uri": "at://did:plc:abc/app.bsky.feed.post/xyz123", "author": {"handle": "example.org"}, "record": {"text": "AI research update", "createdAt": "2026-11-04T01:00:00Z"}, "likeCount": 12, "repostCount": 3, "replyCount": 4}
        reply = {**post, "record": {**post["record"], "reply": {"parent": {}}}}
        repost = {**post, "author": {"handle": "other.org"}}
        raw = json.dumps({"feed": [{"post": repost, "reason": {"$type": "app.bsky.feed.defs#reasonRepost"}}, {"post": reply}, {"post": post}]}).encode()
        items = parse_bluesky_feed(raw, "example.org")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://bsky.app/profile/example.org/post/xyz123")
        self.assertEqual(items[0]["metrics"], {"likes": 12, "reposts": 3, "comments": 4})

    def test_bluesky_ai_filter_excludes_unrelated_author_posts(self):
        post = {"uri": "at://did:plc:abc/app.bsky.feed.post/xyz123", "author": {"handle": "example.org"}, "record": {"text": "Weekend bike ride", "createdAt": "2026-11-04T01:00:00Z"}}
        ai_post = {**post, "record": {**post["record"], "text": "Claude model evaluation"}}
        raw = json.dumps({"feed": [{"post": post}, {"post": ai_post}]}).encode()
        self.assertEqual(parse_bluesky_feed(raw, "example.org", ai_only=True)[0]["title"], "Claude model evaluation")

    def test_rss_keeps_publisher_category_and_original_url(self):
        raw = b'<rss><channel><item><title>Research update</title><link>https://example.org/post</link><pubDate>Wed, 04 Nov 2026 00:00:00 GMT</pubDate><category>Research</category></item></channel></rss>'
        result = parse_feed(raw, "https://example.org")
        self.assertEqual(result[0]["categories"], ["Research"])
        self.assertEqual(result[0]["url"], "https://example.org/post")
        self.assertEqual(result[0]["publishedAt"], "2026-11-04T00:00:00Z")

    def test_feed_preserves_official_authors_and_categories_beyond_three(self):
        raw = b'<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item><title>Research update</title><link>https://example.org/post</link><dc:creator>Alice Chen</dc:creator><dc:creator>Bob Smith</dc:creator><category>News</category><category>Research</category><category>Models</category><category>Safety</category><category>Safety</category></item></channel></rss>'
        item = parse_feed(raw, "https://example.org")[0]
        self.assertEqual(item["authors"], ["Alice Chen", "Bob Smith"])
        self.assertEqual(item["categories"], ["News", "Research", "Models", "Safety"])
        long_categories = ''.join(f'<category>Topic {index}</category>' for index in range(17))
        long_feed = f'<rss><channel><item><title>Another post</title><link>https://example.org/another</link>{long_categories}</item></channel></rss>'.encode()
        self.assertEqual(len(parse_feed(long_feed, "https://example.org")[0]["categories"]), 17)

    def test_atom_author_names_are_read_without_email_addresses(self):
        raw = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Paper</title><link href="https://example.org/paper"/><author><name>Alice Chen</name><email>alice@example.org</email></author><author><name>Bob Smith</name></author></entry></feed>'
        self.assertEqual(parse_feed(raw, "https://example.org")[0]["authors"], ["Alice Chen", "Bob Smith"])
        rss = b'<rss><channel><item><title>Post</title><link>https://example.org/post</link><author>editor@example.org</author></item></channel></rss>'
        self.assertNotIn("authors", parse_feed(rss, "https://example.org")[0])

    def test_deepmind_news_category_joins_only_matching_original_url(self):
        items = [{"url": "https://deepmind.google/blog/matching/", "categories": []},
                 {"url": "https://deepmind.google/blog/other/", "categories": []}]
        html = b'<article class="card card-blog"><a href=/blog/matching/>Read</a><span class="text-caption meta__category">Science</span></article>'
        enrich_deepmind_categories(items, html, "https://deepmind.google/blog/")
        self.assertEqual(items[0]["categories"], ["Science"])
        self.assertEqual(items[1]["categories"], [])

    def test_feed_category_filter_applies_before_item_limit(self):
        unrelated = ''.join(f'<item><title>Product {i}</title><link>https://openai.com/p/{i}</link><category>Product</category></item>' for i in range(15))
        selected = '<item><title>Paper</title><link>https://openai.com/research/paper</link><category>Publication</category><pubDate>Fri, 06 Nov 2026 00:00:00 GMT</pubDate></item>'
        raw = f'<rss><channel>{unrelated}{selected}</channel></rss>'.encode()
        items = parse_feed(raw, "https://openai.com/research/index/", {"Research", "Publication"})
        self.assertEqual([item["title"] for item in items], ["Paper"])

    def test_qwen_index_uses_original_intro_and_publication_date(self):
        articles = [
            {"title": "Older", "path": "older", "language": "en-US", "content": "Full article must stay out", "extra": {"date": "2026-11-01T00:00:00+08:00", "introduction": "Original older introduction.", "tags": ["Research"]}},
            {"title": "Newer", "path": "newer", "language": "en-US", "content": "Full article must stay out", "extra": {"date": "2026-11-06T00:00:00+08:00", "introduction": "Publisher's newer teaser.", "tags": ["Release"]}},
            {"title": "Other language", "path": "other", "language": "zh-CN", "extra": {"date": "2026-11-07T00:00:00+08:00"}},
        ]
        items = parse_qwen_blog(json.dumps({"success": True, "data": {"articles": articles}}).encode())
        self.assertEqual([item["title"] for item in items], ["Newer", "Older"])
        self.assertEqual(items[0]["url"], "https://qwen.ai/blog?id=newer")
        self.assertEqual(items[0]["publishedAt"], "2026-11-05T16:00:00Z")
        self.assertEqual(items[0]["categories"], ["Release"])
        self.assertEqual(items[0]["description"], "Publisher's newer teaser.")
        self.assertNotIn("Full article", str(items))

    def test_feed_revalidation_reuses_saved_items_on_304(self):
        previous = {"items": [{"title": "Saved"}], "httpValidators": {"etag": '"abc"'}}
        def unchanged(request, timeout):
            self.assertEqual(request.get_header("If-none-match"), '"abc"')
            raise urllib.error.HTTPError(request.full_url, 304, "Not Modified", Message(), None)
        with patch("update.urllib.request.urlopen", side_effect=unchanged):
            body, validators = fetch_feed("https://example.org/rss", previous)
        self.assertIsNone(body)
        self.assertEqual(validators, previous["httpValidators"])

    def test_feed_request_decodes_gzip_even_without_encoding_header(self):
        xml = b'<rss><channel><item><title>News</title><link>https://example.org/news</link></item></channel></rss>'
        response = io.BytesIO(gzip.compress(xml))
        response.headers = Message()
        with patch("update.urllib.request.urlopen", return_value=response):
            body, _ = fetch_feed("https://example.org/feed", {})
        self.assertEqual(parse_feed(body, "https://example.org")[0]["title"], "News")

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

    def test_ranked_resources_keep_results_beyond_ten(self):
        records = [{"id": f"org/model-{index}", "likes": index} for index in range(26)]
        items = parse_hf_trending(json.dumps(records).encode(), "models")
        self.assertEqual(len(items), 25)
        self.assertEqual(items[-1]["title"], "org/model-24")

    def test_trending_rows_and_void_tags(self):
        raw = b'<article class="Box-row"><h2><a href="/org/repo"> org / repo </a></h2><p>A useful repo<img src="x"></p><a href="/org/repo/stargazers"><svg></svg>12,345</a><span>678 stars today</span></article>'
        result = parse_trending(raw)
        self.assertEqual(result[0]["title"], "org/repo")
        self.assertEqual(result[0]["description"], "A useful repo")
        self.assertEqual(result[0]["metrics"], {"stars": 12345, "starsToday": 678})

    def test_github_trending_keeps_full_available_page(self):
        rows = ''.join(f'<article class="Box-row"><h2><a href="/org/repo-{index}">org / repo-{index}</a></h2></article>' for index in range(17))
        items = parse_trending(rows.encode())
        self.assertEqual(len(items), 17)
        self.assertEqual(items[-1]["title"], "org/repo-16")

    def test_aa_leaderboard_requires_sorted_intelligence_scores(self):
        rows = ''.join(f'<tr><td><div>Model {i}</div></td><td>1M</td><td>Lab</td><td><div>{10-i}</div></td><td><a href="/models/model-{i}">Model</a></td></tr>' for i in range(6))
        result = parse_aa_leaderboard(f'<table><tbody>{rows}</tbody></table>'.encode())
        self.assertEqual(result[0]["title"], "Model 0")
        self.assertEqual(result[0]["metrics"], {"intelligenceIndex": 10})
        self.assertEqual(result[0]["provider"], "Lab")

    def test_aa_leaderboard_keeps_more_than_ten_ranked_models(self):
        rows = ''.join(f'<tr><td><div>Model {index}</div></td><td>1M</td><td>Lab</td><td><div>{40-index}</div></td><td><a href="/models/model-{index}">Model</a></td></tr>' for index in range(30))
        items = parse_aa_leaderboard(f'<table><tbody>{rows}</tbody></table>'.encode())
        self.assertEqual(len(items), 25)
        self.assertEqual(items[-1]["title"], "Model 24")

    def test_feed_keeps_more_than_twelve_items_when_available(self):
        entries = ''.join(f'<item><title>Post {index}</title><link>https://example.org/{index}</link></item>' for index in range(26))
        items = parse_feed(f'<rss><channel>{entries}</channel></rss>'.encode(), "https://example.org")
        self.assertEqual(len(items), 24)
        self.assertEqual(items[-1]["title"], "Post 23")

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
