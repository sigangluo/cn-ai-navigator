# AI Source Desk maintenance

This repository is an index of original AI information sources. The source registry is `data/sources.json`; the site renders every entry in it. Keep that invariant.

## Product rules

- Show all registered source channels by default, even when a channel is quiet or its fetch fails.
- Link to the publisher's official index and original item. Store only title, date, publisher categories, link, and for a ranking the current order and short public description.
- Preserve a publisher's native sections and item labels. The site's broad groups only organize the directory.
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
