# 维护说明

本文件供部署和维护项目时查阅。面向读者的目的、使用方式和页面状态见 [README](../README.md)；AI 修改项目须遵循 [AGENTS.md](../AGENTS.md)。

## 数据结构与约束

- [`data/sources.json`](../data/sources.json) 是来源目录，构建时复制到 `site/data/sources.json`。所有登记的入口必须始终出现在页面上。
- `site/data/latest.json` 是条目快照。文章、视频和论文每个来源最多保存 24 条；榜单最多保存 25 条。页面每次显示 5 条文章或 10 个排名，其余可分页查看。
- 来源只保存公开的标题、原文链接、发布日期、发布者简介或摘要、发布者分类，以及可验证的公开数据。榜单代表当前排名；仓库创建或更新时间须与文章发布日期区分。
- 对暂时无法可靠提取的来源使用原站入口。抓取失败时保留上次成功的条目并标记异常。不要因为某个站点阻止采集就删除该来源。

## 本地运行与检查

需要 Python 3.9+，无需安装依赖。

```bash
python3 scripts/update.py --offline
python3 scripts/build.py
python3 -m http.server 8000 -d site
```

`--offline` 保留已有快照并初始化新入口。联网刷新全部可获取来源运行 `python3 scripts/update.py`；只刷新一个来源可运行 `python3 scripts/update.py --only openai-news`。每次修改来源或解析器后运行：

```bash
python3 -m unittest discover -s tests
python3 scripts/update.py --offline
python3 scripts/build.py
node --check site/assets/app.js
node --check site/assets/page-agent.js
git diff --check
```

## GitHub Pages 与定时刷新

在仓库的 **Settings → Pages → Build and deployment** 选择 **GitHub Actions**。[发布工作流](../.github/workflows/pages.yml)在推送 `main` 后部署，也在每天 09:17、21:17（新加坡时间）刷新来源并部署。计划任务由 GitHub 运行；刷新失败只影响对应来源。

工作流把社媒条目包含在部署产物中，随后从提交回仓库的快照中清除 Reddit 和 X 帖子，避免长期留在 Git 历史。这个流程无法保证 Reddit 已删除内容立即从已发布页面移除；启用 Reddit 前应评估其当前 Data API 条款和删除要求。公开仓库的计划任务也可能因长期无活动而停用，见 [GitHub 文档](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。

## 可选的社媒 API

Reddit 使用应用级 OAuth。获得 [Data API 许可](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)后，在仓库 Actions Secrets 中设置 `REDDIT_CLIENT_ID`、`REDDIT_CLIENT_SECRET`、`REDDIT_USER_AGENT`。User-Agent 应符合 Reddit 规定的格式，例如 `script:your-app-id:v1.0 (by /u/yourname)`。未配置时，三个社区只显示入口。

X 使用 App Bearer Token。在 Actions Secrets 中设置 `X_BEARER_TOKEN`，并在 Actions Variables 中设置 `X_API_ENABLED=true` 才会启用读取。X API 按使用量计费，启用前查看[官方价格](https://developer.x.com/)和开发者控制台的余额。未启用时，两个账号只显示入口。不要把任何凭证写入仓库或站点快照。

## AI 维护

[AI 维护工作流](../.github/workflows/ai-maintenance.yml)只有在配置 Actions Secret `OPENAI_API_KEY` 和 Variable `ENABLE_AI_MAINTENANCE=true` 后才运行。它每周检查来源健康状况，提出修复并创建待审核 PR，不自动合并；数据定时刷新不依赖这个功能。修改来源时先核对官网原文、日期和分类，新增解析方式须补相应测试。

## 当前保留为原站入口的原因

以下是 2026 年 11 月的核查记录，随官方站点变化应重新验证：Reddit 和 X 共 5 个入口待配置授权；机器之心 `/rss` 返回数据服务页面；OpenAI Release Notes 对脚本返回 403，API Changelog 缺已确认的逐条稳定链接；美团公众号与 PaperWeekly 缺可靠的近期列表；MindSpore 原文标注更新时间，无法直接用作发布日期；两个 Bilibili 频道请求返回 412；OpenReview 需要先限定会议或期刊范围。YouTube 官方 Atom 偶有临时错误，失败时保留旧条目。
