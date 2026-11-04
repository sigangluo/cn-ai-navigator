# AI Source Desk maintenance

This repository is an index of original AI information sources. The source registry is `data/sources.json`; the site renders every entry in it. Keep that invariant.

## Product rules

- Show all registered source channels by default, even when a channel is quiet or its fetch fails.
- Link to the publisher's official index and original item. Store only title, date, publisher categories, link, the publisher-provided teaser or paper abstract, and relevant public source-specific facts (such as stars or votes) when available. Rankings may store current order and a public description. Never generate a teaser or copy full articles.
- Preserve a publisher's native sections and item labels. The site's broad groups only organize the directory.
- In the upper-right corner of article rows, display only the publication date. Show useful publisher categories and source-specific public metrics below the title when available; omit generic categories and missing metrics.
- Group sources by the content readers seek, then label each source `zh` or `non_zh` based on the linked page's primary reading language. Language is a second-level display grouping; distribution channel (including WeChat) is not a standalone content group. Place rankings with the projects or resources they rank.
- Rankings are current snapshots. Never call a trending repository “published today.”
- Distinguish `directory`, `pending`, `ok`, and `error`. On errors, retain the last successful items and never claim they were freshly checked.
- A missing feed is not a reason to drop a valuable source. Keep it as a link until a stable public extraction path is verified.
- Do not add arbitrary WeChat scraping or rely on unofficial account enumeration. Link to the publisher's public website or an official index that leads to WeChat originals.

## Changing sources

Verify each URL and its page title on the official site. Confirm whether it exposes dates, categories, and stable article links before changing `mode` from `link` to a fetchable mode. Add a small parser test for any new HTML/API format. Never put API keys in the registry or snapshot.

## Checks

```bash
python3 -m unittest discover -s tests
python3 scripts/update.py --offline
python3 scripts/build.py
node --check site/assets/app.js
```

`python3 scripts/update.py` needs network access and may encounter publisher blocking. A failed fetch should change only that source's status. GitHub Actions refreshes twice daily. The optional weekly Codex workflow proposes fixes through a PR when `OPENAI_API_KEY` and `ENABLE_AI_MAINTENANCE=true` are configured.
