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
const updatedToday = (source) => !isRanking(source) && resultFor(source).status === 'ok' && resultFor(source).items.some((item) => dateKey(item.publishedAt) === today);
const cadenceLabel = { daily: '通常每天', weekly: '通常每周', monthly: '不定期' };

function sourceCard(source) {
  const result = resultFor(source);
  const fresh = updatedToday(source);
  const card = el('article', `source-card ${fresh ? 'is-fresh' : ''}`);
  const top = el('div', 'card-top');
  top.append(el('span', 'org', source.org));
  const status = el('span', `status ${result.status === 'error' ? 'error' : fresh ? 'fresh' : result.status === 'ok' ? 'recent' : 'manual'}`,
    result.status === 'error' ? '同步异常' : fresh ? '今日有更新' : result.status === 'ok' ? (isRanking(source) ? '榜单快照' : '有最新条目') : source.mode === 'link' ? '官网入口' : '等待同步');
  top.append(status); card.append(top);
  const title = el('h4'); title.append(link(source.name, source.url)); card.append(title);
  card.append(el('p', 'note', source.note));
  const meta = el('div', 'card-meta'); meta.append(el('span', '', cadenceLabel[source.cadence] || '不定期'));
  if (source.mode !== 'link') meta.append(el('span', '', source.mode === 'feed' ? 'RSS / Atom' : source.mode === 'papers' ? '官方 API' : ['github_trending', 'aa_leaderboard'].includes(source.mode) ? '官网榜单' : '官方 API'));
  card.append(meta);
  if (source.sections?.length) {
    const tags = el('div', 'native-tags'); tags.append(el('span', 'tag-label', '官网栏目'));
    source.sections.forEach((name) => tags.append(el('span', 'native-tag', name)));
    card.append(tags);
  }
  if (result.items?.length) {
    const heading = el('div', 'item-heading'); heading.append(el('span', '', isRanking(source) ? '当前排名' : '最新线索'));
    if (result.lastSuccessAt) heading.append(el('span', 'item-time', `同步 ${shortDate(result.lastSuccessAt)}`));
    card.append(heading);
    const makeItem = (item, index) => {
      const li = el('li');
      if (isRanking(source)) li.append(el('span', 'rank-number', String(index + 1).padStart(2, '0')));
      const body = el('div', 'item-body'); body.append(link(item.title, item.url, 'item-link'));
      if (!isRanking(source)) {
        const detail = [shortDate(item.publishedAt), ...(item.categories || []).slice(0, 2)].join(' · ');
        body.append(el('span', 'item-detail', detail));
      } else if (item.description) body.append(el('span', 'item-detail', item.description));
      li.append(body); return li;
    };
    const list = el(isRanking(source) ? 'ol' : 'ul', `item-list ${isRanking(source) ? 'ranking-list' : ''}`);
    result.items.slice(0, isRanking(source) ? 5 : 3).forEach((item, index) => list.append(makeItem(item, index)));
    card.append(list);
    if (isRanking(source) && result.items.length > 5) {
      const more = el('details', 'more-ranks');
      more.append(el('summary', '', `展开第 6–${Math.min(result.items.length, 10)} 名`));
      const rest = el('ol', 'item-list ranking-list');
      result.items.slice(5, 10).forEach((item, index) => rest.append(makeItem(item, index + 5)));
      more.append(rest); card.append(more);
    }
  } else {
    card.append(el('div', 'no-items', source.mode === 'link' ? '前往官网浏览最新内容 ↗' : '尚未取得条目，仍可访问官网 ↗'));
  }
  if (result.status === 'error') card.append(el('div', 'error-note', `最近一次同步失败${result.lastSuccessAt ? ' · 已保留上次结果' : ''}`));
  const bottom = el('div', 'card-bottom'); bottom.append(link('打开官网', source.url, 'official-link'));
  if (source.feed) bottom.append(link('订阅源 ↗', source.feed, 'feed-link'));
  card.append(bottom);
  return card;
}

function matches(source) {
  if (state.activeGroup !== 'all' && source.group !== state.activeGroup) return false;
  if (state.todayOnly && !updatedToday(source)) return false;
  if (!state.query) return true;
  const text = [source.name, source.org, source.note, ...(source.sections || []), ...resultFor(source).items.map((x) => x.title)].join(' ').toLocaleLowerCase();
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

function render() {
  if (!state.catalog) return;
  renderNav();
  const sources = state.catalog.sources;
  $('#sourceCount').textContent = sources.length;
  $('#trackedCount').textContent = sources.filter((x) => x.mode !== 'link').length;
  $('#rankingCount').textContent = sources.filter(isRanking).length;
  $('#updatedCount').textContent = sources.filter(updatedToday).length;
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
  if (!visible.length) container.append(el('div', 'empty', '没有匹配的来源。清除搜索或关闭“只看今日更新”即可浏览全部入口。'));
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
