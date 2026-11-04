#!/usr/bin/env python3
"""Validate the catalog and copy it into the static site."""

import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "sources.json"
TARGET = ROOT / "site" / "data" / "sources.json"
SNAPSHOT = ROOT / "site" / "data" / "latest.json"


def main():
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    groups = {group["id"] for group in data["groups"]}
    assert groups and len(groups) == len(data["groups"])
    ids = set()
    for source in data["sources"]:
        assert source["id"] not in ids, source["id"]
        ids.add(source["id"])
        assert source["group"] in groups, source["id"]
        assert source.get("language") in {"zh", "non_zh"}, source["id"]
        assert source["mode"] in {"link", "feed", "metr_feed", "infoq_ai_feed", "youtube", "publisher_page", "kimi_page", "cohere_page", "llamaindex_page", "deepseek_updates", "aa_articles", "openai_cookbook", "papers", "papers_trending", "hf_trending", "github_trending", "github_topic", "aa_leaderboard"}, source["id"]
        assert source["cadence"] in {"daily", "weekly", "monthly"}, source["id"]
        for field in ("url", "feed"):
            if field in source:
                parsed = urlparse(source[field])
                assert parsed.scheme == "https" and parsed.netloc, (source["id"], field)
        for related in source.get("relatedLinks", []):
            parsed = urlparse(related["url"])
            assert related.get("label") and parsed.scheme == "https" and parsed.netloc, source["id"]
        if source["mode"] in {"feed", "metr_feed", "infoq_ai_feed", "youtube"}:
            assert source.get("feed"), source["id"]
        if source["mode"] == "hf_trending":
            assert source.get("resource") in {"models", "datasets", "spaces"}, source["id"]
    if SNAPSHOT.exists():
        snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        assert set(snapshot["results"]) == ids, "snapshot and registry differ; run update.py --offline"
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Validated {len(ids)} sources across {len(groups)} groups")


if __name__ == "__main__":
    main()
