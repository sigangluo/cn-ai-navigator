/* AI 导航台前端。纯浏览器 JavaScript，无构建步骤。
 * 区块：1 状态与常量 · 2 通用函数 · 3 状态表 · 4 条目渲染 · 5 卡片渲染 · 6 筛选 · 7 URL 状态 · 8 页面渲染 · 9 事件与初始化
 * 渲染一律用 textContent；链接经过 safeHref。 */

/* ========== 1. 状态与常量 ========== */
const $ = (selector) => document.querySelector(selector);
const state = { catalog: null, snapshot: null, query: '', language: 'all', org: 'all', todayOnly: false, activeGroup: 'all', pages: new Map(), sectionFilters: new Map() };
const tz = 'Asia/Singapore';
const PAGE_SIZE = 5;
const RANKING_PAGE_SIZE = 10;
const FRESH_SNAPSHOT_MS = 36 * 60 * 60 * 1000; // 超过这个时间的成功快照视为旧快照
const SEARCH_DEBOUNCE_MS = 150;
const orgCounts = new Map();
const kindNames = new Map();

/* ========== 2. 通用函数 ========== */
const dateKey = (value) => {
  if (!value || Number.isNaN(Date.parse(value))) return '';
  return new Intl.DateTimeFormat('sv-SE', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value));
};
const today = dateKey(new Date().toISOString());
const displayDate = (value) => {
  const key = dateKey(value);
  return key ? (key === today ? '今天' : key) : '日期未提供';
};
const safeHref = (value) => {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : '#'; }
  catch { return '#'; }
};
const el = (tag, className, content) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
};
const link = (label, url, className = '') => {
  const node = el('a', className, label);
  node.href = safeHref(url);
  node.target = '_blank';
  node.rel = 'noopener noreferrer';
  return node;
};
const numberText = (value) => Number(value).toLocaleString('en-US');
const lower = (value) => String(value ?? '').toLocaleLowerCase();
const orgOf = (source) => source.parent || source.org; // 机构筛选按母公司聚合

const resultFor = (source) => state.snapshot?.results?.[source.id] || { status: source.mode === 'link' ? 'directory' : 'pending', items: [] };
const recentlySynced = (result) => Boolean(result.lastSuccessAt && Date.now() - Date.parse(result.lastSuccessAt) <= FRESH_SNAPSHOT_MS);
const updatedToday = (source) => !source.ranking && resultFor(source).status === 'ok' && resultFor(source).items.some((item) => dateKey(item.publishedAt) === today);

/* ========== 3. 状态表：卡片徽标与图例共用，改文案只改这里 ========== */
// tone 对应 CSS 的 .tag-status.is-<tone> 与 .dot.<tone>
const STATUS = {
  fresh: { label: '今天有更新', tone: 'fresh' },
  recent: { label: '此前有发布', tone: 'recent' },
  ranking: { label: '当前榜单', tone: 'recent' },
  rankingStale: { label: '旧榜单快照', tone: 'stale' },
  directory: { label: '原站入口', tone: 'manual' },
  pending: { label: '等待同步', tone: 'manual' },
  error: { label: '同步异常', tone: 'error' },
};

function statusKey(source, result) {
  if (result.status === 'error') return 'error';
  if (result.status === 'directory') return 'directory';
  if (result.status !== 'ok') return 'pending';
  if (source.ranking) return recentlySynced(result) ? 'ranking' : 'rankingStale';
  return updatedToday(source) ? 'fresh' : 'recent';
}

// 成功但快照已旧的来源，徽标沿用橙色提示（图例里的“旧榜单快照”同色）
const toneOf = (source, result) => {
  const key = statusKey(source, result);
  return result.status === 'ok' && !recentlySynced(result) ? 'stale' : STATUS[key].tone;
};

function renderLegend() {
  const legend = $('#legend'); legend.replaceChildren();
  Object.values(STATUS).forEach(({ label, tone }) => {
    const item = el('span'); item.append(el('i', `dot ${tone}`), label);
    legend.append(item);
  });
  legend.append(el('span', 'timezone', '日期按新加坡时间'));
}

/* ========== 4. 条目渲染 ========== */
const isGenericCategory = (category) => (state.catalog.genericCategories || []).includes(lower(category.trim()));

// 各 mode 展示哪些公开数据：[metrics 字段, 文案模板]，{n} 为千分位数字
const STARS = [['stars', '★ {n} Stars'], ['starsToday', '今日新增 +{n}']];
const LIKES_DOWNLOADS = [['likes', '赞 {n}'], ['downloads', '下载 {n}']];
const PAPER_VOTES = [['upvotes', '投票 {n}'], ['comments', '讨论 {n}']];
const POST_VOTES = [['upvotes', '投票 {n}'], ['comments', '评论 {n}']];
const SOCIAL = [['likes', '赞 {n}'], ['reposts', '转发 {n}'], ['comments', '回复 {n}']];
const METRIC_FACTS = {
  github_trending: STARS, github_topic: STARS,
  hf_trending: LIKES_DOWNLOADS, modelscope_models: LIKES_DOWNLOADS,
  papers: PAPER_VOTES, papers_trending: PAPER_VOTES,
  aa_leaderboard: [['intelligenceIndex', 'Intelligence Index {n}']],
  youtube: [['views', '播放 {n}']],
  hn_feed: POST_VOTES, reddit: POST_VOTES,
  bluesky: SOCIAL, x_api: SOCIAL,
};
// 条目上的专属文本字段：[字段, 前缀, 可选的格式化函数]
const HF_FIELDS = [['task', '任务 '], ['sdk', '框架 ']];
const EXTRA_FACTS = {
  modelscope_models: [['modelId', '模型 ID '], ['createdAt', '仓库创建 ', dateKey], ['updatedAt', '仓库更新 ', dateKey], ['license', '许可 '], ...HF_FIELDS],
  hf_trending: HF_FIELDS,
  aa_leaderboard: [['provider', '机构 ']],
};

// 来源名形如 “arXiv · cs.AI” 时，条目里的主分类 cs.AI 是冗余的
const primaryCategory = (source) => source.name.match(/^arXiv · (\S+)$/)?.[1] || null;

const MAX_ITEM_CATEGORIES = 3;

function itemFacts(source, item) {
  const facts = [];
  const add = (label) => { const fact = el('span', 'tag', label); facts.push(fact); return fact; };
  const metrics = item.metrics || {};
  const authors = (item.authors || []).filter((name) => name && lower(name) !== lower(source.org));
  if (authors.length) {
    const more = authors.length > 3 ? ` 等 ${authors.length} 人` : '';
    add(`作者 ${authors.slice(0, 3).join('、')}${more}`).title = authors.join('、');
  }
  if (source.group === 'papers' && item.linkedOrganization) add(`HF 关联组织 ${item.linkedOrganization}`);
  (METRIC_FACTS[source.mode] || []).forEach(([key, template]) => {
    if (Number.isFinite(metrics[key])) add(template.replace('{n}', numberText(metrics[key])));
  });
  (EXTRA_FACTS[source.mode] || []).forEach(([key, prefix, format = (value) => value]) => {
    if (item[key]) add(prefix + format(item[key]));
  });
  const primary = primaryCategory(source);
  const categories = (item.categories || []).filter((category) => category && !isGenericCategory(category) && category !== primary);
  categories.slice(0, MAX_ITEM_CATEGORIES).forEach((category) => add(category)); // 其余标签留在原文页，不折叠
  if (!facts.length) return null;
  const line = el('div', 'item-facts'); line.append(...facts);
  return line;
}

function itemRow(source, item, index, ranking) {
  const fresh = !ranking && dateKey(item.publishedAt) === today;
  const row = el('li', `item-row ${source.mode === 'youtube' ? 'video-row' : ''} ${fresh ? 'published-today' : 'published-earlier'}`);
  if (ranking) row.append(el('span', 'rank-number', String(index + 1).padStart(2, '0')));
  if (source.mode === 'youtube' && item.thumbnail) {
    const preview = link('', item.url, 'video-thumb');
    const image = el('img'); image.src = safeHref(item.thumbnail); image.alt = ''; image.loading = 'lazy'; image.decoding = 'async';
    preview.append(image); row.append(preview);
  }
  const body = el('div', 'item-body');
  const heading = el('div', 'item-heading');
  heading.append(link(item.title, item.url, 'item-link'));
  if (!ranking) {
    const published = dateKey(item.publishedAt);
    const date = el('time', 'item-detail', published || '日期未提供');
    if (published) date.dateTime = published;
    heading.append(date);
  }
  body.append(heading);
  const facts = itemFacts(source, item);
  if (facts) body.append(facts);
  if (item.description && item.description.trim() !== item.title.trim()) body.append(el('p', 'item-description', item.description));
  row.append(body);
  return row;
}

/* ========== 5. 卡片渲染 ========== */
function sectionTags(source, result) {
  const sections = el('details', 'source-sections');
  sections.append(el('summary', '', '官网栏目'));
  const tags = el('div', 'native-tags');
  const selected = state.sectionFilters.get(source.id);
  if (selected) sections.open = true;
  source.sections.forEach((name) => {
    const count = (result.items || []).filter((item) => (item.categories || []).includes(name)).length;
    if (!count) { tags.append(el('span', 'tag', name)); return; }
    const button = el('button', 'tag tag-button', `${name} · ${count}`);
    button.type = 'button'; button.setAttribute('aria-pressed', String(selected === name));
    button.title = `筛选此来源近期条目中的 ${name} 分类`;
    button.addEventListener('click', () => {
      if (selected === name) state.sectionFilters.delete(source.id);
      else state.sectionFilters.set(source.id, name);
      state.pages.set(source.id, 0);
      render();
    });
    tags.append(button);
  });
  sections.append(tags);
  return sections;
}

function itemsFor(source, result, ranking) {
  const matchesItemOnly = state.query && !sourceMatchesQuery(source);
  const queryItems = matchesItemOnly ? result.items.filter((item) => lower(item.title).includes(state.query)) : result.items;
  const selectedSection = state.sectionFilters.get(source.id);
  const matching = selectedSection ? queryItems.filter((item) => (item.categories || []).includes(selectedSection)) : queryItems;
  return ranking ? matching : [...matching].sort((a, b) => (Date.parse(b.publishedAt) || 0) - (Date.parse(a.publishedAt) || 0));
}

function pagerFor(source, page, totalPages, count, goTo) {
  const pager = el('nav', 'pager');
  pager.setAttribute('aria-label', `${source.name} 条目分页`);
  pager.append(el('span', 'page-count', `${count} 条 · 第 ${page + 1} / ${totalPages} 页`));
  const buttons = el('div', 'page-buttons');
  const addButton = (label, target, disabled, current = false) => {
    const button = el('button', 'page-button', label);
    button.type = 'button'; button.disabled = disabled;
    if (current) button.setAttribute('aria-current', 'page');
    button.addEventListener('click', () => goTo(target));
    buttons.append(button);
  };
  addButton('上一页', page - 1, page === 0);
  for (let i = 0; i < totalPages; i++) addButton(String(i + 1), i, false, i === page);
  addButton('下一页', page + 1, page === totalPages - 1);
  pager.append(buttons);
  return pager;
}

function sourceContent(source, result, ranking) {
  const content = el('div', 'source-content');
  if (!result.items?.length) {
    content.append(el('p', 'no-items', '尚未取得条目，可直接打开原站。'));
    if (result.status === 'error') content.append(el('p', 'error-note', '最近一次同步失败'));
    return content;
  }
  const items = itemsFor(source, result, ranking);
  const selectedSection = state.sectionFilters.get(source.id);
  const pageSize = ranking ? RANKING_PAGE_SIZE : PAGE_SIZE;
  const totalPages = Math.ceil(items.length / pageSize);
  const renderPage = () => {
    const page = Math.max(0, Math.min(state.pages.get(source.id) || 0, totalPages - 1));
    content.replaceChildren();
    if (selectedSection) content.append(el('p', 'section-filter-note', `官网分类：${selectedSection} · 当前快照 ${items.length} 条`));
    const list = el(ranking ? 'ol' : 'ul', `item-list ${ranking ? 'ranking-list' : ''}`);
    items.slice(page * pageSize, (page + 1) * pageSize).forEach((item, offset) => list.append(itemRow(source, item, page * pageSize + offset, ranking)));
    content.append(list);
    if (!items.length) content.append(el('p', 'no-items', '当前筛选下没有匹配条目。'));
    if (totalPages > 1) {
      content.append(pagerFor(source, page, totalPages, items.length, (target) => {
        state.pages.set(source.id, target);
        renderPage();
        content.querySelector('.page-button[aria-current]')?.focus();
      }));
    }
    if (result.status === 'error') content.append(el('p', 'error-note', `最近一次同步失败${result.lastSuccessAt ? `，上次成功 ${displayDate(result.lastSuccessAt)}` : ''}`));
  };
  renderPage();
  return content;
}

function sourceCard(source) {
  const result = resultFor(source);
  const key = statusKey(source, result);
  const card = el('article', `source-card ${key === 'fresh' ? 'is-fresh' : ''} ${result.status === 'directory' ? 'is-directory' : ''}`);
  card.id = `source-${source.id}`;
  const identity = el('div', 'source-identity');
  const top = el('div', 'card-top');
  if (source.org !== source.name) top.append(el('span', 'org', source.org));
  if (kindNames.has(source.kind)) top.append(el('span', 'tag tag-outline', kindNames.get(source.kind)));
  const tone = toneOf(source, result);
  const status = el('span', `tag tag-status is-${tone}`); status.append(el('i', `dot ${tone}`), STATUS[key].label);
  top.append(status);
  identity.append(top);
  const title = el('h4', 'source-name'); title.append(link(source.name, source.url)); identity.append(title);
  const notes = el('div', 'source-notes');
  notes.append(el('p', 'source-note', source.note));
  if (result.status === 'directory' && source.directoryReason) {
    notes.append(el('p', 'directory-reason', `暂无自动列表：${source.directoryReason}${source.verifiedAt ? `（${source.verifiedAt} 核查）` : ''}`));
  }
  identity.append(notes);
  const links = el('div', 'source-links');
  links.append(link(source.group === 'video' ? '打开频道 ↗' : '打开原站 ↗', source.url, 'official-link'));
  if (source.feed) links.append(link(source.feed.includes('/atom/') ? 'Atom ↗' : 'RSS ↗', source.feed, 'feed-link'));
  (source.relatedLinks || []).forEach((related) => links.append(link(related.label, related.url, 'official-link')));
  identity.append(links);
  if (source.sections?.length) identity.append(sectionTags(source, result));
  card.append(identity);
  if (result.status !== 'directory') card.append(sourceContent(source, result, source.ranking));
  return card;
}

/* ========== 6. 筛选 ========== */
function sourceMatchesQuery(source) {
  if (state.query === '中文') return source.language === 'zh';
  if (state.query === '非中文') return source.language === 'non_zh';
  return lower([source.name, source.org, source.parent, kindNames.get(source.kind), source.note, ...(source.sections || [])].filter(Boolean).join(' ')).includes(state.query);
}

function matches(source) {
  if (state.activeGroup !== 'all' && source.group !== state.activeGroup) return false;
  if (state.language !== 'all' && source.language !== state.language) return false;
  if (state.org !== 'all' && orgOf(source) !== state.org) return false;
  if (state.todayOnly && !updatedToday(source)) return false;
  if (!state.query) return true;
  return sourceMatchesQuery(source) || resultFor(source).items?.some((item) => lower(item.title).includes(state.query));
}

const filtersActive = () => state.activeGroup !== 'all' || state.language !== 'all' || state.org !== 'all' || state.todayOnly || Boolean(state.query) || state.sectionFilters.size > 0;

function closeOrgPanel(returnFocus = false) {
  $('#orgPanel').hidden = true;
  $('#orgFilter').setAttribute('aria-expanded', 'false');
  if (returnFocus) $('#orgFilter').focus();
}

function closeLanguagePanel(returnFocus = false) {
  $('#languagePanel').hidden = true;
  $('#languageFilter').setAttribute('aria-expanded', 'false');
  if (returnFocus) $('#languageFilter').focus();
}

function renderOrgOptions() {
  const query = lower($('#orgSearch').value.trim());
  const options = $('#orgOptions'); options.replaceChildren();
  const names = ['all', ...[...orgCounts.keys()].sort(new Intl.Collator('zh-CN').compare)];
  names.filter((org) => org === 'all' ? !query || '全部机构'.includes(query) : lower(org).includes(query)).forEach((org) => {
    const choice = el('button', 'org-option');
    choice.type = 'button'; choice.setAttribute('aria-pressed', String(state.org === org));
    choice.append(el('span', '', org === 'all' ? '全部机构' : org), el('span', 'org-option-count', String(org === 'all' ? state.catalog.sources.length : orgCounts.get(org))));
    choice.addEventListener('click', () => { state.org = org; render(); closeOrgPanel(true); });
    options.append(choice);
  });
  if (!options.children.length) options.append(el('p', 'org-no-match', '没有匹配的机构'));
}

function renderNav() {
  const nav = $('#groupNav'); nav.replaceChildren();
  [{ id: 'all', name: '全部入口' }, ...state.catalog.groups].forEach((group, index) => {
    const button = el('button', 'nav-item');
    button.type = 'button'; button.setAttribute('aria-pressed', String(state.activeGroup === group.id));
    button.append(el('span', 'nav-index', String(index).padStart(2, '0')), el('span', 'nav-name', group.name));
    button.append(el('span', 'nav-count', String(group.id === 'all' ? state.catalog.sources.length : state.catalog.sources.filter((source) => source.group === group.id).length)));
    button.addEventListener('click', () => {
      state.activeGroup = group.id;
      render();
      $('#sections').scrollIntoView({ block: 'start' });
    });
    nav.append(button);
  });
}

/* ========== 7. URL 状态：?g=分组&lang=语言&org=机构&q=关键词&today=1 ========== */
function writeUrlState() {
  const params = new URLSearchParams();
  if (state.activeGroup !== 'all') params.set('g', state.activeGroup);
  if (state.language !== 'all') params.set('lang', state.language);
  if (state.org !== 'all') params.set('org', state.org);
  const query = $('#search').value.trim();
  if (query) params.set('q', query);
  if (state.todayOnly) params.set('today', '1');
  const search = params.toString();
  try { history.replaceState(null, '', `${location.pathname}${search ? `?${search}` : ''}${location.hash}`); }
  catch { /* file:// 等环境不允许改地址，忽略 */ }
}

function readUrlState() {
  const params = new URLSearchParams(location.search);
  const group = params.get('g');
  if (group && state.catalog.groups.some((item) => item.id === group)) state.activeGroup = group;
  const language = params.get('lang');
  if (language === 'zh' || language === 'non_zh') state.language = language;
  const org = params.get('org');
  if (org && orgCounts.has(org)) state.org = org;
  const query = (params.get('q') || '').trim();
  $('#search').value = query; state.query = lower(query);
  state.todayOnly = params.get('today') === '1';
  $('#todayOnly').checked = state.todayOnly;
}

/* ========== 8. 页面渲染 ========== */
function renderGroups(visible) {
  const container = $('#sections'); container.replaceChildren();
  for (const group of state.catalog.groups) {
    const entries = visible.filter((source) => source.group === group.id);
    if (!entries.length) continue;
    const section = el('section', 'group-section'); section.id = `group-${group.id}`;
    const heading = el('div', 'group-heading');
    const label = el('div'); label.append(el('span', 'section-kicker', group.id.toUpperCase()), el('h2', '', group.name));
    heading.append(label, el('p', '', group.description), el('span', 'group-count', `${entries.length} 个入口`));
    section.append(heading);
    for (const language of [{ id: 'zh', name: '中文' }, { id: 'non_zh', name: '非中文' }]) {
      const languageEntries = entries.filter((source) => source.language === language.id);
      if (!languageEntries.length) continue;
      const subsection = el('div', 'language-section');
      const subheading = el('div', 'language-heading');
      subheading.append(el('h3', '', language.name), el('span', '', `${languageEntries.length} 个入口`));
      subsection.append(subheading);
      const list = el('div', 'source-list');
      languageEntries.forEach((source) => list.append(sourceCard(source)));
      subsection.append(list); section.append(subsection);
    }
    container.append(section);
  }
  if (!visible.length) container.append(el('div', 'empty', '没有匹配的入口。可清除筛选后浏览完整目录。'));
}

function renderNotices(sources) {
  const unhealthy = sources.filter((source) => source.mode !== 'link' && resultFor(source).status !== 'directory' && (resultFor(source).status !== 'ok' || !recentlySynced(resultFor(source))));
  const notice = $('#syncNotice'); notice.hidden = unhealthy.length === 0;
  if (unhealthy.length) notice.textContent = `${unhealthy.length} 个入口近期未成功同步；旧结果和官网链接仍可访问。`;
  const generatedAt = state.snapshot?.generatedAt;
  $('#syncTime').textContent = generatedAt ? `快照 ${new Intl.DateTimeFormat('zh-CN', { timeZone: tz, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(generatedAt))} · 新加坡时间` : '等候首次同步';
  // 快照不是今天生成的：“今天更新”只反映快照时刻，提示用户
  const staleDay = generatedAt && dateKey(generatedAt) !== today;
  const todayNotice = $('#todayNotice'); todayNotice.hidden = !staleDay;
  if (staleDay) todayNotice.textContent = `当前快照生成于 ${dateKey(generatedAt)}（新加坡时间），可能尚未包含今天的内容；“今天有更新”只统计快照里的条目。`;
}

function render() {
  if (!state.catalog) return;
  const languageNames = { all: '全部语言', zh: '中文', non_zh: '非中文' };
  $('#languageFilterLabel').textContent = languageNames[state.language];
  document.querySelectorAll('#languagePanel [data-language]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.language === state.language)));
  $('#orgFilterLabel').textContent = state.org === 'all' ? '全部机构' : state.org;
  renderOrgOptions();
  renderNav();
  const sources = state.catalog.sources;
  $('#sourceCount').textContent = sources.length;
  $('#todayCount').textContent = `（${sources.filter(updatedToday).length}）`;
  renderNotices(sources);
  const visible = sources.filter(matches);
  $('#resetFilters').hidden = !filtersActive();
  $('#visibleCount').textContent = `${visible.length} / ${sources.length}`;
  $('#resultStatus').textContent = `显示 ${visible.length} / ${sources.length} 个入口`;
  renderGroups(visible);
  writeUrlState();
}

/* ========== 9. 事件与初始化 ========== */
async function init() {
  try {
    const [catalogResponse, snapshotResponse] = await Promise.all([fetch('data/sources.json'), fetch('data/latest.json')]);
    if (!catalogResponse.ok) throw new Error('目录加载失败');
    state.catalog = await catalogResponse.json();
    state.snapshot = snapshotResponse.ok ? await snapshotResponse.json() : null;
    (state.catalog.kinds || []).forEach((kind) => kindNames.set(kind.id, kind.name));
    state.catalog.sources.forEach((source) => orgCounts.set(orgOf(source), (orgCounts.get(orgOf(source)) || 0) + 1));
    readUrlState();
    render();
  } catch (error) { $('#sections').replaceChildren(el('div', 'empty', `${error.message}。请通过本地 HTTP 服务打开页面。`)); }
}

let searchTimer;
$('#search').addEventListener('input', (event) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.query = lower(event.target.value.trim()); state.pages.clear(); render(); }, SEARCH_DEBOUNCE_MS);
});
$('#todayOnly').addEventListener('change', (event) => { state.todayOnly = event.target.checked; state.pages.clear(); render(); });
$('#languageFilter').addEventListener('click', () => {
  const opening = $('#languagePanel').hidden;
  closeOrgPanel();
  $('#languagePanel').hidden = !opening;
  $('#languageFilter').setAttribute('aria-expanded', String(opening));
  if (opening) $('#languagePanel [aria-pressed="true"]').focus();
});
$('#languagePanel').addEventListener('click', (event) => {
  const button = event.target.closest('[data-language]');
  if (button) { state.language = button.dataset.language; render(); closeLanguagePanel(true); }
});
$('#orgFilter').addEventListener('click', () => {
  const opening = $('#orgPanel').hidden;
  closeLanguagePanel();
  $('#orgPanel').hidden = !opening;
  $('#orgFilter').setAttribute('aria-expanded', String(opening));
  if (opening) { $('#orgSearch').value = ''; renderOrgOptions(); $('#orgSearch').focus(); }
});
$('#orgSearch').addEventListener('input', renderOrgOptions);
$('#orgSearch').addEventListener('keydown', (event) => {
  if (event.key === 'ArrowDown') { $('#orgOptions .org-option')?.focus(); event.preventDefault(); }
});
$('#orgOptions').addEventListener('keydown', (event) => {
  if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
  const choices = [...$('#orgOptions').querySelectorAll('.org-option')];
  const index = choices.indexOf(document.activeElement);
  choices[index + (event.key === 'ArrowDown' ? 1 : -1)]?.focus();
  event.preventDefault();
});
document.addEventListener('pointerdown', (event) => {
  if (!event.target.closest('.org-filter')) closeOrgPanel();
  if (!event.target.closest('.language-filter')) closeLanguagePanel();
});
$('.org-filter').addEventListener('keydown', (event) => { if (event.key === 'Escape') { closeOrgPanel(true); event.preventDefault(); } });
$('.org-filter').addEventListener('focusout', (event) => { if (!event.relatedTarget || !event.currentTarget.contains(event.relatedTarget)) closeOrgPanel(); });
$('.language-filter').addEventListener('keydown', (event) => {
  if (event.key === 'Escape') { closeLanguagePanel(true); event.preventDefault(); }
  if (!['ArrowDown', 'ArrowUp'].includes(event.key) || $('#languagePanel').hidden) return;
  const choices = [...$('#languagePanel').querySelectorAll('.language-option')];
  const index = choices.indexOf(document.activeElement);
  choices[(index + (event.key === 'ArrowDown' ? 1 : choices.length - 1)) % choices.length].focus();
  event.preventDefault();
});
$('.language-filter').addEventListener('focusout', (event) => { if (!event.relatedTarget || !event.currentTarget.contains(event.relatedTarget)) closeLanguagePanel(); });
$('#resetFilters').addEventListener('click', () => {
  clearTimeout(searchTimer);
  state.query = ''; state.language = 'all'; state.org = 'all'; state.todayOnly = false; state.activeGroup = 'all'; state.sectionFilters.clear(); state.pages.clear();
  $('#search').value = ''; $('#todayOnly').checked = false; closeOrgPanel(); closeLanguagePanel();
  render(); // render 会把地址栏一并清空
});
renderLegend();
init();
