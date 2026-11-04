const $ = (selector) => document.querySelector(selector);
const state = { catalog: null, snapshot: null, query: '', language: 'all', org: 'all', todayOnly: false, activeGroup: 'all', pages: new Map() };
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
const orgCounts = new Map();

const genericCategories = new Set(['ai', 'news', 'blog', '资讯', '人工智能', 'artificial intelligence', 'research blog']);
const numberText = (value) => Number(value).toLocaleString('en-US');
function itemFacts(source, item) {
  const facts = [];
  const add = (label, kind = 'metric') => facts.push(el('span', `item-fact ${kind}`, label));
  const metrics = item.metrics || {};
  if (source.id === 'github-trending') {
    if (Number.isFinite(metrics.stars)) add(`★ ${numberText(metrics.stars)} Stars`);
    if (Number.isFinite(metrics.starsToday)) add(`今日新增 +${numberText(metrics.starsToday)}`);
  } else if (source.mode === 'hf_trending') {
    if (Number.isFinite(metrics.likes)) add(`赞 ${numberText(metrics.likes)}`);
    if (Number.isFinite(metrics.downloads)) add(`下载 ${numberText(metrics.downloads)}`);
    if (item.task) add(`任务 ${item.task}`, 'category');
    if (item.sdk) add(`框架 ${item.sdk}`, 'category');
  } else if (source.mode === 'papers_trending' || source.mode === 'papers') {
    if (Number.isFinite(metrics.upvotes)) add(`投票 ${numberText(metrics.upvotes)}`);
    if (Number.isFinite(metrics.comments)) add(`讨论 ${numberText(metrics.comments)}`);
  } else if (source.mode === 'aa_leaderboard') {
    if (Number.isFinite(metrics.intelligenceIndex)) add(`Intelligence Index ${numberText(metrics.intelligenceIndex)}`);
    if (item.provider) add(`机构 ${item.provider}`, 'category');
  }
  const primaryArxivCategory = source.id.startsWith('arxiv-') ? { 'arxiv-ai': 'cs.AI', 'arxiv-lg': 'cs.LG', 'arxiv-cl': 'cs.CL', 'arxiv-cv': 'cs.CV', 'arxiv-ro': 'cs.RO' }[source.id] : null;
  (item.categories || []).filter((category) => category && !genericCategories.has(category.trim().toLocaleLowerCase()) && category !== primaryArxivCategory).slice(0, 2).forEach((category) => add(category, 'category'));
  if (!facts.length) return null;
  const line = el('div', 'item-facts'); line.append(...facts);
  return line;
}

function itemRow(source, item, index, ranking) {
  const fresh = !ranking && dateKey(item.publishedAt) === today;
  const row = el('li', `item-row ${fresh ? 'published-today' : 'published-earlier'}`);
  if (ranking) row.append(el('span', 'rank-number', String(index + 1).padStart(2, '0')));
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
  const title = el('h4', 'source-name'); title.append(link(source.name, source.url)); identity.append(title);
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
      result.items.slice(page * pageSize, (page + 1) * pageSize).forEach((item, offset) => list.append(itemRow(source, item, page * pageSize + offset, ranking)));
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
  if (state.language !== 'all' && source.language !== state.language) return false;
  if (state.org !== 'all' && source.org !== state.org) return false;
  if (state.todayOnly && !updatedToday(source)) return false;
  if (!state.query) return true;
  if (state.query === '中文') return source.language === 'zh';
  if (state.query === '非中文') return source.language === 'non_zh';
  return [source.name, source.org, source.note, ...(source.sections || [])].join(' ').toLocaleLowerCase().includes(state.query);
}

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
  const query = $('#orgSearch').value.trim().toLocaleLowerCase();
  const options = $('#orgOptions'); options.replaceChildren();
  const names = ['all', ...[...orgCounts.keys()].sort(new Intl.Collator('zh-CN').compare)];
  names.filter((org) => org === 'all' ? !query || '全部机构'.includes(query) : org.toLocaleLowerCase().includes(query)).forEach((org) => {
    const choice = el('button', `org-option ${state.org === org ? 'selected' : ''}`);
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
  const languageNames = { all: '全部语言', zh: '中文', non_zh: '非中文' };
  $('#languageFilterLabel').textContent = languageNames[state.language];
  document.querySelectorAll('#languagePanel [data-language]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.language === state.language)));
  $('#orgFilterLabel').textContent = state.org === 'all' ? '全部机构' : state.org;
  renderOrgOptions();
  renderNav();
  const sources = state.catalog.sources;
  $('#sourceCount').textContent = sources.length;
  const unhealthy = sources.filter((source) => source.mode !== 'link' && (resultFor(source).status !== 'ok' || !recentlySynced(resultFor(source))));
  const notice = $('#syncNotice'); notice.hidden = unhealthy.length === 0;
  if (unhealthy.length) notice.textContent = `${unhealthy.length} 个入口近期未成功同步；旧结果和官网链接仍可访问。`;
  $('#syncTime').textContent = state.snapshot?.generatedAt ? `快照 ${new Intl.DateTimeFormat('zh-CN', { timeZone: tz, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(state.snapshot.generatedAt))} · 新加坡时间` : '等候首次同步';
  const visible = sources.filter(matches);
  $('#resetFilters').hidden = state.activeGroup === 'all' && state.language === 'all' && state.org === 'all' && !state.todayOnly && !state.query;
  $('#visibleCount').textContent = `${visible.length} / ${sources.length}`;
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

async function init() {
  try {
    const [catalogResponse, snapshotResponse] = await Promise.all([fetch('data/sources.json'), fetch('data/latest.json')]);
    if (!catalogResponse.ok) throw new Error('目录加载失败');
    state.catalog = await catalogResponse.json();
    state.snapshot = snapshotResponse.ok ? await snapshotResponse.json() : null;
    state.catalog.sources.forEach((source) => orgCounts.set(source.org, (orgCounts.get(source.org) || 0) + 1));
    render();
  } catch (error) { $('#sections').replaceChildren(el('div', 'empty', `${error.message}。请通过本地 HTTP 服务打开页面。`)); }
}
$('#search').addEventListener('input', (event) => { state.query = event.target.value.trim().toLocaleLowerCase(); render(); });
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
  state.query = ''; state.language = 'all'; state.org = 'all'; state.todayOnly = false; state.activeGroup = 'all';
  $('#search').value = ''; $('#todayOnly').checked = false; closeOrgPanel(); closeLanguagePanel();
  render();
});
init();
