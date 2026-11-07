#!/usr/bin/env python3
"""Validate the source registry and generate the static site's copy of it.

Generated outputs (never edit by hand):
- site/data/sources.json: the registry plus a derived `ranking` flag per source.
- the ?v=<hash> cache-busting suffix of every local asset referenced by site/index.html.
"""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "sources.json"
SCHEMA = ROOT / "data" / "schema.json"
TARGET = ROOT / "site" / "data" / "sources.json"
SNAPSHOT = ROOT / "site" / "data" / "latest.json"
INDEX = ROOT / "site" / "index.html"
SITE = ROOT / "site"
LANGUAGES = {"zh", "non_zh"}
MONTH = re.compile(r"20\d\d-(0[1-9]|1[0-2])")


def https_url(value):
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def validate(data, schema):
    """Raise AssertionError naming the offending source when the registry breaks a rule."""
    groups = {group["id"] for group in data["groups"]}
    assert groups and len(groups) == len(data["groups"]), "groups must be unique"
    kinds = {kind["id"] for kind in data.get("kinds", [])}
    modes, cadence = schema["modes"], schema["cadence"]
    ids = set()
    for source in data["sources"]:
        sid = source["id"]
        assert sid not in ids, f"duplicate id {sid}"
        ids.add(sid)
        assert source["group"] in groups, (sid, "unknown group")
        assert source.get("language") in LANGUAGES, (sid, "language")
        assert source["mode"] in modes, (sid, "mode is not declared in data/schema.json")
        assert source["cadence"] in cadence, (sid, "cadence is not declared in data/schema.json")
        mode = modes[source["mode"]]
        unknown = set(source) - set(schema["commonFields"]) - set(mode["requires"]) - set(mode.get("options", []))
        assert not unknown, (sid, f"unknown field(s) {sorted(unknown)}; declare them in data/schema.json")
        for field in mode["requires"]:
            assert source.get(field), (sid, f"mode {source['mode']} requires `{field}`")
        for field in ("url", "feed"):
            if field in source:
                assert https_url(source[field]), (sid, field)
        for related in source.get("relatedLinks", []):
            assert related.get("label") and https_url(related["url"]), (sid, "relatedLinks")
        if source["group"] == "labs":
            assert source.get("kind") in kinds, (sid, "labs sources need a `kind` from data/sources.json kinds")
        elif "kind" in source:
            assert source["kind"] in kinds, (sid, "unknown kind")
        if source["mode"] == "link":
            assert source.get("directoryReason") and MONTH.fullmatch(source.get("verifiedAt", "")), \
                (sid, "link sources need directoryReason and verifiedAt (YYYY-MM)")
        else:
            assert "directoryReason" not in source, (sid, "directoryReason only applies to mode=link")
        if "feedCategories" in source:
            assert source["mode"] == "feed" and isinstance(source["feedCategories"], list), sid
            assert source["feedCategories"] and all(isinstance(label, str) and label for label in source["feedCategories"]), sid
            assert set(source["feedCategories"]) <= set(source.get("sections", [])), (sid, "sections must include every feedCategories label")
        if "allowEmpty" in source:
            assert source["mode"] == "feed" and isinstance(source["allowEmpty"], bool), (sid, "allowEmpty")
        if source["mode"] == "bluesky":
            assert source["url"] == f"https://bsky.app/profile/{source['actor']}", sid
            assert isinstance(source.get("aiOnly", False), bool), sid
        if source["mode"] == "reddit":
            assert source["url"] == f"https://www.reddit.com/r/{source['subreddit']}/", sid
        if source["mode"] == "x_api":
            assert source["url"] == f"https://x.com/{source['username']}", sid
        if source["mode"] == "hf_trending":
            assert source["resource"] in {"models", "datasets", "spaces"}, sid
        if source["mode"] == "baai_news":
            assert source["newsCategory"] in {"news", "media"}, sid
    return ids


def with_derived_fields(data, schema):
    sources = [{**source, "ranking": schema["modes"][source["mode"]]["ranking"]} for source in data["sources"]]
    return {**data, "genericCategories": schema["genericCategories"], "sources": sources}


def asset_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def stamp_asset_versions():
    """Rewrite ?v= on local assets so browsers refetch exactly when the file changes."""
    html = INDEX.read_text(encoding="utf-8")

    def replace(match):
        attr, name = match.group(1), match.group(2)
        path = SITE / name
        return f'{attr}="{name}?v={asset_hash(path)}"' if path.exists() else match.group(0)

    stamped = re.sub(r'\b(href|src)="(assets/[^"?]+)(?:\?v=[^"]*)?"', replace, html)
    if stamped != html:
        INDEX.write_text(stamped, encoding="utf-8")


def main():
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    ids = validate(data, schema)
    if SNAPSHOT.exists():
        snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        assert set(snapshot["results"]) == ids, "snapshot and registry differ; run update.py --offline"
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(with_derived_fields(data, schema), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    stamp_asset_versions()
    print(f"Validated {len(ids)} sources across {len(data['groups'])} groups")


if __name__ == "__main__":
    main()
