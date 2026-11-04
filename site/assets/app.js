const $ = (selector) => document.querySelector(selector);
const state = { catalog: null, snapshot: null, query: '', todayOnly: false, activeGroup: 'all', pages: new Map() };
const tz = 'Asia/Singapore';
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
const resultFor = (source) => state.snapshot?.results?.[source.id] || { status: source.mode === 'link' ? 'directory' : 'pending', items: [] };
const isRanking = (source) => ['github_trending', 'hf_trending', 'papers_trending', 'aa_leaderboard'].includes(source.mode);
const recentlySynced = (result) => Boolean(result.lastSuccessAt && Date.now() - Date.parse(result.lastSuccessAt) <= 36 * 60 * 60 * 1000);
const updatedToday = (source) => !isRanking(source) && resultFor(source).status === 'ok' && resultFor(source).items.some((item) => dateKey(item.publishedAt) === today);
const PAGE_SIZE = 3;

function itemRow(item, index, ranking) {
  const fresh = !ranking && dateKey(item.publishedAt) === today;
  const row = el('li', `item-row ${fresh ? 'published-today' : 'published-earlier'}`);
  if (ranking) row.append(el('span', 'rank-number', String(index + 1).padStart(2, '0')));
  const body = el('div', 'item-body');
  const heading = el('div', 'item-heading');
  heading.append(link(item.title, item.url, 'item-link'));
  if (!ranking) {
    const detail = [...(fresh ? [] : [displayDate(item.publishedAt)]), ...(item.categories || []).slice(0, 2)].join(' · ');
    if (detail) heading.append(el('span', 'item-detail', detail));
  }
  body.append(heading);
  if (item.description && item.description.trim() !== item.title.trim()) body.append(el('p', 'item-description', item.description));
  row.append(body);
  return row;
}

function sourceCard(source) {
  const result = resultFor(source);
  const ranking = isRanking(source);
  const fresh = updatedToday(source);
  const stale = result.status === 'ok' && !recentlySynced(result);
  const card = el('article', `source-card ${fresh ? 'is-fresh' : ''} ${source.mode === 'link' ? 'is-directory' : ''}`);
  card.id = `source-${source.id}`;
  const identity = el('div', 'source-identity');
  const top = el('div', 'card-top');
  if (source.org !== source.name) top.append(el('span', 'org', source.org));
  top.append(el('span', `status ${result.status === 'error' ? 'error' : stale ? 'stale' : fresh ? 'fresh' : result.status === 'ok' ? 'recent' : 'manual'}`,
    result.status === 'error' ? '同步异常' : fresh ? '今天有更新' : result.status === 'ok' ? (ranking ? (recentlySynced(result) ? '当前榜单' : '旧榜单快照') : '此前有发布') : source.mode === 'link' ? '官网入口' : '等待同步'));
  identity.append(top);
  const title = el('h3', 'source-name'); title.append(link(source.name, source.url)); identity.append(title);
  identity.append(el('p', 'source-note', source.note));
  const links = el('div', 'source-links');
  links.append(link('打开官网 ↗', source.url, 'official-link'));
  if (source.feed) links.append(link('RSS ↗', source.feed, 'feed-link'));
  identity.append(links);
  if (source.sections?.length) {
    const sections = el('details', 'source-sections');
    sections.append(el('summary', '', '官网栏目'));
    const tags = el('div', 'native-tags');
    source.sections.forEach((name) => tags.append(el('span', 'native-tag', name)));
    sections.append(tags); identity.append(sections);
  }
  card.append(identity);
  if (source.mode === 'link') return card;

  const content = el('div', 'source-content');
  if (result.items?.length) {
    const pageSize = ranking ? 5 : PAGE_SIZE;
    const totalPages = Math.ceil(result.items.length / pageSize);
    const renderPage = () => {
      const page = Math.min(state.pages.get(source.id) || 0, totalPages - 1);
      content.replaceChildren();
      const list = el(ranking ? 'ol' : 'ul', `item-list ${ranking ? 'ranking-list' : ''}`);
      result.items.slice(page * pageSize, (page + 1) * pageSize).forEach((item, offset) => list.append(itemRow(item, page * pageSize + offset, ranking)));
      content.append(list);
      if (totalPages > 1) {
        const pager = el('nav', 'pager');
        pager.setAttribute('aria-label', `${source.name} 条目分页`);
        pager.append(el('span', 'page-count', `${result.items.length} 条 · 第 ${page + 1} / ${totalPages} 页`));
        const buttons = el('div', 'page-buttons');
        const addButton = (label, target, disabled, current = false) => {
          const button = el('button', `page-button ${current ? 'current' : ''}`, label);
          button.type = 'button'; button.disabled = disabled;
          if (current) button.setAttribute('aria-current', 'page');
          button.addEventListener('click', () => {
            state.pages.set(source.id, target);
            renderPage();
            content.querySelector('.page-button.current')?.focus();
          });
          buttons.append(button);
        };
        addButton('上一页', page - 1, page === 0);
        for (let i = 0; i < totalPages; i++) addButton(String(i + 1), i, false, i === page);
        addButton('下一页', page + 1, page === totalPages - 1);
        pager.append(buttons); content.append(pager);
      }
      if (result.status === 'error') content.append(el('p', 'error-note', `最近一次同步失败${result.lastSuccessAt ? `，上次成功 ${displayDate(result.lastSuccessAt)}` : ''}`));
    };
    renderPage();
  } else {
    content.append(el('p', 'no-items', '尚未取得条目，可直接打开官网。'));
    if (result.status === 'error') content.append(el('p', 'error-note', '最近一次同步失败'));
  }
  card.append(content);
  return card;
}

function matches(source) {
  if (state.activeGroup !== 'all' && source.group !== state.activeGroup) return false;
  if (state.todayOnly && !updatedToday(source)) return false;
  if (!state.query) return true;
  return [source.name, source.org, source.note, ...(source.sections || [])].join(' ').toLocaleLowerCase().includes(state.query);
}

function renderNav() {
  const nav = $('#groupNav'); nav.replaceChildren();
  [{ id: 'all', name: '全部来源' }, ...state.catalog.groups].forEach((group, index) => {
    const button = el('button', `nav-item ${state.activeGroup === group.id ? 'active' : ''}`);
    button.type = 'button'; button.setAttribute('aria-pressed', state.activeGroup === group.id);
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

function render() {
  if (!state.catalog) return;
  renderNav();
  const sources = state.catalog.sources;
  $('#sourceCount').textContent = sources.length;
  const unhealthy = sources.filter((source) => source.mode !== 'link' && (resultFor(source).status !== 'ok' || !recentlySynced(resultFor(source))));
  const notice = $('#syncNotice'); notice.hidden = unhealthy.length === 0;
  if (unhealthy.length) notice.textContent = `${unhealthy.length} 个自动来源近期未成功同步；旧结果和官网入口仍可访问。`;
  $('#syncTime').textContent = state.snapshot?.generatedAt ? `快照 ${new Intl.DateTimeFormat('zh-CN', { timeZone: tz, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(state.snapshot.generatedAt))} · 新加坡时间` : '等候首次同步';
  const visible = sources.filter(matches);
  $('#visibleCount').textContent = `${visible.length} / ${sources.length}`;
  const container = $('#sections'); container.replaceChildren();
  for (const group of state.catalog.groups) {
    const entries = visible.filter((source) => source.group === group.id);
    if (!entries.length) continue;
    const section = el('section', 'group-section'); section.id = `group-${group.id}`;
    const heading = el('div', 'group-heading');
    const label = el('div'); label.append(el('span', 'section-kicker', group.id.toUpperCase()), el('h2', '', group.name));
    heading.append(label, el('p', '', group.description), el('span', 'group-count', `${entries.length} 个来源`));
    section.append(heading);
    const list = el('div', 'source-list'); entries.forEach((source) => list.append(sourceCard(source)));
    section.append(list); container.append(section);
  }
  if (!visible.length) container.append(el('div', 'empty', '没有匹配的来源。清除搜索或关闭“只看今天更新”即可浏览全部入口。'));
}

async function init() {
  try {
    const [catalogResponse, snapshotResponse] = await Promise.all([fetch('data/sources.json'), fetch('data/latest.json')]);
    if (!catalogResponse.ok) throw new Error('来源目录加载失败');
    state.catalog = await catalogResponse.json();
    state.snapshot = snapshotResponse.ok ? await snapshotResponse.json() : null;
    render();
  } catch (error) { $('#sections').replaceChildren(el('div', 'empty', `${error.message}。请通过本地 HTTP 服务打开页面。`)); }
}
$('#search').addEventListener('input', (event) => { state.query = event.target.value.trim().toLocaleLowerCase(); render(); });
$('#todayOnly').addEventListener('change', (event) => { state.todayOnly = event.target.checked; state.pages.clear(); render(); });
init();
