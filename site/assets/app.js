const $ = (selector) => document.querySelector(selector);
const state = { catalog: null, snapshot: null, query: '', todayOnly: false, activeGroup: 'all' };
const tz = 'Asia/Singapore';
const dateKey = (value) => {
  if (!value || Number.isNaN(Date.parse(value))) return '';
  return new Intl.DateTimeFormat('sv-SE', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value));
};
const today = dateKey(new Date().toISOString());
const shortDate = (value) => {
  const key = dateKey(value);
  return key ? (key === today ? '今天' : key.slice(5)) : '日期未提供';
};
const safeHref = (value) => {
  try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) ? u.href : '#'; }
  catch { return '#'; }
};
const el = (tag, className, content) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
};
const link = (text, href, className = '') => {
  const a = el('a', className, text); a.href = safeHref(href); a.target = '_blank'; a.rel = 'noopener noreferrer'; return a;
};
const resultFor = (source) => state.snapshot?.results?.[source.id] || { status: source.mode === 'link' ? 'directory' : 'pending', items: [] };
const isRanking = (source) => ['github_trending', 'hf_trending', 'papers_trending', 'aa_leaderboard'].includes(source.mode);
const recentlySynced = (result) => Boolean(result.lastSuccessAt && Date.now() - Date.parse(result.lastSuccessAt) <= 36 * 60 * 60 * 1000);
const updatedToday = (source) => !isRanking(source) && resultFor(source).status === 'ok' && resultFor(source).items.some((item) => dateKey(item.publishedAt) === today);
const cadenceLabel = { daily: '通常每天', weekly: '通常每周', monthly: '不定期' };

function itemRow(item, index, ranking = false) {
  const li = el('li', 'item-row');
  if (ranking) li.append(el('span', 'rank-number', String(index + 1).padStart(2, '0')));
  const body = el('div', 'item-body');
  const head = el('div', 'item-head');
  head.append(link(item.title, item.url, 'item-link'));
  if (!ranking) head.append(el('span', 'item-detail', [shortDate(item.publishedAt), ...(item.categories || []).slice(0, 2)].join(' · ')));
  body.append(head);
  if (item.description && item.description.trim() !== item.title.trim()) body.append(el('p', 'item-description', item.description));
  li.append(body);
  return li;
}

function sourceCard(source) {
  const result = resultFor(source);
  const fresh = updatedToday(source);
  const stale = result.status === 'ok' && !recentlySynced(result);
  const ranking = isRanking(source);
  const card = el('article', `source-card source-row ${fresh ? 'is-fresh' : ''}`);
  card.id = `source-${source.id}`;
  const identity = el('div', 'source-identity');
  const top = el('div', 'card-top');
  if (source.org !== source.name) top.append(el('span', 'org', source.org));
  top.append(el('span', `status ${result.status === 'error' ? 'error' : stale ? 'stale' : fresh ? 'fresh' : result.status === 'ok' ? 'recent' : 'manual'}`,
    result.status === 'error' ? '同步异常' : fresh ? '今日有更新' : result.status === 'ok' ? (ranking ? (recentlySynced(result) ? '当前榜单' : '旧榜单快照') : '有最新条目') : source.mode === 'link' ? '官网入口' : '等待同步'));
  identity.append(top);
  const title = el('h4'); title.append(link(source.name, source.url)); identity.append(title);
  identity.append(el('p', 'note', source.note));
  const meta = el('div', 'card-meta'); meta.append(el('span', '', cadenceLabel[source.cadence] || '不定期'));
  if (result.lastSuccessAt) meta.append(el('span', '', `同步 ${shortDate(result.lastSuccessAt)}`));
  identity.append(meta);
  const links = el('div', 'card-bottom'); links.append(link('打开官网', source.url, 'official-link'));
  if (source.feed) links.append(link('RSS ↗', source.feed, 'feed-link'));
  identity.append(links);
  if (source.sections?.length) {
    const sections = el('details', 'source-sections'); sections.append(el('summary', '', '官网栏目'));
    const tags = el('div', 'native-tags');
    source.sections.forEach((name) => tags.append(el('span', 'native-tag', name)));
    sections.append(tags); identity.append(sections);
  }
  card.append(identity);
  const content = el('div', 'source-content');
  if (result.items?.length) {
    const list = el(ranking ? 'ol' : 'ul', `item-list ${ranking ? 'ranking-list' : ''}`);
    const initial = ranking ? 5 : 1;
    result.items.slice(0, initial).forEach((item, index) => list.append(itemRow(item, index, ranking)));
    content.append(list);
    if (result.items.length > initial) {
      const more = el('details', 'more-items');
      more.append(el('summary', '', ranking ? `查看第 6–${Math.min(result.items.length, 10)} 名` : `再看 ${Math.min(result.items.length - 1, 4)} 条官网内容`));
      const rest = el(ranking ? 'ol' : 'ul', `item-list ${ranking ? 'ranking-list' : ''}`);
      result.items.slice(initial, ranking ? 10 : 5).forEach((item, index) => rest.append(itemRow(item, index + initial, ranking)));
      more.append(rest); content.append(more);
    }
  } else {
    content.append(el('p', 'no-items', source.mode === 'link' ? '此来源保留官网入口，查看最新内容请打开官网。' : '尚未取得最新条目，可直接打开官网。'));
  }
  if (result.status === 'error') content.append(el('div', 'error-note', `最近一次同步失败${result.lastSuccessAt ? ' · 已保留上次结果' : ''}`));
  card.append(content);
  return card;
}

function matches(source) {
  if (state.activeGroup !== 'all' && source.group !== state.activeGroup) return false;
  if (state.todayOnly && !updatedToday(source)) return false;
  if (!state.query) return true;
  const text = [source.name, source.org, source.note, ...(source.sections || [])].join(' ').toLocaleLowerCase();
  return text.includes(state.query);
}

function renderNav() {
  const nav = $('#groupNav'); nav.replaceChildren();
  const entries = [{ id: 'all', name: '全部来源' }, ...state.catalog.groups];
  entries.forEach((group, index) => {
    const button = el('button', `nav-item ${state.activeGroup === group.id ? 'active' : ''}`);
    button.type = 'button'; button.setAttribute('aria-pressed', state.activeGroup === group.id);
    button.append(el('span', 'nav-index', String(index).padStart(2, '0')), el('span', 'nav-name', group.name));
    button.append(el('span', 'nav-count', String(group.id === 'all' ? state.catalog.sources.length : state.catalog.sources.filter((x) => x.group === group.id).length)));
    button.addEventListener('click', () => { state.activeGroup = group.id; render(); }); nav.append(button);
  });
}

function renderTodayStrip(sources) {
  const wrap = $('#todayLinks'); wrap.replaceChildren();
  const fresh = sources.filter(updatedToday).map((source) => ({
    source,
    items: resultFor(source).items.filter((item) => dateKey(item.publishedAt) === today),
  }));
  if (!fresh.length) { wrap.append(el('span', 'today-empty', '今天尚未在已同步来源中发现新条目。下方所有官网入口仍可访问。')); return; }
  fresh.forEach(({ source, items }) => {
    const card = el('article', 'today-card');
    const heading = el('div', 'today-card-head');
    heading.append(link(source.name, source.url, 'today-source'));
    heading.append(el('span', 'today-count', `${items.length} 条 · ${shortDate(items[0].publishedAt)}`));
    card.append(heading);
    card.append(link(items[0].title, items[0].url, 'today-title'));
    if (items[0].description) card.append(el('p', 'today-description', items[0].description));
    if (items[0].categories?.length) card.append(el('span', 'today-category', items[0].categories.slice(0, 2).join(' · ')));
    if (items.length > 1) {
      const more = el('details', 'today-more'); more.append(el('summary', '', `另有 ${items.length - 1} 条今日内容`));
      const list = el('ul'); items.slice(1).forEach((item) => {
        const li = el('li'); li.append(link(item.title, item.url)); list.append(li);
      }); more.append(list); card.append(more);
    }
    wrap.append(card);
  });
}

function render() {
  if (!state.catalog) return;
  renderNav();
  const sources = state.catalog.sources;
  $('#sourceCount').textContent = sources.length;
  $('#trackedCount').textContent = sources.filter((x) => x.mode !== 'link').length;
  $('#rankingCount').textContent = sources.filter(isRanking).length;
  $('#updatedCount').textContent = sources.filter(updatedToday).length;
  renderTodayStrip(sources);
  const unhealthy = sources.filter((source) => source.mode !== 'link' && (resultFor(source).status !== 'ok' || !recentlySynced(resultFor(source))));
  const notice = $('#syncNotice'); notice.hidden = unhealthy.length === 0;
  if (unhealthy.length) notice.textContent = `${unhealthy.length} 个自动来源近期未成功同步。页面保留上次结果及官网入口，最新内容请以官网为准。`;
  const visible = sources.filter(matches); $('#visibleCount').textContent = `${visible.length} / ${sources.length}`;
  $('#syncTime').textContent = state.snapshot?.generatedAt ? `快照生成 · ${new Intl.DateTimeFormat('zh-CN', { timeZone: tz, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(state.snapshot.generatedAt))}（新加坡时间）` : '等候首次自动同步';
  const container = $('#sections'); container.replaceChildren();
  for (const group of state.catalog.groups) {
    const entries = visible.filter((source) => source.group === group.id);
    if (!entries.length) continue;
    const section = el('section', 'group-section'); section.id = `group-${group.id}`;
    const heading = el('div', 'group-heading'); const label = el('div'); label.append(el('span', 'section-kicker', group.id.toUpperCase())); label.append(el('h3', '', group.name));
    heading.append(label, el('p', '', group.description), el('span', 'group-count', `${entries.length} 个来源`));
    section.append(heading);
    const grid = el('div', 'card-grid'); entries.forEach((source) => grid.append(sourceCard(source))); section.append(grid); container.append(section);
  }
  if (!visible.length) container.append(el('div', 'empty', '没有匹配的来源。清除搜索或关闭“只看今日发布”即可浏览全部入口。'));
}

async function init() {
  try {
    const [catalogResponse, snapshotResponse] = await Promise.all([fetch('data/sources.json'), fetch('data/latest.json')]);
    if (!catalogResponse.ok) throw new Error('来源目录加载失败');
    state.catalog = await catalogResponse.json();
    state.snapshot = snapshotResponse.ok ? await snapshotResponse.json() : null;
    render();
  } catch (error) { $('#sections').replaceChildren(el('div', 'empty', `${error.message}。请通过本地 HTTP 服务或 GitHub Pages 打开页面。`)); }
}
$('#search').addEventListener('input', (event) => { state.query = event.target.value.trim().toLocaleLowerCase(); render(); });
$('#todayOnly').addEventListener('change', (event) => { state.todayOnly = event.target.checked; render(); });
init();
