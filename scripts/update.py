#!/usr/bin/env python3
"""Refresh the small, link-only snapshot consumed by the static dashboard."""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import gzip
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "sources.json"
OUTPUT = ROOT / "site" / "data" / "latest.json"
USER_AGENT = "cn-ai-navigator/1.0 (public-link-index)"
MAX_BYTES = 5_000_000
MAX_ITEMS = 24
MAX_RANKING_ITEMS = 25
MAX_PREVIEW = 1_800
MAX_ABSTRACT = 5_000
MAX_CATEGORIES = 64
FEED_PARSER_VERSION = 4
WORKERS = 12                 # sources refreshed in parallel
HOST_CONCURRENCY = 3         # simultaneous requests to one host, however many sources share it
REQUEST_DEADLINE = 40        # wall-clock seconds for one response body
ERROR_AFTER_FAILURES = 2     # a source shows as "error" only after this many consecutive failed refreshes
TRANSIENT_STATUSES = frozenset({408, 500, 502, 503, 504})


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean(value, limit=240):
    value = unescape(str(value or ""))
    value = re.sub(r"<(?:style|script)\b[^>]*>.*?</(?:style|script)\s*>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<!--.*?-->", " ", value, flags=re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", unescape(value)).strip()
    if len(value) <= limit:
        return value
    trimmed = value[:limit - 1]
    if " " in trimmed and trimmed.rfind(" ") > limit * .7:
        trimmed = trimmed.rsplit(" ", 1)[0]
    return trimmed.rstrip(" ,.;:") + "…"


def publisher_description(value, title="", limit=MAX_PREVIEW):
    if len(value) > 4_000 and re.search(r"<p\b", value, re.I):
        paragraphs = re.findall(r"<p\b[^>]*>(.*?)</p>", value, flags=re.I | re.S)
        excerpts = [clean(paragraph, 800) for paragraph in paragraphs]
        excerpts = [paragraph for paragraph in excerpts if len(paragraph) >= 40 and paragraph != title]
        if excerpts:
            value = " ".join(excerpts[:2])
    value = re.sub(r"^arXiv:\S+\s+Announce Type:\s*\w+\s+Abstract:\s*", "", clean(value, MAX_ABSTRACT + 500), flags=re.I)
    if title:
        value = re.sub(r"\s+The (?:article|post) " + re.escape(title) + r" appeared first on .+?\s*\.?$", "", value, flags=re.I)
    value = clean(value, limit)
    if value == title or re.match(r"^(Animated carousel|Image of |Photo of |Figure \d+:)", value, re.I):
        return ""
    return value


def feed_description(entry, title):
    candidates = []
    for child in entry:
        if child.tag.rsplit("}", 1)[-1].lower() not in {"description", "summary"}:
            continue
        raw = "".join(child.itertext()).strip()
        meta = re.search(r'<meta\s+name=["\']description["\'][^>]*content=(["\'])(.*?)\1', raw, re.I | re.S)
        if meta:
            candidates.append(meta.group(2))
        candidates.append(raw)
    for candidate in candidates:
        limit = MAX_ABSTRACT if candidate.lstrip().startswith("arXiv:") else MAX_PREVIEW
        description = publisher_description(candidate, title, limit)
        if description:
            return description
    return ""


def safe_url(value, base=""):
    url = urljoin(base, str(value or "").strip())
    parsed = urlparse(url)
    return url if parsed.scheme in ("http", "https") and parsed.netloc else ""


class DeadlineExceeded(TimeoutError):
    """The response body kept streaming past REQUEST_DEADLINE; not worth retrying."""


def read_response(response, max_bytes=MAX_BYTES, deadline=REQUEST_DEADLINE):
    chunks, size = [], 0
    limit = time.monotonic() + deadline
    while True:
        if time.monotonic() > limit:
            raise DeadlineExceeded(f"response exceeded {deadline} seconds")
        chunk = response.read(min(65536, max_bytes + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > max_bytes:
            raise ValueError(f"response exceeds {max_bytes} bytes")
    return b"".join(chunks)


_host_slots, _host_lock = {}, threading.Lock()


@contextmanager
def host_slot(url):
    """Bound parallel requests per host so a wide thread pool stays polite."""
    host = urlparse(url).netloc.lower()
    with _host_lock:
        slot = _host_slots.setdefault(host, threading.BoundedSemaphore(HOST_CONCURRENCY))
    with slot:
        yield


def request_bytes(request, timeout=22, max_bytes=MAX_BYTES, retry_statuses=TRANSIENT_STATUSES, attempts=2):
    """Send one request; retry transient HTTP and network failures; return (body, headers).

    Gzip bodies are decoded whether or not the server declares Content-Encoding.
    """
    for attempt in range(1, attempts + 1):
        try:
            with host_slot(request.full_url), urllib.request.urlopen(request, timeout=timeout) as response:
                body = read_response(response, max_bytes)
                headers = response.headers
            if body.startswith(b"\x1f\x8b"):
                with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
                    body = compressed.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ValueError(f"decompressed response exceeds {max_bytes} bytes")
            return body, headers
        except urllib.error.HTTPError as exc:
            if attempt == attempts or exc.code not in retry_statuses or exc.headers.get("Retry-After"):
                raise
        except DeadlineExceeded:
            raise
        except OSError:  # URLError, TLS EOF, resets and socket timeouts
            if attempt == attempts:
                raise
        time.sleep(0.5 * attempt)


def fetch(url, timeout=22, headers=None, data=None, max_bytes=MAX_BYTES, compressed=False):
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, application/json, text/html;q=0.9, */*;q=0.5",
    }
    if compressed:
        request_headers["Accept-Encoding"] = "gzip"
    request_headers.update(headers or {})
    return request_bytes(urllib.request.Request(url, data=data, headers=request_headers), timeout, max_bytes)[0]


def fetch_feed(url, previous, retry_statuses=TRANSIENT_STATUSES):
    """Revalidate a saved publisher feed. Returns (body, validators); body is None on 304."""
    validators = previous.get("httpValidators", {})
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"}
    if validators.get("etag") and previous.get("items"):
        headers["If-None-Match"] = validators["etag"]
    if validators.get("lastModified") and previous.get("items"):
        headers["If-Modified-Since"] = validators["lastModified"]
    try:
        body, response_headers = request_bytes(urllib.request.Request(url, headers=headers), retry_statuses=retry_statuses)
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and previous.get("items"):
            return None, validators
        raise
    fresh = {"etag": response_headers.get("ETag", ""), "lastModified": response_headers.get("Last-Modified", "")}
    return body, {key: value for key, value in fresh.items() if value}


def date_iso(value):
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value.strip())
        except (TypeError, ValueError):
            try:
                dt = datetime.strptime(value.strip(), "%b %d, %Y")
            except ValueError:
                try:
                    dt = datetime.strptime(value.strip(), "%B %d, %Y")
                except ValueError:
                    return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def date_from_dated_path(url):
    """Fallback for publisher links such as /2026/11/02/article.html."""
    match = re.search(r"/(20\d{2})/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])(?:/|$)", url)
    return date_iso(f"{match.group(1)}-{match.group(2)}-{match.group(3)}T00:00:00+08:00") if match else ""


def child_text(node, names):
    for child in node:
        if child.tag.rsplit("}", 1)[-1].lower() in names:
            value = "".join(child.itertext()).strip()
            if value:
                return value
    return ""


def feed_authors(entry, arxiv=False):
    """Read explicit item authors from RSS Dublin Core and Atom author elements."""
    authors = []
    for child in entry:
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag not in {"creator", "author"}:
            continue
        value = child_text(child, {"name"}) if tag == "author" else (child.text or "")
        value = clean(value, 240)
        if tag == "author" and "@" in value:
            trailing_name = re.search(r"\(([^()@]+)\)$", value)
            leading_name = re.fullmatch(r"([^<>@()]+)\s*[<(]\s*[^\s<>]+@[^\s<>]+\s*[>)]", value)
            value = clean((trailing_name or leading_name).group(1), 100) if trailing_name or leading_name else ""
        names = value.split(",") if arxiv and tag == "creator" else [value]
        for name in names:
            name = clean(name, 100)
            if name and name not in authors:
                authors.append(name)
    return authors[:20]


class EmptyFeed(ValueError):
    """A well-formed feed with no entries (arXiv publishes none on weekends)."""


def parse_feed(raw, base, required_categories=None):
    root = ET.fromstring(raw)
    entries = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in ("item", "entry")]
    if not entries:
        raise EmptyFeed("feed contains no entries")
    items = []
    seen = set()
    for entry in entries:
        if required_categories and not any(
                child.tag.rsplit("}", 1)[-1].lower() == "category"
                and clean(child.attrib.get("term") or child.text, 48) in required_categories
                for child in entry):
            continue
        title = clean(child_text(entry, {"title"}))
        link = child_text(entry, {"link"})
        if not link:
            for child in entry:
                if child.tag.rsplit("}", 1)[-1].lower() == "link":
                    if child.attrib.get("rel", "alternate") == "alternate":
                        link = child.attrib.get("href", "")
                        break
        link = safe_url(link, base)
        if not title or not link or link in seen:
            continue
        seen.add(link)
        categories = []
        for child in entry:
            if child.tag.rsplit("}", 1)[-1].lower() == "category":
                label = clean(child.attrib.get("term") or child.text, 48)
                if label and label not in categories:
                    categories.append(label)
        item = {
            "title": title,
            "url": link,
            "publishedAt": date_iso(child_text(entry, {"published"}) or child_text(entry, {"pubdate", "updated", "date"})) or date_from_dated_path(link),
            "categories": categories[:MAX_CATEGORIES],
            "description": feed_description(entry, title),
        }
        authors = feed_authors(entry, urlparse(link).netloc.endswith("arxiv.org"))
        if authors:
            item["authors"] = authors
        items.append(item)
        if len(items) >= MAX_ITEMS:
            break
    if not items:
        raise ValueError("feed has no usable links")
    return items


def parse_aiera_feed(raw, base):
    """The publisher sometimes cuts a UTF-8 character inside an RSS CDATA teaser."""
    items = parse_feed(raw.decode("utf-8", errors="replace").encode("utf-8"), base)
    for item in items:
        item["description"] = item["description"].replace("\ufffd", "")
    return items


def enrich_deepmind_categories(items, raw, base):
    """Join the official news cards to RSS items only by their original URL."""
    html = raw.decode("utf-8", errors="replace")
    rows = re.findall(r'<article\b[^>]*class="[^"]*\bcard-blog\b[^"]*"[^>]*>(.*?)</article>', html, re.S)
    categories_by_url = {}
    for row in rows:
        link = re.search(r'href=["\']?(/blog/[^\s"\'<>]+)', row)
        category = re.search(r'<span class="text-caption meta__category">(.*?)</span>', row, re.S)
        if link and category:
            url = safe_url(link.group(1), base).rstrip("/")
            label = clean(category.group(1), 48)
            if url and label:
                categories_by_url[url] = label
    for item in items:
        label = categories_by_url.get(item["url"].rstrip("/"))
        if label and label not in item["categories"]:
            item["categories"].append(label)
    return items


def parse_qwen_blog(raw):
    """Read Qwen's own article index, using its introduction rather than article HTML."""
    payload = json.loads(raw)
    data = payload.get("data") if isinstance(payload, dict) else None
    articles = data.get("articles") if isinstance(data, dict) else None
    if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(articles, list):
        raise ValueError("Qwen article index is missing")
    items = []
    for article in articles:
        if not isinstance(article, dict) or article.get("language") != "en-US":
            continue
        extra = article.get("extra") or {}
        if not isinstance(extra, dict):
            continue
        title = clean(article.get("title"))
        path = article.get("path", "")
        published = date_iso(extra.get("date")) if isinstance(extra.get("date"), str) else ""
        if not title or not isinstance(path, str) or not re.fullmatch(r"[a-zA-Z0-9._-]+", path) or not published:
            continue
        tags = extra.get("tags") or []
        categories = [clean(tag, 48) for tag in tags if isinstance(tag, str)] if isinstance(tags, list) else []
        items.append({"title": title, "url": "https://qwen.ai/blog?" + urlencode({"id": path}),
                      "publishedAt": published, "categories": categories[:MAX_CATEGORIES],
                      "description": publisher_description(extra.get("introduction") or extra.get("description"), title)})
    if not items:
        raise ValueError("Qwen article index has no dated English articles")
    return sorted(items, key=lambda item: item["publishedAt"], reverse=True)[:MAX_ITEMS]


HN_AI_TITLE = re.compile(
    r"\b(?:ai|llms?|gpts?|openai|anthropic|claude|gemini|deepseek|qwen|kimi|llama|"
    r"machine learning|deep learning|neural|transformers?|inference|agents?|agentic|"
    r"language models?|robotics|vibe coding)\b", re.I)


def parse_hn_feed(raw):
    """Keep AI-related front-page stories and point to the HN discussion."""
    root = ET.fromstring(raw)
    items = []
    for entry in root.findall("./channel/item"):
        title = clean(child_text(entry, {"title"}))
        comments = safe_url(child_text(entry, {"comments"}))
        parsed = urlparse(comments)
        story_id = parse_qs(parsed.query).get("id", [""])[0]
        if (not HN_AI_TITLE.search(title) or parsed.netloc != "news.ycombinator.com"
                or parsed.path != "/item" or not story_id.isdigit()):
            continue
        published = date_iso(child_text(entry, {"pubdate"}))
        if not published:
            continue
        items.append({"title": title, "url": comments, "publishedAt": published,
                      "categories": [], "description": ""})
    if not items:
        raise ValueError("HN front page has no dated AI discussions")
    return sorted(items, key=lambda item: item["publishedAt"], reverse=True)[:MAX_ITEMS]


def enrich_hn_metrics(items):
    for item in items:
        story_id = parse_qs(urlparse(item["url"]).query)["id"][0]
        try:
            story = json.loads(fetch(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json", timeout=5))
        except (OSError, ValueError):
            continue
        if not isinstance(story, dict) or story.get("id") != int(story_id):
            continue
        metrics = {}
        if isinstance(story.get("score"), int):
            metrics["upvotes"] = story["score"]
        if isinstance(story.get("descendants"), int):
            metrics["comments"] = story["descendants"]
        if metrics:
            item["metrics"] = metrics
    return items


def parse_bluesky_feed(raw, actor, ai_only=False):
    """Index an author's own public posts, excluding reposts and replies."""
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("feed"), list):
        raise ValueError("Bluesky author feed is missing")
    items = []
    for entry in payload["feed"]:
        if not isinstance(entry, dict) or entry.get("reason"):
            continue
        post = entry.get("post") or {}
        author = post.get("author") or {}
        record = post.get("record") or {}
        if author.get("handle") != actor or record.get("reply"):
            continue
        match = re.fullmatch(r"at://[^/]+/app\.bsky\.feed\.post/([a-zA-Z0-9]+)", post.get("uri", ""))
        title = clean(record.get("text"), 240)
        published = date_iso(record.get("createdAt"))
        if not match or not title or not published or (ai_only and not HN_AI_TITLE.search(title)):
            continue
        metrics = {}
        for field, key in (("likeCount", "likes"), ("repostCount", "reposts"), ("replyCount", "comments")):
            if isinstance(post.get(field), int):
                metrics[key] = post[field]
        item = {"title": title, "url": f"https://bsky.app/profile/{actor}/post/{match.group(1)}",
                "publishedAt": published, "categories": [], "description": ""}
        if metrics:
            item["metrics"] = metrics
        items.append(item)
        if len(items) >= MAX_ITEMS:
            break
    if not items:
        raise ValueError("Bluesky author has no dated original posts")
    return items


def reddit_oauth_token(client_id, client_secret, user_agent):
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
    raw = fetch("https://www.reddit.com/api/v1/access_token", headers={
        "Authorization": f"Basic {credentials}", "User-Agent": user_agent,
        "Content-Type": "application/x-www-form-urlencoded",
    }, data=b"grant_type=client_credentials")
    token = json.loads(raw).get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("Reddit OAuth token missing")
    return token


def parse_reddit_listing(raw, subreddit, cutoff=None):
    """Use only recent, visible post metadata; each successful fetch replaces prior items."""
    payload = json.loads(raw)
    children = (payload.get("data") or {}).get("children") if isinstance(payload, dict) else None
    if not isinstance(children, list):
        raise ValueError("Reddit listing is missing")
    cutoff = cutoff or datetime.now(timezone.utc) - timedelta(hours=48)
    items = []
    for child in children:
        post = child.get("data") if isinstance(child, dict) else None
        if not isinstance(post, dict) or post.get("removed_by_category") or post.get("author") == "[deleted]":
            continue
        title = clean(post.get("title"))
        permalink = post.get("permalink", "")
        url = safe_url(permalink, "https://www.reddit.com")
        created = post.get("created_utc")
        if (not title or title in {"[removed]", "[deleted]"} or
                not isinstance(created, (int, float)) or not isinstance(permalink, str) or
                not permalink.lower().startswith(f"/r/{subreddit}/comments/".lower()) or
                urlparse(url).netloc != "www.reddit.com"):
            continue
        published = datetime.fromtimestamp(created, timezone.utc)
        if published < cutoff or published > datetime.now(timezone.utc) + timedelta(minutes=5):
            continue
        item = {"title": title, "url": url, "publishedAt": date_iso(published.isoformat()),
                "categories": [], "description": ""}
        flair = clean(post.get("link_flair_text"), 48)
        if flair:
            item["categories"] = [flair]
        metrics = {}
        for field, key in (("score", "upvotes"), ("num_comments", "comments")):
            if isinstance(post.get(field), int) and not isinstance(post[field], bool):
                metrics[key] = post[field]
        if metrics:
            item["metrics"] = metrics
        items.append(item)
        if len(items) >= MAX_ITEMS:
            break
    return sorted(items, key=lambda item: item["publishedAt"], reverse=True)


def parse_x_user_id(raw, username):
    payload = json.loads(raw)
    user = payload.get("data") if isinstance(payload, dict) else None
    user_id = user.get("id") if isinstance(user, dict) else None
    if (not isinstance(user_id, str) or not re.fullmatch(r"[0-9]{1,19}", user_id)
            or user.get("username", "").lower() != username.lower()):
        raise ValueError("X user lookup did not match the configured account")
    return user_id


def parse_x_posts(raw, username):
    payload = json.loads(raw)
    if not isinstance(payload, dict) or ("data" not in payload and "meta" not in payload):
        raise ValueError("X posts response is missing")
    posts = payload.get("data", [])
    if not isinstance(posts, list):
        raise ValueError("X posts list is invalid")
    items = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        references = post.get("referenced_posts") or []
        if any(ref.get("type") in {"replied_to", "retweeted"} for ref in references if isinstance(ref, dict)):
            continue
        post_id = post.get("id")
        title = clean(post.get("text"), 240)
        published = date_iso(post.get("created_at"))
        if not isinstance(post_id, str) or not post_id.isdigit() or not title or not published:
            continue
        item = {"title": title, "url": f"https://x.com/{username}/status/{post_id}",
                "publishedAt": published, "categories": [], "description": ""}
        public = post.get("public_metrics") or {}
        metrics = {}
        for field, key in (("like_count", "likes"), ("retweet_count", "reposts"), ("reply_count", "comments")):
            if isinstance(public.get(field), int) and not isinstance(public[field], bool):
                metrics[key] = public[field]
        if metrics:
            item["metrics"] = metrics
        items.append(item)
    return sorted(items, key=lambda item: item["publishedAt"], reverse=True)[:MAX_ITEMS]


def parse_youtube_feed(raw, base):
    """Use the channel's public Atom feed, including its own video description and views."""
    root = ET.fromstring(raw)
    entries = [node for node in root if node.tag.rsplit("}", 1)[-1] == "entry"]
    if not entries:
        raise ValueError("YouTube channel feed contains no videos")
    items, seen = [], set()
    for entry in entries:
        title = clean(child_text(entry, {"title"}))
        href = next((node.attrib.get("href", "") for node in entry
                     if node.tag.rsplit("}", 1)[-1] == "link"
                     and node.attrib.get("rel", "alternate") == "alternate"), "")
        url = safe_url(href, base)
        published = date_iso(child_text(entry, {"published"}))
        if not title or not url or not published or url in seen:
            continue
        seen.add(url)
        description = next((node.text or "" for node in entry.iter()
                            if node.tag.rsplit("}", 1)[-1] == "description"), "")
        description_lines = [clean(line, 800) for line in description.splitlines()]
        description_lines = [line for line in description_lines
                             if len(line) >= 30 and not re.search(r"https?://", line[:120])
                             and line.count(",") < 5
                             and not re.search(r"\b(?:patreon|sponsor|subscribe|sign up|discount|use code|would like to thank|available here)\b", line[:160], re.I)]
        item = {"title": title, "url": url, "publishedAt": published,
                "categories": [], "description": publisher_description(" ".join(description_lines[:2]), title, 800)}
        thumbnail = next((node.attrib.get("url", "") for node in entry.iter()
                          if node.tag.rsplit("}", 1)[-1] == "thumbnail"), "")
        thumbnail = safe_url(thumbnail)
        if thumbnail:
            item["thumbnail"] = thumbnail
        statistics = next((node for node in entry.iter()
                           if node.tag.rsplit("}", 1)[-1] == "statistics"), None)
        views = statistics.attrib.get("views", "") if statistics is not None else ""
        if views.isdigit():
            item["metrics"] = {"views": int(views)}
        items.append(item)
        if len(items) >= MAX_ITEMS:
            break
    if not items:
        raise ValueError("YouTube channel feed has no usable videos")
    return items


def parse_papers(raw):
    payload = json.loads(raw)
    if not isinstance(payload, list) or not payload:
        raise ValueError("daily papers API returned no papers")
    items = []
    for item in payload[:MAX_ITEMS]:
        paper = item.get("paper", {})
        paper_id = paper.get("id") or item.get("id")
        url = safe_url("https://huggingface.co/papers/" + str(paper_id)) if paper_id else ""
        title = clean(paper.get("title") or item.get("title"))
        if title and url:
            entry = {"title": title, "url": url,
                     "publishedAt": date_iso(item.get("publishedAt") or paper.get("publishedAt")),
                     "categories": [], "description": clean(paper.get("summary"), MAX_ABSTRACT)}
            authors = [clean(author.get("name"), 100) for author in (paper.get("authors") or [])
                       if isinstance(author, dict) and author.get("name")]
            if authors:
                entry["authors"] = authors
            # A paper's Hugging Face organization tag is not an author affiliation.
            organization = paper.get("organization") or item.get("organization")
            if isinstance(organization, dict):
                organization = organization.get("fullname") or organization.get("name")
            if isinstance(organization, str) and organization.strip():
                entry["linkedOrganization"] = clean(organization, 100)
            metrics = {}
            if isinstance(paper.get("upvotes"), int) and paper["upvotes"] > 0:
                metrics["upvotes"] = paper["upvotes"]
            if isinstance(item.get("numComments"), int) and item["numComments"] > 0:
                metrics["comments"] = item["numComments"]
            if metrics:
                entry["metrics"] = metrics
            items.append(entry)
    if not items:
        raise ValueError("daily papers API has no usable links")
    return items


def parse_hf_trending(raw, resource):
    payload = json.loads(raw)
    if not isinstance(payload, list) or not payload:
        raise ValueError("Hugging Face API returned no ranked resources")
    prefix = {"models": "", "datasets": "datasets/", "spaces": "spaces/"}[resource]
    items = []
    for record in payload[:MAX_RANKING_ITEMS]:
        name = clean(record.get("id"), 120)
        if name.count("/") != 1:
            continue
        url = safe_url(f"https://huggingface.co/{prefix}{name}")
        if url:
            entry = {"title": name, "url": url,
                     "description": clean(record.get("description"), MAX_PREVIEW)}
            metrics = {key: record[key] for key in ("likes", "downloads")
                       if isinstance(record.get(key), int) and record[key] >= 0}
            if metrics:
                entry["metrics"] = metrics
            task = clean(record.get("pipeline_tag"), 60)
            sdk = clean(record.get("sdk"), 60)
            if task:
                entry["task"] = task
            if sdk:
                entry["sdk"] = sdk
            items.append(entry)
    if not items:
        raise ValueError("Hugging Face API has no usable ranked links")
    return items


def parse_modelscope_models(raw):
    payload = json.loads(raw)
    records = payload.get("data", {}).get("models", [])
    if not payload.get("success") or not isinstance(records, list) or not records:
        raise ValueError("ModelScope API returned no models")
    items = []
    for record in records:
        model_id = str(record.get("id", ""))
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", model_id) or record.get("private"):
            continue
        entry = {"title": clean(record.get("display_name") or model_id, 160),
                 "url": safe_url("https://www.modelscope.cn/models/" + model_id),
                 "description": publisher_description(record.get("description", "")),
                 "modelId": model_id}
        for api_field, item_field in (("created_at", "createdAt"), ("last_modified", "updatedAt")):
            timestamp = date_iso(record.get(api_field, ""))
            if timestamp:
                entry[item_field] = timestamp
        license_name = clean(record.get("license", ""), 80)
        if license_name:
            entry["license"] = license_name
        metrics = {key: record[key] for key in ("downloads", "likes")
                   if isinstance(record.get(key), int) and record[key] >= 0}
        if metrics:
            entry["metrics"] = metrics
        tasks = record.get("tasks") or []
        if isinstance(tasks, list):
            entry["categories"] = [clean(task, 80) for task in tasks[:3] if isinstance(task, str)]
        items.append(entry)
        if len(items) == MAX_RANKING_ITEMS:
            break
    if not items:
        raise ValueError("ModelScope API has no usable public model links")
    return items


def parse_baai_news(raw, category):
    payload = json.loads(raw)
    records = payload.get("items", [])
    if not isinstance(records, list) or not records:
        raise ValueError("BAAI news API returned no items")
    items = []
    for record in records:
        if record.get("category") != category or not record.get("is_published"):
            continue
        title = clean(record.get("title"), 240)
        raw_date = str(record.get("published_at", ""))
        # Microsecond stamps (…07.536172Z) are bulk-import times, not publication dates: keep the item, leave it undated.
        import_stamp = bool(re.search(r"\.\d{6}", raw_date))
        published = "" if import_stamp else date_iso(raw_date)
        url = safe_url(record.get("source_url", ""))
        if not title or not url or not (published or import_stamp):
            continue
        items.append({"title": title, "url": url, "publishedAt": published,
                      "categories": ["最新新闻" if category == "news" else "媒体报道"],
                      "description": publisher_description(record.get("description", ""), title)})
    if not items:
        raise ValueError("BAAI news API has no dated original links for " + category)
    return sorted(items, key=lambda item: item["publishedAt"], reverse=True)[:MAX_ITEMS]


def parse_trending(raw):
    html = raw.decode("utf-8", errors="replace")
    rows = re.findall(r'<article\b[^>]*class="[^"]*\bBox-row\b[^"]*"[^>]*>(.*?)</article>', html, re.S)
    items = []
    for row in rows:
        heading = re.search(r"<h2\b[^>]*>(.*?)</h2>", row, re.S)
        anchor = re.search(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', heading.group(1), re.S) if heading else None
        if not anchor:
            continue
        href = safe_url(unescape(anchor.group(1)), "https://github.com")
        title = clean(anchor.group(2), 100).replace(" / ", "/").replace(" /", "/").replace("/ ", "/")
        if not href or urlparse(href).netloc != "github.com" or not title:
            continue
        description = re.search(r"<p\b[^>]*>(.*?)</p>", row, re.S)
        entry = {"title": title, "url": href,
                 "description": clean(description.group(1), MAX_PREVIEW) if description else ""}
        stars_link = re.search(r'<a\b[^>]*href="[^"]+/stargazers"[^>]*>(.*?)</a>', row, re.S)
        stars_today = re.search(r'([\d,]+)\s+stars today', row, re.I)
        metrics = {}
        if stars_link:
            total = re.sub(r"[,\s]", "", clean(stars_link.group(1), 40))
            if total.isdigit():
                metrics["stars"] = int(total)
        if stars_today:
            metrics["starsToday"] = int(stars_today.group(1).replace(",", ""))
        if metrics:
            entry["metrics"] = metrics
        items.append(entry)
    if not items:
        raise ValueError("GitHub Trending page has no repository rows")
    return items[:MAX_RANKING_ITEMS]


def parse_aa_leaderboard(raw):
    html = raw.decode("utf-8", errors="replace")
    bodies = re.findall(r"<tbody\b[^>]*>(.*?)</tbody>", html, re.S)
    for body in bodies:
        rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", body, re.S)
        items = []
        scores = []
        for row in rows:
            cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.S)
            detail = re.search(r'href="(/models/[^"/]+)"', row)
            if len(cells) < 4 or not detail:
                continue
            name = clean(cells[0], 140)
            score_text = clean(cells[3], 20)
            if not name or not score_text.isdigit():
                continue
            score = int(score_text)
            scores.append(score)
            items.append({"title": name,
                          "url": safe_url(detail.group(1), "https://artificialanalysis.ai"),
                          "provider": clean(cells[2], 45),
                          "metrics": {"intelligenceIndex": score}})
            if len(items) == MAX_RANKING_ITEMS:
                break
        if len(items) >= 5 and scores == sorted(scores, reverse=True):
            return items
    raise ValueError("Artificial Analysis intelligence ranking table changed")


def html_part(row, pattern, limit=220):
    match = re.search(pattern, row, re.S | re.I)
    return clean(match.group(1), limit) if match else ""


def month_day_iso(value):
    try:
        day = datetime.strptime(value.strip(), "%b %d")
        current = datetime.now(timezone.utc)
        day = day.replace(year=current.year, tzinfo=timezone.utc)
        if day > current + timedelta(days=1):
            day = day.replace(year=current.year - 1)
        return date_iso(day.isoformat())
    except ValueError:
        return ""


def parse_publisher_page(raw, source_id, base):
    """Read only public listing cards with original links and publisher teasers."""
    html = raw.decode("utf-8", errors="replace")
    if source_id == "anthropic-news":
        rows = re.findall(r'<li class="[^"]*\bbxgyUW_row\b[^"]*"[^>]*>(.*?)</li>', html, re.S)
        patterns = {
            "url": r'<a class="bxgyUW_title-link" href="([^"]+)"',
            "title": r'<div class="bxgyUW_title">.*?<h3[^>]*>(.*?)</h3>',
            "description": r'<div class="bxgyUW_description">.*?<p[^>]*>(.*?)</p>',
            "category": r'<div class="bxgyUW_category">.*?<a[^>]*>(.*?)</a>',
            "date": r'<div class="bxgyUW_date"><time[^>]*>(.*?)</time>',
        }
    elif source_id == "anthropic-research":
        rows = re.findall(r'<article class="[^"]*\bnUeDbq_article\b[^"]*"[^>]*>(.*?)</article>', html, re.S)
        patterns = {
            "url": r'<a class="nUeDbq_metaTitleLink" href="([^"]+)"',
            "title": r'<a class="nUeDbq_metaTitleLink"[^>]*>(.*?)</a>',
            "description": r'<p class="nUeDbq_metaDescription"[^>]*>(.*?)</p>',
            "category": r'<header[^>]*>.*?<p><a[^>]*>(.*?)</a></p>',
            "date": r'<header[^>]*>.*?<time[^>]*>(.*?)</time>',
        }
    elif source_id == "mistral":
        rows = re.findall(r'<article class="[^"]*\bjs-post\b[^"]*"[^>]*>(.*?)</article>', html, re.S)
        patterns = {
            "url": r'<a href="([^"]+)"',
            "title": r'<h2 class="[^"]*\bjs-post-title\b[^"]*"[^>]*>(.*?)</h2>',
            "description": r'<p class="[^"]*\btext-body-large\b[^"]*"[^>]*>(.*?)</p>',
            "category": r'<span data-category-slug="[^"]+"[^>]*>.*?<p[^>]*>(.*?)</p>',
            "date": r'<p class="[^"]*\btext-text-tertiary\b[^"]*"[^>]*>(.*?)</p>',
        }
    elif source_id == "openai-dev-blog":
        rows = re.findall(r'<a class="[^"]*\bresource-item\b[^"]*"[^>]*>.*?</a>', html, re.S)
        patterns = {
            "url": r'<a class="[^"]*\bresource-item\b[^"]*" href="([^"]+)"',
            "title": r'<div class="flex-1 line-clamp-2">(.*?)</div>',
            "description": r'<p class="[^"]*\bline-clamp-3\b[^"]*"[^>]*>(.*?)</p>',
            "category": r'<div class="px-4 pt-2 text-sm text-secondary[^>]*>(.*?)</div>',
            "date": r'<div class="px-4 pt-4 text-secondary[^>]*>(.*?)</div>',
        }
    else:
        raise ValueError("unsupported publisher page")
    items, seen = [], set()
    for row in rows:
        href = html_part(row, patterns["url"], 500)
        url = safe_url(href, base)
        title = html_part(row, patterns["title"], 240)
        date_text = html_part(row, patterns["date"], 48)
        published = month_day_iso(date_text) if source_id == "openai-dev-blog" else date_iso(date_text)
        if not url or url in seen or not title or not published:
            continue
        seen.add(url)
        category = html_part(row, patterns["category"], 48)
        items.append({"title": title, "url": url, "publishedAt": published,
                      "categories": [category] if category else [],
                      "description": html_part(row, patterns["description"], MAX_PREVIEW)})
        if len(items) == MAX_ITEMS:
            break
    if not items:
        raise ValueError("publisher page has no usable listing cards")
    return items


def parse_official_listing(raw, source_id, base):
    """Read dated cards from the publisher's public index, without article bodies."""
    html = raw.decode("utf-8", errors="replace")
    if source_id == "meta-ai":
        rows = re.findall(r'<div class="_8034">(.*?)(?=<div class="_8034">|$)', html, re.S)
    elif source_id == "shlab":
        rows = re.findall(r'<a href="/news/\d+"[^>]*>.*?</a>', html, re.S)
    elif source_id == "amazon-science":
        rows = re.findall(r'<li class="SearchResultsModule-results-item"[^>]*>(.*?)</li>', html, re.S)
    elif source_id == "ibm-research":
        rows = re.findall(r'<article\b[^>]*data-testid="blog-post-card"[^>]*>(.*?)</article>', html, re.S)
    else:
        raise ValueError("unsupported official listing")
    items, seen = [], set()
    for row in rows:
        authors = []
        if source_id == "meta-ai":
            title = html_part(row, r'<h4 class="_8w6a _8w6e _8w61">(.*?)</h4>')
            href = html_part(row, r'<a class="_8xc5[^>]*href="([^"]+)"', 500)
            published = date_iso(html_part(row, r'<p class="_8w6f _8wl0 _8w6h">(.*?)</p>', 50))
            category = html_part(row, r'<h4 class="_8w6a _8w6e _8w60 _8xok _8x78">(.*?)</h4>', 48)
            description = html_part(row, r'<p class="_8w6f _8xm4 _8w61 _8w6h">(.*?)</p>', MAX_PREVIEW)
        elif source_id == "shlab":
            title = html_part(row, r'<h2>(.*?)</h2>')
            href = html_part(row, r'<a href="([^"]+)"', 500)
            published = date_iso(html_part(row, r'<p class="u-time">(.*?)</p>', 50))
            category, description = "", ""
        elif source_id == "amazon-science":
            title = html_part(row, r'<div class="PromoF-title">\s*<a[^>]*>(.*?)</a>')
            href = html_part(row, r'<div class="PromoF-title">\s*<a[^>]*href="([^"]+)"', 500)
            published = date_iso(html_part(row, r'<div class="PromoF-date">(.*?)</div>', 50))
            category = html_part(row, r'<div class="PromoF-category">\s*<a[^>]*>(.*?)</a>', 48)
            description = html_part(row, r'<div[^>]*class="PromoF-description"[^>]*>(.*?)</div>', MAX_PREVIEW)
            author_block = re.search(r'<div class="PromoF-authors">(.*?)</div>', row, re.S)
            if author_block:
                authors = [clean(name, 100) for name in re.findall(r'<a[^>]*>(.*?)</a>', author_block.group(1), re.S)]
        else:
            title = html_part(row, r'<h2[^>]*><a[^>]*>.*?<span[^>]*>(.*?)</span>')
            href = html_part(row, r'<h2[^>]*><a[^>]*href="([^"]+)"', 500)
            published = date_iso(html_part(row, r'<time dateTime="([^"]+)"', 50))
            labels = [clean(label, 48) for label in re.findall(r'class="cds--tag__label"[^>]*>(.*?)</span>', row, re.S)]
            if "AI" not in labels:
                continue
            category, description = "", ""
            author = html_part(row, r'<div class="u0Z9b _2Pvpq">(.*?)</div>', 240)
            if author:
                authors = [author]
        url = safe_url(href, base)
        if not title or not url or not published or url in seen:
            continue
        seen.add(url)
        item = {"title": title, "url": url, "publishedAt": published,
                "categories": labels[:MAX_CATEGORIES] if source_id == "ibm-research" else ([category] if category else []),
                "description": description}
        if authors:
            item["authors"] = authors
        items.append(item)
    if not items:
        raise ValueError("official listing has no dated original links")
    return sorted(items, key=lambda item: item["publishedAt"], reverse=True)[:MAX_ITEMS]


def parse_stanford_listing(raw, base, article_fetch=fetch, known=None):
    """Confirm the year on each original article before indexing a month/day card.

    `known` maps URL -> publishedAt confirmed by an earlier refresh, which skips the article request.
    """
    known = known or {}
    html = raw.decode("utf-8", errors="replace")
    rows = re.findall(r'<div class="ContentItem_root__[^" ]+[^\"]*"(.*?)(?=<div class="ContentItem_root__|$)', html, re.S)
    items, seen = [], set()
    for row in rows:
        href = html_part(row, r'class="ContentItem_titleLink__[^\"]+"[^>]*href="([^"]+)"', 500)
        title = html_part(row, r'class="ContentItem_titleLink__[^\"]+"[^>]*>(.*?)</a>')
        short_date = html_part(row, r'Typography_mono-small__[^\"]+"[^>]*>([^<]+)</span>', 30)
        url = safe_url(href, base)
        if not url or url in seen or not title or not re.fullmatch(r'[A-Za-z]{3} \d{1,2}', short_date):
            continue
        seen.add(url)
        if url in known:
            items.append({"title": title, "url": url, "publishedAt": known[url], "categories": [], "description": ""})
            if len(items) >= MAX_ITEMS:
                break
            continue
        try:
            detail = article_fetch(url).decode("utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        dates = re.findall(r'children\\":\\"([A-Za-z]+ \d{1,2}, 20\d{2})\\"', detail)
        published = next((parsed for value in dates if (parsed := date_iso(value))
                          and datetime.fromisoformat(parsed.replace("Z", "+00:00")).strftime("%b %d").replace(" 0", " ")
                          == short_date.replace(" 0", " ")), "")
        if published:
            items.append({"title": title, "url": url, "publishedAt": published,
                          "categories": [], "description": ""})
        if len(items) >= MAX_ITEMS:
            break
    if not items:
        raise ValueError("Stanford HAI has no date-confirmed news cards")
    return sorted(items, key=lambda item: item["publishedAt"], reverse=True)


def parse_kimi_page(raw, base):
    html = raw.decode("utf-8", errors="replace")
    pattern = (r'<div class="menu-card[^\"]*"[^>]*>\s*<a href="(/blog/[^\"]+)"[^>]*>'
               r'.*?<h4 class="card-title[^\"]*"[^>]*>(.*?)</h4>\s*'
               r'<p class="card-date[^\"]*"[^>]*>(.*?)</p>')
    items, seen = [], set()
    for href, heading, date in re.findall(pattern, html, re.S):
        url = safe_url(href, base)
        if not url or url in seen or not date_iso(clean(date)):
            continue
        seen.add(url)
        items.append({"title": clean(heading), "url": url,
                      "publishedAt": date_iso(clean(date)), "categories": [], "description": ""})
        if len(items) == MAX_ITEMS:
            break
    if not items:
        raise ValueError("Kimi blog has no dated cards")
    return items


def parse_cohere_page(raw, base):
    html = raw.decode("utf-8", errors="replace")
    rows = re.finditer(r'<a class="flex flex-1 flex-col[^>]*href="(/blog/[^"#?]+)"[^>]*>(.*?)</a>', html, re.S)
    items, seen = [], set()
    for match in rows:
        url = safe_url(match.group(1), base)
        paragraphs = [clean(p, MAX_PREVIEW) for p in re.findall(r'<p\b[^>]*>(.*?)</p>', match.group(2), re.S)]
        date = next((date_iso(p) for p in paragraphs if re.fullmatch(r'[A-Za-z]+ \d{1,2}, 20\d{2}', p)), "")
        if not url or url in seen or not date or not paragraphs:
            continue
        seen.add(url)
        category_start = html.rfind('<div class="flex flex-wrap gap-1">', 0, match.start())
        category_html = html[category_start:match.start()] if category_start >= 0 and match.start() - category_start < 2000 else ""
        categories = [clean(label, 48) for label in re.findall(r'<span\b[^>]*>(.*?)</span>', category_html, re.S)]
        teaser = next((text for text in paragraphs[1:] if text != paragraphs[0]
                       and not re.fullmatch(r'[A-Za-z]+ \d{1,2}, 20\d{2}|\d+ min read', text)), "")
        items.append({"title": paragraphs[0], "url": url, "publishedAt": date,
                      "categories": categories[:MAX_CATEGORIES], "description": teaser})
    if not items:
        raise ValueError("Cohere blog has no dated cards")
    return sorted(items, key=lambda item: item["publishedAt"], reverse=True)[:MAX_ITEMS]


def parse_llamaindex_page(raw, base):
    html = raw.decode("utf-8", errors="replace")
    match = re.search(r'<script id="BlogSearchData" type="application/json">(.*?)</script>', html, re.S)
    if not match:
        raise ValueError("LlamaIndex blog data missing")
    records = json.loads(unescape(match.group(1)))
    items = []
    for record in records:
        published = date_iso(record.get("publishedDate") or "")
        url = safe_url("/blog/" + str(record.get("slug") or ""), base)
        if not published or not record.get("slug") or not record.get("title") or not url:
            continue
        tags = [clean(tag, 48) for tag in record.get("tags", []) if isinstance(tag, str)]
        items.append({"title": clean(record["title"]), "url": url, "publishedAt": published,
                      "categories": tags[:MAX_CATEGORIES], "description": publisher_description(record.get("excerpt") or "", record["title"])})
        if len(items) == MAX_ITEMS:
            break
    if not items:
        raise ValueError("LlamaIndex blog has no dated posts")
    return items


class _MetaDescription(HTMLParser):
    def __init__(self):
        super().__init__()
        self.description = ""

    def handle_starttag(self, tag, attrs):
        if tag != "meta" or self.description:
            return
        attributes = dict(attrs)
        if attributes.get("name", "").lower() == "description" or attributes.get("property", "").lower() == "og:description":
            self.description = clean(attributes.get("content", ""), MAX_PREVIEW)


def enrich_descriptions(items, known=None, limit=MAX_ITEMS):
    """Fill missing teasers from the publisher's own article metadata.

    `known` maps URL -> teaser saved by an earlier refresh, so only new articles cost a request.
    """
    known = known or {}
    for item in items:
        if not item.get("description") and known.get(item["url"]):
            item["description"] = known[item["url"]]

    def description(item):
        try:
            parser = _MetaDescription()
            parser.feed(fetch(item["url"]).decode("utf-8", errors="replace"))
            return publisher_description(parser.description, item["title"])
        except (OSError, ValueError):
            return ""

    targets = [item for item in items[:limit] if not item.get("description")]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for item, value in zip(targets, pool.map(description, targets)):
            if value:
                item["description"] = value
    return items


AI_TITLE_TERMS = re.compile(
    r"大模型|智能体|机器学习|深度学习|神经网络|算力|多模态|模型|推理|提示词|生成式"
    r"|(?-i:(?<![A-Za-z])AI(?![a-z]))"  # case-sensitive: matches AI / AICon, not maintain / mail
    r"|(?<![A-Za-z])(?:Agents?|LLMs?|GPT|Claude|Gemini|DeepSeek|Qwen|Kimi|OpenAI|Anthropic|MCP)(?![A-Za-z])", re.I)


def parse_infoq_ai_feed(raw, base):
    items = [item for item in parse_feed(raw, base) if AI_TITLE_TERMS.search(item["title"])]
    if not items:
        raise ValueError("InfoQ feed has no AI-related titles")
    for item in items:
        item["url"] = item["url"].split("?", 1)[0]
        if item["publishedAt"]:
            # InfoQ's RSS marks Beijing wall time as GMT; its original page shows the prior Beijing date.
            item["publishedAt"] = date_iso((datetime.fromisoformat(item["publishedAt"].replace("Z", "+00:00")) - timedelta(hours=8)).isoformat())
        if "点击查看原文" in item["description"]:
            item["description"] = ""
    return items[:MAX_ITEMS]


def parse_metr_feed(raw, base):
    """Keep only a short excerpt from METR's publisher-operated full-content RSS."""
    items = parse_feed(raw, base)
    by_url = {item["url"]: item for item in items}
    root = ET.fromstring(raw)
    for entry in root.iter():
        if entry.tag.rsplit("}", 1)[-1] != "item":
            continue
        item = by_url.get(safe_url(child_text(entry, {"link"}), base))
        if not item:
            continue
        content = child_text(entry, {"encoded"})
        paragraph = re.search(r'<p\b[^>]*>(.*?)</p>', content, re.I | re.S)
        if paragraph:
            intro = re.sub(r'<sup\b[^>]*>.*?</sup>', '', paragraph.group(1), flags=re.I | re.S)
            item["description"] = publisher_description(intro, item["title"], 320)
    return items


def parse_deepseek_updates(raw, base):
    html = raw.decode("utf-8", errors="replace")
    headings = list(re.finditer(r'<h[23]\b[^>]*class="[^"]*anchorWithStickyNavbar[^"]*"[^>]*id="([^"]+)"[^>]*>(.*?)</h[23]>', html, re.S))
    items, published = [], ""
    for index, match in enumerate(headings):
        title = clean(match.group(2)).replace("\u200b", "").strip()
        date_match = re.search(r'20\d{2}-\d{2}-\d{2}', title)
        if html[match.start():match.start()+3] == "<h2":
            published = date_iso(date_match.group(0)) if date_match else ""
            continue
        if not published or not title:
            continue
        section = html[match.end():headings[index + 1].start() if index + 1 < len(headings) else len(html)]
        paragraph = re.search(r'<p\b[^>]*>(.*?)</p>', section, re.I | re.S)
        items.append({"title": title, "url": safe_url(base.split("#", 1)[0] + "#" + match.group(1)),
                      "publishedAt": published, "categories": [],
                      "description": publisher_description(paragraph.group(1), title, 500) if paragraph else ""})
        if len(items) == MAX_ITEMS:
            break
    if not items:
        raise ValueError("DeepSeek changelog has no dated entries")
    return items


def parse_github_topic(raw):
    html = raw.decode("utf-8", errors="replace")
    rows = re.findall(r'<article class="border rounded color-shadow-small[^\"]*">(.*?)</article>', html, re.S)
    items = []
    for row in rows:
        heading = re.search(r'<h3\b[^>]*>(.*?)</h3>', row, re.S)
        anchors = re.findall(r'<a\b[^>]*href="(/[^"]+)"[^>]*>(.*?)</a>', heading.group(1), re.S) if heading else []
        if len(anchors) < 2:
            continue
        url = safe_url(anchors[-1][0], "https://github.com")
        title = clean(anchors[0][1], 100) + "/" + clean(anchors[-1][1], 100)
        if not url or urlparse(url).netloc != "github.com":
            continue
        description = html_part(row, r'<p class="color-fg-muted mb-0"[^>]*>(.*?)</p>', MAX_PREVIEW)
        stars = re.search(r'aria-label="([\d,]+) users? starred this repository"', row)
        item = {"title": title, "url": url, "description": description}
        if stars:
            item["metrics"] = {"stars": int(stars.group(1).replace(",", ""))}
        items.append(item)
        if len(items) == MAX_RANKING_ITEMS:
            break
    if not items:
        raise ValueError("GitHub topic page has no repository cards")
    return items


def parse_aa_articles(raw, base):
    html = raw.decode("utf-8", errors="replace")
    embedded = re.search(r'\\"articles\\":\[(.*?)\]', html, re.S)
    if embedded:
        records = json.loads('[' + embedded.group(1).replace('\\"', '"') + ']')
        items = []
        for record in records:
            title = clean(record.get("title"))
            published = date_iso(record.get("date"))
            slug = record.get("slug")
            url = safe_url("/articles/" + str(slug), base) if slug else ""
            if not title or not published or not url:
                continue
            items.append({"title": title, "url": url, "publishedAt": published,
                          "categories": [], "description": publisher_description(record.get("subtitle", ""), title)})
            if len(items) == MAX_ITEMS:
                return items
        if items:
            return items
    rows = re.findall(r'<a class="relative flex flex-col gap-4 group" href="(/articles/[^"#?]+)"[^>]*>(.*?)</a>', html, re.S)
    items, seen = [], set()
    for href, row in rows:
        url = safe_url(href, base)
        title = html_part(row, r'<h3\b[^>]*>(.*?)</h3>')
        published = date_iso(html_part(row, r'<p class="text-xs text-muted-foreground"[^>]*>(.*?)</p>', 48))
        if not url or url in seen or not title or not published:
            continue
        seen.add(url)
        items.append({"title": title, "url": url, "publishedAt": published,
                      "categories": [], "description": ""})
        if len(items) == MAX_ITEMS:
            break
    if not items:
        raise ValueError("Artificial Analysis articles page has no dated cards")
    return items


def parse_openai_cookbook(raw, base):
    html = raw.decode("utf-8", errors="replace")
    match = re.search(r'<astro-island[^>]+component-url="[^"]*CookbookHomepage[^>]+props="([^"]+)"', html, re.S)
    if not match:
        raise ValueError("OpenAI Cookbook listing data missing")
    payload = json.loads(unescape(match.group(1)))
    items = []
    for wrapper in payload.get("entries", [None, []])[1]:
        if not isinstance(wrapper, list) or len(wrapper) < 2 or not isinstance(wrapper[1], dict):
            continue
        record = wrapper[1]
        path = record.get("internal_path", [None, ""])[1]
        title = clean(record.get("title", [None, ""])[1])
        published = date_iso(record.get("date", [None, ""])[1])
        url = safe_url("/cookbook/" + path, base) if path else ""
        if not title or not published or not url:
            continue
        tags = [clean(tag[1], 48) for tag in record.get("tags", [None, []])[1]
                if isinstance(tag, list) and len(tag) > 1 and isinstance(tag[1], str)]
        items.append({"title": title, "url": url, "publishedAt": published,
                      "categories": tags[:3], "description": ""})
        if len(items) == MAX_ITEMS:
            break
    if not items:
        raise ValueError("OpenAI Cookbook has no dated entries")
    return items


def load_previous():
    try:
        return json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"results": {}}


def recent_reddit_items(items):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    return [item for item in items if item.get("publishedAt") and
            datetime.fromisoformat(item["publishedAt"].replace("Z", "+00:00")) >= cutoff]


def redact_social_snapshot():
    """Keep social post content out of committed Git history after deployment upload."""
    payload = load_previous()
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for source in registry["sources"]:
        if source["mode"] in {"reddit", "x_api"}:
            old = payload["results"].get(source["id"], {})
            result = {"status": "directory", "items": []}
            if source["mode"] == "x_api" and old.get("userId"):
                result["userId"] = old["userId"]
            payload["results"][source["id"]] = result
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Credentials:
    """Optional social API credentials, read from the environment when a refresh starts."""

    def __init__(self, environ):
        self.reddit_id = environ.get("REDDIT_CLIENT_ID", "")
        self.reddit_secret = environ.get("REDDIT_CLIENT_SECRET", "")
        self.reddit_agent = environ.get("REDDIT_USER_AGENT", "")
        self.x_token = environ.get("X_BEARER_TOKEN", "")
        self.reddit_ready = bool(self.reddit_id and self.reddit_secret and self.reddit_agent)
        self.x_ready = environ.get("X_API_ENABLED", "").lower() == "true" and bool(self.x_token)
        self._reddit_token, self._lock = None, threading.Lock()

    def reddit_token(self):
        with self._lock:
            if self._reddit_token is None:
                self._reddit_token = reddit_oauth_token(self.reddit_id, self.reddit_secret, self.reddit_agent)
            return self._reddit_token

    def allows(self, mode):
        return {"reddit": self.reddit_ready, "x_api": self.x_ready}.get(mode, True)


class Run:
    """What a fetcher gets: its source, the previous snapshot entry, credentials, and a place for extra fields."""

    def __init__(self, source, old, credentials):
        self.source, self.old, self.credentials = source, old, credentials
        self.extra = {}  # snapshot fields to persist next to `items`, e.g. HTTP validators

    def known(self, field):
        """URL -> saved value from the previous snapshot, skipping empty values."""
        return {item["url"]: item[field] for item in self.old.get("items", []) if item.get(field)}


# mode -> function(run) -> items. Every mode in data/schema.json needs an entry (tests enforce it).
FETCHERS = {}


def fetcher(*modes):
    def register(function):
        for mode in modes:
            FETCHERS[mode] = function
        return function
    return register


@fetcher("feed")
def fetch_feed_source(run):
    source, old = run.source, run.old
    # Reparse once after a parser upgrade, even when the feed URL returns 304.
    saved = old if old.get("feedParserVersion") == FEED_PARSER_VERSION else {}
    body, validators = fetch_feed(source["feed"], saved)
    run.extra["feedParserVersion"] = FEED_PARSER_VERSION
    if validators:
        run.extra["httpValidators"] = validators
    if body is None:
        items = old["items"]
    else:
        try:
            items = (parse_aiera_feed(body, source["url"]) if source.get("lenientXml") else
                     parse_feed(body, source["url"], set(source.get("feedCategories", []))))
        except EmptyFeed:
            if not source.get("allowEmpty"):
                raise
            items = old.get("items", [])  # no new entries today; keep showing the last ones
    if source.get("aiOnly"):
        items = [item for item in items if AI_TITLE_TERMS.search(item["title"])]
        if not items:
            raise ValueError("feed has no AI-related titles")
    if source.get("noDescription"):
        for item in items:  # the feed's description is a citation, not a teaser
            item["description"] = ""
    if source.get("categoryPage"):
        try:
            items = enrich_deepmind_categories(items, fetch(source["url"]), source["url"])
        except (OSError, ValueError):
            pass
    return items


@fetcher("qwen_blog")
def fetch_qwen(run):
    return parse_qwen_blog(fetch(
        "https://qwen.ai/api/v2/article/retrieval?" + urlencode({"language": "en-US", "type": "qwen_ai"}),
        max_bytes=8_000_000, compressed=True))


@fetcher("hn_feed")
def fetch_hn(run):
    return enrich_hn_metrics(parse_hn_feed(fetch(run.source["feed"])))


@fetcher("bluesky")
def fetch_bluesky(run):
    actor = run.source["actor"]
    url = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?" + urlencode(
        {"actor": actor, "limit": 30, "filter": "posts_no_replies"})
    return parse_bluesky_feed(fetch(url), actor, run.source.get("aiOnly", False))


@fetcher("reddit")
def fetch_reddit(run):
    subreddit, creds = run.source["subreddit"], run.credentials
    url = f"https://oauth.reddit.com/r/{subreddit}/hot?limit=25&raw_json=1"
    return parse_reddit_listing(fetch(url, headers={
        "Authorization": f"Bearer {creds.reddit_token()}", "User-Agent": creds.reddit_agent}), subreddit)


@fetcher("x_api")
def fetch_x(run):
    username = run.source["username"]
    headers = {"Authorization": f"Bearer {run.credentials.x_token}"}
    user_id = run.old.get("userId")
    if not isinstance(user_id, str) or not re.fullmatch(r"[0-9]{1,19}", user_id):
        user_id = parse_x_user_id(fetch(f"https://api.x.com/2/users/by/username/{username}", headers=headers), username)
    run.extra["userId"] = user_id
    query = urlencode({"max_results": 5, "exclude": "replies,retweets", "post.fields": "created_at,public_metrics,referenced_posts"})
    return parse_x_posts(fetch(f"https://api.x.com/2/users/{user_id}/tweets?{query}", headers=headers), username)


@fetcher("metr_feed")
def fetch_metr(run):
    return parse_metr_feed(fetch(run.source["feed"]), run.source["url"])


@fetcher("infoq_ai_feed")
def fetch_infoq(run):
    return enrich_descriptions(parse_infoq_ai_feed(fetch(run.source["feed"]), run.source["url"]), run.known("description"))


@fetcher("youtube")
def fetch_youtube(run):
    # YouTube's Atom endpoint intermittently answers 404 for a channel that exists; retry it once.
    body, _ = fetch_feed(run.source["feed"], {}, retry_statuses=TRANSIENT_STATUSES | {404})
    return parse_youtube_feed(body, run.source["url"])


@fetcher("papers")
def fetch_daily_papers(run):
    return parse_papers(fetch(f"https://huggingface.co/api/daily_papers?limit={MAX_ITEMS}"))


@fetcher("papers_trending")
def fetch_trending_papers(run):
    return parse_papers(fetch(f"https://huggingface.co/api/daily_papers?sort=trending&limit={MAX_ITEMS}"))


@fetcher("hf_trending")
def fetch_hf_trending(run):
    resource = run.source["resource"]
    return parse_hf_trending(fetch(f"https://huggingface.co/api/{resource}?sort=trendingScore&direction=-1&limit={MAX_RANKING_ITEMS}"), resource)


@fetcher("modelscope_models")
def fetch_modelscope(run):
    return parse_modelscope_models(fetch(f"https://modelscope.cn/openapi/v1/models?sort=downloads&page_size={MAX_RANKING_ITEMS}"))


@fetcher("baai_news")
def fetch_baai(run):
    return parse_baai_news(fetch("https://www.baai.ac.cn/api/news?page=1&size=200"), run.source["newsCategory"])


@fetcher("github_trending")
def fetch_github_trending(run):
    return parse_trending(fetch(run.source["url"]))


@fetcher("github_topic")
def fetch_github_topic(run):
    return parse_github_topic(fetch(run.source["url"]))


@fetcher("aa_leaderboard")
def fetch_aa_leaderboard(run):
    return parse_aa_leaderboard(fetch(run.source["url"]))


@fetcher("aa_articles")
def fetch_aa_articles(run):
    return parse_aa_articles(fetch(run.source["url"]), run.source["url"])


@fetcher("openai_cookbook")
def fetch_cookbook(run):
    return enrich_descriptions(parse_openai_cookbook(fetch(run.source["url"]), run.source["url"]), run.known("description"))


@fetcher("publisher_page")
def fetch_publisher_page(run):
    return parse_publisher_page(fetch(run.source["url"]), run.source["id"], run.source["url"])


@fetcher("official_listing")
def fetch_official_listing(run):
    return parse_official_listing(fetch(run.source["url"], max_bytes=6_000_000), run.source["id"], run.source["url"])


@fetcher("stanford_listing")
def fetch_stanford(run):
    return parse_stanford_listing(fetch(run.source["url"]), run.source["url"], known=run.known("publishedAt"))


@fetcher("kimi_page")
def fetch_kimi(run):
    return enrich_descriptions(parse_kimi_page(fetch(run.source["url"]), run.source["url"]), run.known("description"))


@fetcher("cohere_page")
def fetch_cohere(run):
    return enrich_descriptions(parse_cohere_page(fetch(run.source["url"]), run.source["url"]), run.known("description"))


@fetcher("llamaindex_page")
def fetch_llamaindex(run):
    return parse_llamaindex_page(fetch(run.source["url"]), run.source["url"])


@fetcher("deepseek_updates")
def fetch_deepseek(run):
    return parse_deepseek_updates(fetch(run.source["url"]), run.source["url"])


KEPT_ON_FAILURE = ("httpValidators", "feedParserVersion", "userId")


def refresh(source, old, credentials, checked):
    """Fetch one source and return (id, snapshot entry, log line). Never raises for network or parse errors."""
    source_id, mode = source["id"], source["mode"]
    run = Run(source, old, credentials)
    try:
        if mode not in FETCHERS:
            raise ValueError("unknown mode")
        items = FETCHERS[mode](run)
        result = {"status": "ok", "checkedAt": checked, "lastSuccessAt": checked, "items": items, **run.extra}
        return source_id, result, f"OK   {source_id}: {len(items)}"
    except (OSError, ET.ParseError, ValueError, TypeError, KeyError) as exc:
        failures = old.get("failures", 0) + 1
        # One failed refresh of a source that was healthy is usually transient: keep it "ok" until it repeats.
        status = "ok" if old.get("status") == "ok" and failures < ERROR_AFTER_FAILURES else "error"
        items = old.get("items", [])
        result = {"status": status, "checkedAt": checked, "lastSuccessAt": old.get("lastSuccessAt", ""),
                  "items": recent_reddit_items(items) if mode == "reddit" else items,
                  "failures": failures, "error": clean(str(exc), 120)}
        result.update({key: old[key] for key in KEPT_ON_FAILURE if old.get(key)})
        return source_id, result, f"ERR  {source_id} ({failures}x): {exc}"


def update(offline=False, only=None):
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    previous_payload = load_previous()
    previous = previous_payload.get("results", {})
    credentials = Credentials(os.environ)
    results, pending, checked = {}, [], now()
    for source in registry["sources"]:
        source_id, mode, old = source["id"], source["mode"], previous.get(source["id"], {})
        if mode == "link" or not credentials.allows(mode):
            results[source_id] = {"status": "directory", "items": []}
        elif offline or (only and source_id not in only):
            result = old.copy() if old else {"status": "pending", "items": []}
            if mode == "reddit":
                result["items"] = recent_reddit_items(result.get("items", []))
            results[source_id] = result
        else:
            pending.append(source)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for source_id, result, message in pool.map(
                lambda source: refresh(source, previous.get(source["id"], {}), credentials, checked), pending):
            results[source_id] = result
            print(message, file=sys.stderr)
    results = {source["id"]: results[source["id"]] for source in registry["sources"]}
    # A partial refresh (--only) must not make the whole snapshot look newer than it is.
    keep_time = offline or bool(only)
    payload = {"generatedAt": previous_payload.get("generatedAt", "") if keep_time and previous_payload.get("generatedAt") else checked,
               "results": results}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(results)} sources)")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="preserve current snapshot, initialize missing entries")
    parser.add_argument("--only", nargs="*", help="refresh selected source IDs")
    parser.add_argument("--redact-social", action="store_true", help="remove social post items before committing snapshot")
    args = parser.parse_args()
    if args.redact_social:
        redact_social_snapshot()
    else:
        update(args.offline, set(args.only) if args.only else None)
