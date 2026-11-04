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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "sources.json"
OUTPUT = ROOT / "site" / "data" / "latest.json"
USER_AGENT = "AI-Source-Desk/1.0 (+https://github.com/sigangluo/ai-source-desk; public-link-index)"
MAX_BYTES = 5_000_000
MAX_ITEMS = 12


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean(value, limit=240):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()[:limit]


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
        if child.tag.rsplit("}", 1)[-1].lower() in names and child.text:
            return child.text.strip()
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
        })
        if len(items) >= MAX_ITEMS:
            break
    if not items:
        raise ValueError("feed has no usable links")
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
            items.append({"title": title, "url": url,
                          "publishedAt": date_iso(item.get("publishedAt") or paper.get("publishedAt")),
                          "categories": []})
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
            items.append({"title": name, "url": url,
                          "description": clean(record.get("pipeline_tag") or record.get("sdk"), 80)})
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
        items.append({"title": title, "url": href,
                      "description": clean(description.group(1), 180) if description else ""})
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
                          "description": f"Intelligence Index {score} · {clean(cells[2], 45)}"})
            if len(items) == 10:
                break
        if len(items) >= 5 and scores == sorted(scores, reverse=True):
            return items
    raise ValueError("Artificial Analysis intelligence ranking table changed")


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
            else:
                raise ValueError("unknown mode")
            return source_id, {"status": "ok", "checkedAt": checked,
                               "lastSuccessAt": checked, "items": items}, f"OK   {source_id}: {len(items)}"
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError, TypeError, KeyError) as exc:
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
