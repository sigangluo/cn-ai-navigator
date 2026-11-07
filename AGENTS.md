# cn-ai-navigator 维护指南

本仓库是 AI 原始信息来源的导航目录，由 AI 维护、人工审核变更。`data/sources.json` 是来源登记表，网站必须展示其中每一个入口。

这是**唯一**的维护文档。`README.md` 只写给访问者（目的与查看方式），不写维护内容。每条规则只在这里出现一次。

## 1. 文件地图

| 路径 | 性质 | 能否手改 |
| --- | --- | --- |
| `data/sources.json` | 来源登记表 | 可以，修改来源就改这里 |
| `data/schema.json` | `mode`、`cadence` 以及登记表字段的声明 | 新增 mode、cadence 或字段时改 |
| `scripts/update.py` | 抓取与解析；`FETCHERS` 注册表 | 可以 |
| `scripts/build.py` | 校验登记表，生成站点副本，写入 `?v=` | 可以 |
| `scripts/health.py` | 健康报告，不联网 | 可以 |
| `tests/` | 单元测试 | 可以 |
| `site/index.html`、`site/assets/*` | 前端；`index.html` 里的 `?v=` 由 `build.py` 写入 | 可以，但不要手改 `?v=` |
| `site/data/sources.json` | **生成物**：登记表副本，多一个 `ranking` 字段 | 不可以，运行 `build.py` |
| `site/data/latest.json` | **生成物**：条目快照，由定时任务刷新 | 不可以，运行 `update.py` |
| `.github/workflows/` | `pages.yml` 刷新并部署；`ci.yml` 检查 PR | 可以 |

## 2. 产品规则

- 默认展示全部已登记来源，即使某个来源近期没有更新或抓取失败。
- 来源入口指向发布者官网目录或原视频频道；条目指向原文。只保存标题、日期、发布者分类、链接、发布者提供的导语、视频简介或论文摘要，以及可核实的公开数据，例如星标、投票和播放量。榜单可以保存当前顺序和公开简介。不得生成导语，不得复制文章或视频全文，不得编造标题、日期、订阅源地址或排名。
- 保留发布者自己的栏目和条目标签（`sections`、条目 `categories`）。本站的内容分类只用于组织目录。
- 文章行右上角只显示发布日期。标题下方展示有用的发布者分类和来源专属公开数据；通用分类和缺失的数据不展示。日期不可靠时（例如批量导入时间）宁可留空。
- 榜单是当前快照，不得称为“今天发布”。仓库创建或更新时间与文章发布日期要区分。
- 不随意抓取公众号，也不依赖非官方的账号枚举。优先链接发布者官网，或能通向公众号原文的官方目录。
- 不得把接口密钥写进登记表、快照或代码。

## 3. 分类规则

### 内容分类 `group`

先按“读者想找什么”分组，再按链接页面的主要阅读语言分 `zh` / `non_zh`。发布渠道（公众号、YouTube）不单独成为内容分类，视频除外：视频是内容形态。按下面的顺序判断，第一个命中的就是答案：

1. 榜单归入**所排名的资源**：论文榜 → `papers`，模型、数据集、仓库榜 → `open`。
2. 视频频道 → `video`；论文库、预印本 → `papers`；代码仓库、模型、数据集、应用 → `open`。
3. 短帖与社区讨论（Bluesky、Reddit、X、Hacker News）→ `social`。
4. 以个人署名为主体的博客 → `blogs`。
5. 机构自己发布的研究、模型和公司动态 → `labs`，并**必须**填 `kind`。
6. 面向开发者的文档、更新日志、技术博客、框架与基础设施动态 → `engineering`。
7. 其余由媒体撰写的报道与解读 → `news`。

边界案例（照此处理，不要重新争论）：

- OpenAI 有三处：新闻与研究在 `labs`；开发者博客、Cookbook、发布说明、API 变更日志在 `engineering`（读者在找开发实践）。
- 机构官网收录的**第三方媒体报道**属于 `news`，不属于 `labs`（智源“媒体报道”）。智源自己的新闻留在 `labs`。
- 同一机构的“官网”与“公众号”若内容相同，只登记一个入口，另一个放进 `relatedLinks`（美团、InfoQ 的做法）。
- Artificial Analysis 的文章在 `news`，榜单在 `open`：按内容形态分，不按机构分。

### 其他分类字段

- `kind`（仅 `labs`，在 `sources.json` 的 `kinds` 里声明）：`model-lab` 模型公司、`big-tech` 科技大厂研究院、`institute` 研究院与高校、`evaluator` 独立评测与研究。
- `parent`：`org` 是发布者名称；同一母公司下有多个 `org` 时（Google DeepMind、Google Research、Google）用 `parent` 写母公司名，机构筛选按 `parent || org` 聚合。
- `language`：按**链接页面的主要阅读语言**，不按机构国籍。标题和简介是英文就是 `non_zh`，即使机构在中国（Kimi 官网文章为英文）。`health.py` 会检查标注与内容是否一致。
- `cadence`：声明**预期的发布间隔**，阈值在 `schema.json`。按近半年的实际节奏选，不按期望选；基本停更用 `dormant`。`health.py` 报告陈旧时，先判断是来源真的停更（放宽 `cadence`）还是抓取出错（修解析器），不要为了消除告警而随意改 `cadence`。

## 4. 数据结构

### `data/sources.json`

顶层有 `groups`、`kinds`、`sources`，每个来源一行。字段必须在 `data/schema.json` 声明过（`commonFields`，或该 mode 的 `requires` / `options`），拼错会被 `build.py` 拒绝。

| 字段 | 含义 |
| --- | --- |
| `id` | 唯一标识，也是快照的键。改名等于删除再新增 |
| `name`、`org`、`parent` | 页面显示名；发布者名称；母公司名（可选） |
| `group`、`kind`、`language`、`cadence` | 见第 3 节 |
| `url`、`note` | 官网入口（HTTPS）；页面上的一句话说明 |
| `mode` | 采集方式，在 `schema.json` 的 `modes` 里声明，其 `requires` 是必填字段，`ranking` 表示是否榜单 |
| `sections`、`relatedLinks` | 发布者官网自己的栏目名（仅展示）；其他入口，如公众号 `[{"label":"…","url":"https://…"}]` |
| `directoryReason`、`verifiedAt` | `mode: link` 必填：为什么没有自动列表；核查月份 `YYYY-MM`。其他 mode 不得出现 |

`feed` 的可选项：`feedCategories`（只保留这些分类的条目）、`allowEmpty`（空订阅源表示暂无新内容，沿用旧条目，arXiv 周末如此）、`lenientXml`（发布者会在 CDATA 里截断 UTF-8，如新智元）、`noDescription`（订阅源里的描述只是引文，如 ACL）、`categoryPage`（从官网页面补全分类，如 DeepMind）、`aiOnly`（综合科技媒体只保留标题含 AI 相关词的文章，词表是 `update.py` 的 `AI_TITLE_TERMS`，英文词整词匹配）。

### `site/data/latest.json`

`{"generatedAt": …, "results": {来源 id: 结果}}`。结果字段：`status`（`ok`、`error`、`directory` 只有入口、`pending` 尚未首次获取）、`checkedAt`、`lastSuccessAt`、`failures` 与 `error`（连续失败次数与最近的错误，成功一次即清除）、`items`，以及内部缓存 `httpValidators`、`feedParserVersion`、`userId`。

条目字段：`title`、`url`、`description`、`categories[]`（发布者的分类）、`publishedAt`（UTC ISO 时间，榜单没有，日期不可靠时为空字符串）。可选：`authors[]`、`metrics{}`（`stars` `starsToday` `likes` `downloads` `upvotes` `comments` `reposts` `views` `intelligenceIndex`，只放可核实的公开数字）、`thumbnail`、`linkedOrganization`、`task`、`sdk`、`provider`、`modelId`、`createdAt`、`updatedAt`、`license`。新增字段时在这里补一项，并在前端 `app.js` 的指标表里加展示。

保存上限：文章、视频、论文每个来源 24 条，榜单 25 条；页面每次显示 5 条文章或 10 个排名，其余分页。

## 5. 日常任务

### 5.1 新增或修改来源

1. 在发布者官网核对链接和页面标题；确认官网提供日期、分类和稳定的文章直达链接，才可以把 `mode` 从 `link` 改为自动采集模式。
2. 按第 3 节选 `group`、`language`、`cadence`（`labs` 还要选 `kind`）。
3. `mode: link` 时写 `directoryReason` 和 `verifiedAt`。
4. 新增网页或接口解析方式时，在 `tests/` 里加小范围解析测试，用手写的最小样本，不要提交真实页面。
5. 运行第 7 节的命令。

### 5.2 排查来源

先运行 `python3 scripts/health.py`（不联网，读登记表和快照），再按报告处理。单次失败不用改任何东西；连续失败才说明来源变了。抓取失败时保留上次成功的条目，不得声称它们刚刚同步过；不要因为站点阻止采集就删除来源，改成 `mode: link` 并写明原因。单个来源的异常不得影响其他来源。

| 报告 | 先判断 | 处理 |
| --- | --- | --- |
| ERROR 连续失败 | 用 `python3 scripts/update.py --only <id>` 重试；看错误是网络、403/412，还是解析失败 | 网络类：不改代码，下次定时刷新会恢复。被拒绝（403/412）：改 `mode: link`，写 `directoryReason`、`verifiedAt`。解析失败：打开官网对照，改解析器并补测试 |
| ERROR 尚未首次同步 | 新增来源后未刷新 | `update.py --only <id>` |
| WARN 条目已陈旧 | 官网是否真的没有新文章 | 确实停更：放宽 `cadence`（`quarterly` 或 `dormant`）。官网有新文章而快照没有：解析器坏了，修复并补测试 |
| WARN 语言与内容不符 | 打开几篇文章看实际语言 | 改 `language`，并确认它在页面上的位置 |
| WARN 条目没有日期 | 官网是否提供日期 | 能提供就修解析器；不能就接受，不要编造日期 |
| INFO 出现了未登记的官网分类 | 对照官网栏目 | 确认是发布者自己的分类后补进 `sections`；`sections` 必须包含 `feedCategories` 的全部分类（`build.py` 强制）。`综合`、`news` 这类过于笼统的标签列在 `schema.json` 的 `genericCategories`，前端不展示，健康检查也忽略 |
| INFO 入口长期未核查 | 官网现在是否有可靠的列表或订阅源 | 有就改成自动采集 mode 并补测试；没有就更新 `verifiedAt` |

### 5.3 新增一种 `mode`

1. 在 `data/schema.json` 的 `modes` 里声明 `requires`、`options`、`ranking`（前端据此显示排名而不是日期）。
2. 在 `scripts/update.py` 写 `parse_xxx(raw, …)`，只返回标准条目字段。
3. 用 `@fetcher("mode_name")` 注册 `fetch_xxx(run)`，返回条目列表。`run.source` 是登记表条目，`run.old` 是上次的结果，`run.known("description")` 取上次保存的 URL 到值的映射，`run.extra` 放需要随结果持久化的字段。
4. 为解析器加测试。`test_every_declared_mode_has_a_fetcher` 保证声明与注册一致。
5. 有来源专属的公开数据时，在 `site/assets/app.js` 的指标表里补一行。
6. 在 `sources.json` 里至少登记一个使用它的来源，再运行第 7 节的命令。

### 5.4 修改前端

`site/` 是纯 HTML、CSS、JavaScript，没有构建步骤，不引入依赖。渲染一律用 `textContent`，不用 `innerHTML`；链接经过 `safeHref`。样式约定写在 `style.css` 开头的注释里，要点：颜色、描边、圆角、焦点环只用 `:root` 里的变量，组件里不写十六进制颜色；所有标签（机构类型、状态、官网分类、作者、数据指标）都是同一个 `.tag` 组件，默认中性灰底，只有 `.tag-outline`（机构类型）、`.tag-status`（状态）、`.tag-button`（可点击）三种变体，不要新建标签样式，也不要给数据指标单独着色或加粗；条目最多显示 3 个发布者分类，多余的不折叠、不显示；选中态由 `aria-pressed` / `aria-current` 驱动，不设 `selected`、`active` 类；文字不小于 11px，文字色在对应底色上保证 4.5:1 对比度（新增颜色先算对比度）。改完后运行 `build.py`（它会更新 `?v=`），并在浏览器里实际打开页面检查，不要只靠语法检查。

## 6. 失败处理机制

- 一次刷新失败：已正常的来源保持 `ok`，保留旧条目，记录 `failures: 1`。偶发网络错误不会触发页面上的“同步异常”。
- 连续 `ERROR_AFTER_FAILURES`（2）次失败：`status` 变为 `error`，页面显示“同步异常”，旧条目仍保留。刷新成功即清除 `failures` 与 `error`。
- 网络层：单次响应整体期限 40 秒；网络错误与 408/5xx 重试一次（YouTube 订阅源额外对 404 重试一次）；同一主机最多 3 个并发请求；12 个来源并行。
- 文章页补抓的导语按 URL 复用上次的结果，只对新文章发请求。`--only` 部分刷新不改变快照时间。
- 部署闸门：定时刷新后运行 `health.py --max-error-ratio 0.34`，超过三分之一的可抓取来源处于 `error` 时，这次刷新不发布，已发布的页面保持不变。

## 7. 完成标准

每次改动在提交前都要通过：

```bash
python3 -m unittest discover -s tests
python3 scripts/update.py --offline      # 登记表变化后，让快照与登记表一致
python3 scripts/build.py                 # 校验登记表，生成 site/data/sources.json 与 ?v=
node --check site/assets/app.js
node --check site/assets/page-agent.js
git diff --check
python3 scripts/health.py                # 不得引入新的 ERROR；WARN 需要在说明里解释
```

`python3 scripts/update.py`（不带参数）联网刷新全部来源，约 1 到 2 分钟，可能遇到发布者的访问限制，只在需要刷新数据时运行；只刷新一个来源用 `--only <id>`。需要 Python 3.9+ 和 Node.js（仅语法检查），无需安装依赖。本地查看页面：`python3 -m http.server 8000 -d site`。数据刷新提交由工作流产生，不要手工提交快照的刷新。

## 8. 部署、定时与凭证

- 在仓库 **Settings → Pages → Build and deployment** 选择 **GitHub Actions**。
- `pages.yml` 在推送 `main` 后部署，也在每天 09:17、21:17（新加坡时间）刷新并部署。顺序：测试 → 刷新 → 健康闸门 → 构建 → 上传 → 清除社媒帖子 → 提交快照 → 部署。
- `ci.yml` 在每个 PR 上运行测试、构建、语法检查，并要求生成物已提交。
- 工作流把社媒条目包含在部署产物中，随后从提交回仓库的快照里清除 Reddit 和 X 帖子，避免长期留在 Git 历史。这无法保证 Reddit 已删除的内容立即从已发布页面移除；启用 Reddit 前应评估其当前 Data API 条款和删除要求。
- 公开仓库的计划任务可能因长期无活动被 GitHub 停用，见 [GitHub 文档](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。快照每次刷新都会改写，Git 历史会随之增长（目前约 5 MB）。
- **Reddit**：使用应用级 OAuth。获得 [Data API 许可](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)后，在 Actions Secrets 设置 `REDDIT_CLIENT_ID`、`REDDIT_CLIENT_SECRET`、`REDDIT_USER_AGENT`（格式如 `script:your-app-id:v1.0 (by /u/yourname)`）。未配置时三个社区只显示入口。
- **X**：使用 App Bearer Token。Secrets 设置 `X_BEARER_TOKEN`，Variables 设置 `X_API_ENABLED=true` 才会读取。X API 按使用量计费，启用前查看[官方价格](https://developer.x.com/)和开发者控制台的余额。未启用时两个账号只显示入口。
- 不要把任何凭证写入仓库或快照。

## 9. 已知限制

- `link` 来源为什么没有自动列表，见 `sources.json` 里各自的 `directoryReason`，不要在别处重复。
- **Page Agent**：前端从 CDN 加载固定版本 `page-agent@1.12.4` 并校验 SRI。该包的 IIFE 构建只有 `page-agent.demo.js` 一个文件，我们传入自己的 `baseURL` 和模型，不使用其演示端点。升级时同时更新 `site/assets/page-agent.js` 里的地址与 `integrity`。
- **InfoQ 日期**：订阅源把北京时间标成 GMT，解析器已回退 8 小时，使日期与原文页一致。
- **智源日期**：官网接口里带 6 位小数秒的 `published_at`（如 `…07.536172Z`）是批量导入时间，解析器把这类条目的日期留空。
- **有意保留的重复条目**：`openai-news` 与 `openai-research` 共享订阅源；GitHub 的两个主题榜、Hugging Face 的两个论文榜有重叠。它们是不同入口的当前快照，不去重。
- **YouTube 官方 Atom** 偶有临时 404，已自动重试；仍失败时保留旧条目。
