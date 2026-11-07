#!/usr/bin/env python3
"""Deterministic health report for the source catalog; no network access.

Reads data/sources.json, data/schema.json and site/data/latest.json and reports:
  ERROR  a source is failing repeatedly, or never synced
  WARN   a source looks stale for its declared cadence, has a language label that
         contradicts its content, or lists items without dates
  INFO   one-off failures, link-only entries whose verification is old, publisher
         categories seen in items but missing from `sections`

Usage:
  python3 scripts/health.py                    # human-readable report
  python3 scripts/health.py --markdown         # same, as Markdown (used for the Actions job summary)
  python3 scripts/health.py --max-error-ratio 0.34
      # exit 1 when more than that share of fetchable sources is in error (deploy gate)
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "sources.json"
SCHEMA = ROOT / "data" / "schema.json"
SNAPSHOT = ROOT / "site" / "data" / "latest.json"

CJK = re.compile(r"[一-鿿]")
REVERIFY_AFTER_MONTHS = 6
ZH_MIN_SHARE = 0.6      # a `zh` source: this share of items should contain Chinese in title or teaser
NON_ZH_MAX_SHARE = 0.3  # a `non_zh` source: at most this share of titles may contain Chinese


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def months_between(month, now):
    year, mon = map(int, month.split("-"))
    return (now.year - year) * 12 + now.month - mon


def language_share(items):
    """Return (share with Chinese in title or teaser, share with Chinese in title)."""
    if not items:
        return None
    in_any = sum(bool(CJK.search(f"{i.get('title', '')} {i.get('description', '')}")) for i in items)
    in_title = sum(bool(CJK.search(i.get("title", ""))) for i in items)
    return in_any / len(items), in_title / len(items)


def check(registry, schema, snapshot):
    """Return (findings, stats). A finding is (level, source id, message)."""
    findings = []
    results = snapshot.get("results", {})
    generated = parse_time(snapshot.get("generatedAt")) or datetime.now(timezone.utc)
    fetchable = errors = 0
    for source in registry["sources"]:
        sid, mode = source["id"], source["mode"]
        result = results.get(sid, {"status": "pending", "items": []})
        status, items = result.get("status"), result.get("items", [])
        ranking = schema["modes"][mode]["ranking"]
        if mode == "link":
            age = months_between(source["verifiedAt"], generated)
            if age >= REVERIFY_AFTER_MONTHS:
                findings.append(("INFO", sid, f"原站入口已 {age} 个月未重新核查（{source['verifiedAt']}）：{source['directoryReason']}"))
            continue
        if status == "directory":
            continue  # social API without credentials
        fetchable += 1
        if status == "pending":
            findings.append(("ERROR", sid, "尚未完成首次同步"))
            errors += 1
            continue
        if status == "error":
            errors += 1
            findings.append(("ERROR", sid, f"连续失败 {result.get('failures', 1)} 次：{result.get('error', '')}；上次成功 {result.get('lastSuccessAt') or '从未'}"))
        elif result.get("failures"):
            findings.append(("INFO", sid, f"最近一次刷新失败（{result.get('error', '')}），沿用旧数据，若再次失败将标为异常"))
        if not items:
            continue
        shares = language_share(items)
        if source["language"] == "zh" and shares[0] < ZH_MIN_SHARE:
            findings.append(("WARN", sid, f"语言标为 zh，但只有 {shares[0]:.0%} 的条目含中文（标题或简介）"))
        if source["language"] == "non_zh" and shares[1] > NON_ZH_MAX_SHARE:
            findings.append(("WARN", sid, f"语言标为 non_zh，但 {shares[1]:.0%} 的标题含中文"))
        if source.get("sections"):
            known = {name.lower() for name in source["sections"]} | set(schema.get("genericCategories", []))
            seen = {c for i in items for c in i.get("categories", []) if c.lower() not in known}
            if seen:
                findings.append(("INFO", sid, f"快照里出现了 sections 没登记的官网分类：{sorted(seen)}；核对官网后补进 sections"))
        if ranking:
            continue
        dated = [parse_time(i["publishedAt"]) for i in items if i.get("publishedAt")]
        if len(dated) < len(items):
            findings.append(("WARN", sid, f"{len(items) - len(dated)}/{len(items)} 条没有发布日期"))
        limit = schema["cadence"][source["cadence"]]["maxAgeDays"]
        if dated and limit is not None:
            age = (generated - max(dated)).days
            if age > limit:
                findings.append(("WARN", sid, f"最新条目 {age} 天前，超过 cadence={source['cadence']} 的 {limit} 天：确认来源是否失效，或把 cadence 放宽"))
    return findings, {"fetchable": fetchable, "errors": errors, "generatedAt": snapshot.get("generatedAt", "")}


def render(findings, stats, markdown=False):
    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    lines = [("# 来源健康报告\n" if markdown else "来源健康报告"),
             f"快照时间 {stats['generatedAt']}；可抓取来源 {stats['fetchable']} 个，其中异常 {stats['errors']} 个。"]
    for level in sorted({f[0] for f in findings}, key=order.get):
        lines.append(f"\n## {level}" if markdown else f"\n[{level}]")
        for _, sid, message in (f for f in findings if f[0] == level):
            lines.append(f"- `{sid}`：{message}" if markdown else f"  {sid}: {message}")
    if not findings:
        lines.append("没有发现问题。")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--max-error-ratio", type=float, help="exit 1 when errors / fetchable sources exceeds this")
    args = parser.parse_args()
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    findings, stats = check(registry, schema, snapshot)
    print(render(findings, stats, args.markdown))
    if args.max_error_ratio is not None and stats["fetchable"]:
        ratio = stats["errors"] / stats["fetchable"]
        if ratio > args.max_error_ratio:
            print(f"\n异常比例 {ratio:.0%} 超过阈值 {args.max_error_ratio:.0%}，不应发布这次刷新。", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
