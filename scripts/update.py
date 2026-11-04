#!/usr/bin/env python3
"""Refresh the small, link-only snapshot consumed by the static dashboard."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "sources.json"
OUTPUT = ROOT / "site" / "data" / "latest.json"
USER_AGENT = "cn-ai-navigator/1.0 (public-link-index)"
MAX_BYTES = 5_000_000
MAX_ITEMS = 12
MAX_PREVIEW = 1_800
MAX_ABSTRACT = 5_000


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


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, application/json, text/html;q=0.9, */*;q=0.5",
    })
    with urllib.request.urlopen(req, timeout=22) as response:
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("response exceeds 5 MB")
        return data


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


def parse_feed(raw, base):
    root = ET.fromstring(raw)
    entries = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in ("item", "entry")]
    if not entries:
        raise ValueError("feed contains no entries")
    items = []
    seen = set()
    for entry in entries:
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
        items.append({
            "title": title,
            "url": link,
            "publishedAt": date_iso(child_text(entry, {"pubdate", "published", "updated", "date"})) or date_from_dated_path(link),
            "categories": categories[:3],
            "description": feed_description(entry, title),
        })
        if len(items) >= MAX_ITEMS:
            break
    if not items:
        raise ValueError("feed has no usable links")
    return items


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
    for record in payload[:10]:
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
    return items[:10]


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
            if len(items) == 10:
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


def load_previous():
    try:
        return json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"results": {}}


def update(offline=False, only=None):
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    previous_payload = load_previous()
    previous = previous_payload.get("results", {})
    results = {}
    checked = now()
    pending = []
    for source in registry["sources"]:
        source_id = source["id"]
        mode = source["mode"]
        old = previous.get(source_id, {})
        if mode == "link":
            results[source_id] = {"status": "directory", "items": []}
            continue
        if offline or (only and source_id not in only):
            results[source_id] = old or {"status": "pending", "items": []}
            continue
        pending.append(source)

    def refresh(source):
        source_id = source["id"]
        mode = source["mode"]
        old = previous.get(source_id, {})
        try:
            if mode == "feed":
                items = parse_feed(fetch(source["feed"]), source["url"])
            elif mode == "youtube":
                items = parse_youtube_feed(fetch(source["feed"]), source["url"])
            elif mode == "papers":
                items = parse_papers(fetch("https://huggingface.co/api/daily_papers?limit=12"))
            elif mode == "papers_trending":
                items = parse_papers(fetch("https://huggingface.co/api/daily_papers?sort=trending&limit=12"))
            elif mode == "hf_trending":
                resource = source["resource"]
                items = parse_hf_trending(fetch(f"https://huggingface.co/api/{resource}?sort=trendingScore&direction=-1&limit=10"), resource)
            elif mode == "github_trending":
                items = parse_trending(fetch(source["url"]))
            elif mode == "aa_leaderboard":
                items = parse_aa_leaderboard(fetch(source["url"]))
            elif mode == "publisher_page":
                items = parse_publisher_page(fetch(source["url"]), source_id, source["url"])
            else:
                raise ValueError("unknown mode")
            return source_id, {"status": "ok", "checkedAt": checked,
                               "lastSuccessAt": checked, "items": items}, f"OK   {source_id}: {len(items)}"
        except (OSError, ET.ParseError, ValueError, TypeError, KeyError) as exc:
            return source_id, {"status": "error", "checkedAt": checked,
                               "lastSuccessAt": old.get("lastSuccessAt", ""),
                               "items": old.get("items", []),
                               "error": clean(str(exc), 120)}, f"ERR  {source_id}: {exc}"

    with ThreadPoolExecutor(max_workers=4) as pool:
        for source_id, result, message in pool.map(refresh, pending):
            results[source_id] = result
            print(message, file=sys.stderr)
    results = {source["id"]: results[source["id"]] for source in registry["sources"]}
    payload = {"generatedAt": previous_payload.get("generatedAt", "") if offline else checked, "results": results}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(results)} sources)")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="preserve current snapshot, initialize missing entries")
    parser.add_argument("--only", nargs="*", help="refresh selected source IDs")
    args = parser.parse_args()
    update(args.offline, set(args.only) if args.only else None)
