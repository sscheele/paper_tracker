/* --- State --- */
let state = {
  papers: [],
  total: 0,
  filters: { unread_only: true, favorite_only: false, author: '', category: '', tag: '', sort: 'published', order: 'desc' },
  offset: 0,
  limit: 50,
  selected: new Set(),
  searchResults: [],
  trackedAuthors: [],
  autoRefresh: false,
  autoRefreshInterval: null,
};

/* --- Init --- */
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  loadTrackedAuthors().then(() => loadPapers());
  loadFilters();
  loadStats();

  document.getElementById('filter-view').addEventListener('click', handleViewFilter);
  document.getElementById('filter-author').addEventListener('change', e => { state.filters.author = e.target.value; state.offset = 0; loadPapers(); });
  document.getElementById('filter-category').addEventListener('change', e => { state.filters.category = e.target.value; state.offset = 0; loadPapers(); });
  document.getElementById('filter-tag').addEventListener('change', e => { state.filters.tag = e.target.value; state.offset = 0; loadPapers(); });
  document.getElementById('filter-sort').addEventListener('change', e => {
    const [sort, order] = e.target.value.split('-');
    state.filters.sort = sort;
    state.filters.order = order;
    state.offset = 0;
    loadPapers();
  });

  document.getElementById('btn-fetch').addEventListener('click', quickFetch);
  document.getElementById('btn-fetch-advanced').addEventListener('click', openFetchModal);
  document.getElementById('btn-start-fetch').addEventListener('click', startFetchFromModal);
  document.getElementById('btn-search').addEventListener('click', () => showModal('search-modal'));
  document.getElementById('btn-settings').addEventListener('click', () => { loadAuthors(); showModal('settings-modal'); });
  document.getElementById('btn-theme').addEventListener('click', toggleTheme);
  document.getElementById('btn-auto-refresh').addEventListener('click', toggleAutoRefresh);

  // Bulk actions
  document.getElementById('bulk-read').addEventListener('click', () => bulkAction('read'));
  document.getElementById('bulk-unread').addEventListener('click', () => bulkAction('unread'));
  document.getElementById('bulk-tag').addEventListener('click', bulkTag);
  document.getElementById('bulk-select-all').addEventListener('click', selectAll);
  document.getElementById('bulk-deselect').addEventListener('click', deselectAll);

  // Search form
  document.getElementById('search-form').addEventListener('submit', handleSearch);
  document.getElementById('search-save').addEventListener('click', saveSearchResults);

  // Settings form
  document.getElementById('add-author-form').addEventListener('submit', handleAddAuthor);

  // Catchup
  document.getElementById('btn-catchup').addEventListener('click', handleCatchup);

  // Close modals
  document.querySelectorAll('.modal-close').forEach(btn => {
    btn.addEventListener('click', () => btn.closest('.modal-overlay').classList.remove('visible'));
  });
  document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.classList.remove('visible'); });
  });
});

/* --- API helpers --- */
async function api(path, opts = {}) {
  let resp;
  try {
    resp = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...opts.headers },
      ...opts,
    });
  } catch (e) {
    toast(`Network error: ${e.message}`, 'error');
    throw e;
  }
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ error: resp.statusText }));
    const message = err.error || resp.statusText;
    toast(message, 'error');
    throw new Error(message);
  }
  return resp.json();
}

/* --- Papers --- */
async function loadPapers() {
  const f = state.filters;
  const params = new URLSearchParams({
    unread_only: f.unread_only,
    favorite_only: f.favorite_only,
    sort: f.sort,
    order: f.order,
    limit: state.limit,
    offset: state.offset,
  });
  if (f.author) params.set('author', f.author);
  if (f.category) params.set('category', f.category);
  if (f.tag) params.set('tag', f.tag);

  try {
    const data = await api(`/api/papers?${params}`);
    state.papers = data.papers;
    state.total = data.total;
    renderPapers();
    renderPagination();
  } catch (e) { /* toast already shown by api() */ }
}

function renderPapers() {
  const list = document.getElementById('paper-list');
  if (state.papers.length === 0) {
    list.innerHTML = '<div class="empty-state"><p>No papers found.</p></div>';
    return;
  }
  list.innerHTML = state.papers.map(renderPaperCard).join('');
}

function renderPaperCard(paper) {
  const isUnread = !paper.read;
  const isFav = paper.favorite;
  const isSelected = state.selected.has(paper.arxiv_id);
  const tags = (paper.tags || []).map(t =>
    `<span class="tag-badge">${esc(t)}<span class="remove-tag" onclick="removeTag('${esc(paper.arxiv_id)}','${esc(t)}')">&times;</span></span>`
  ).join('');

  return `
    <div class="paper-card ${isUnread ? 'unread' : ''} ${isSelected ? 'selected' : ''}" data-id="${esc(paper.arxiv_id)}">
      <div class="paper-header">
        <input type="checkbox" ${isSelected ? 'checked' : ''} onchange="toggleSelect('${esc(paper.arxiv_id)}')">
        <h3 class="paper-title"><a href="${esc(paper.abs_url)}" target="_blank">${esc(paper.title)}</a></h3>
        <button class="star-btn ${isFav ? 'active' : ''}" onclick="toggleFavorite('${esc(paper.arxiv_id)}')">${isFav ? '\u2605' : '\u2606'}</button>
      </div>
      <div class="paper-meta">
        <span class="authors">${formatAuthors(paper.authors)}</span> &middot;
        <span class="date">${paper.published ? paper.published.slice(0, 10) : ''}</span> &middot;
        <span class="categories">${esc(paper.categories || '')}</span>
        ${paper.pdf_url ? ` &middot; <a href="${esc(paper.pdf_url)}" target="_blank">pdf</a>` : ''}
      </div>
      <div class="paper-tags">
        ${tags}
        <input class="add-tag-inline" type="text" placeholder="+tag"
          onkeydown="if(event.key==='Enter'){addTagFromInput('${esc(paper.arxiv_id)}',this);event.preventDefault();}">
      </div>
      <div class="paper-abstract" id="abstract-${esc(paper.arxiv_id)}">${esc(paper.abstract || '')}</div>
      <div class="paper-notes" id="notes-${esc(paper.arxiv_id)}">
        <textarea placeholder="Notes..."
          oninput="debouncedSaveNotes('${esc(paper.arxiv_id)}',this.value)">${esc(paper.notes || '')}</textarea>
      </div>
      <div class="paper-actions">
        <button onclick="toggleAbstract('${esc(paper.arxiv_id)}')">abstract</button>
        <button onclick="toggleNotes('${esc(paper.arxiv_id)}')">notes</button>
        ${isUnread
          ? `<button onclick="markRead('${esc(paper.arxiv_id)}')">mark read</button>`
          : `<button onclick="markUnread('${esc(paper.arxiv_id)}')">mark unread</button>`}
      </div>
    </div>`;
}

function renderPagination() {
  const pg = document.getElementById('pagination');
  const totalPages = Math.ceil(state.total / state.limit) || 1;
  const currentPage = Math.floor(state.offset / state.limit) + 1;
  pg.innerHTML = `
    <button ${currentPage <= 1 ? 'disabled' : ''} onclick="goPage(${currentPage - 1})">Prev</button>
    <span class="page-info">Page ${currentPage} of ${totalPages} (${state.total} papers)</span>
    <button ${currentPage >= totalPages ? 'disabled' : ''} onclick="goPage(${currentPage + 1})">Next</button>
  `;
}

function goPage(page) {
  state.offset = (page - 1) * state.limit;
  loadPapers();
  window.scrollTo(0, 0);
}

/* --- View filters --- */
function handleViewFilter(e) {
  const btn = e.target.closest('button[data-view]');
  if (!btn) return;
  const view = btn.dataset.view;
  document.querySelectorAll('#filter-view button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  state.filters.unread_only = view === 'unread';
  state.filters.favorite_only = view === 'favorites';
  state.offset = 0;
  loadPapers();
}

/* --- Actions --- */
async function markRead(id) {
  try { await api(`/api/papers/${encodeURIComponent(id)}/read`, { method: 'POST' }); refreshCurrent(); } catch (e) {}
}

async function markUnread(id) {
  try { await api(`/api/papers/${encodeURIComponent(id)}/unread`, { method: 'POST' }); refreshCurrent(); } catch (e) {}
}

async function toggleFavorite(id) {
  try {
    const data = await api(`/api/papers/${encodeURIComponent(id)}/favorite`, { method: 'POST' });
    refreshCurrent();
    if (data.favorite) {
      toast('Downloading TeX source...', 'success');
      pollTexSource(id);
    }
  } catch (e) {}
}

function pollTexSource(id, attempts = 0) {
  if (attempts > 40) { toast(`TeX source download may have failed for ${id}`, 'error'); return; }
  setTimeout(async () => {
    try {
      const resp = await fetch(`/api/papers/${encodeURIComponent(id)}/tex`);
      if (resp.ok) {
        toast(`TeX source downloaded for ${id}`, 'success');
      } else {
        pollTexSource(id, attempts + 1);
      }
    } catch (e) {
      pollTexSource(id, attempts + 1);
    }
  }, 3000);
}

async function addTagFromInput(id, input) {
  const tag = input.value.trim();
  if (!tag) return;
  try {
    await api(`/api/papers/${encodeURIComponent(id)}/tags`, { method: 'POST', body: JSON.stringify({ tag }) });
    input.value = '';
    refreshCurrent();
  } catch (e) {}
}

async function removeTag(id, tag) {
  try { await api(`/api/papers/${encodeURIComponent(id)}/tags/${encodeURIComponent(tag)}`, { method: 'DELETE' }); refreshCurrent(); } catch (e) {}
}

const _notesTimers = {};
function debouncedSaveNotes(id, text) {
  clearTimeout(_notesTimers[id]);
  _notesTimers[id] = setTimeout(() => {
    api(`/api/papers/${encodeURIComponent(id)}/notes`, { method: 'PUT', body: JSON.stringify({ notes: text }) });
  }, 500);
}

function toggleAbstract(id) {
  document.getElementById(`abstract-${id}`).classList.toggle('visible');
}

function toggleNotes(id) {
  document.getElementById(`notes-${id}`).classList.toggle('visible');
}

/* --- Selection & bulk --- */
function toggleSelect(id) {
  if (state.selected.has(id)) state.selected.delete(id);
  else state.selected.add(id);
  updateBulkBar();
  const card = document.querySelector(`.paper-card[data-id="${id}"]`);
  if (card) card.classList.toggle('selected');
}

function selectAll() {
  state.papers.forEach(p => state.selected.add(p.arxiv_id));
  updateBulkBar();
  loadPapers();
}

function deselectAll() {
  state.selected.clear();
  updateBulkBar();
  loadPapers();
}

function updateBulkBar() {
  const bar = document.getElementById('bulk-bar');
  const count = state.selected.size;
  bar.classList.toggle('visible', count > 0);
  document.getElementById('bulk-count').textContent = `${count} selected`;
}

async function bulkAction(action) {
  const ids = [...state.selected];
  if (ids.length === 0) return;
  try {
    await api(`/api/papers/bulk-${action}`, { method: 'POST', body: JSON.stringify({ arxiv_ids: ids }) });
    state.selected.clear();
    updateBulkBar();
    refreshCurrent();
    toast(`${ids.length} papers marked ${action}`, 'success');
  } catch (e) {}
}

async function bulkTag() {
  const tag = prompt('Tag name:');
  if (!tag || !tag.trim()) return;
  try {
    for (const id of state.selected) {
      await api(`/api/papers/${encodeURIComponent(id)}/tags`, { method: 'POST', body: JSON.stringify({ tag: tag.trim() }) });
    }
    state.selected.clear();
    updateBulkBar();
    refreshCurrent();
    toast(`Tag "${tag.trim()}" added`, 'success');
  } catch (e) {}
}

async function handleCatchup() {
  if (!confirm('Mark all papers as read?')) return;
  try {
    await api('/api/papers/catchup', { method: 'POST' });
    refreshCurrent();
    toast('All papers marked as read', 'success');
  } catch (e) {}
}

/* --- Stats & filters --- */
async function loadStats() {
  try {
    const s = await api('/api/stats');
    document.getElementById('unread-badge').textContent = s.unread;
  } catch (e) {}
}

async function loadFilters() {
  try {
    const data = await api('/api/filters');
    populateSelect('filter-author', data.authors, 'All authors');
    populateSelect('filter-category', data.categories, 'All categories');
    populateSelect('filter-tag', data.tags, 'All tags');
  } catch (e) {}
}

function populateSelect(id, items, placeholder) {
  const sel = document.getElementById(id);
  sel.innerHTML = `<option value="">${placeholder}</option>` +
    items.map(i => `<option value="${esc(i)}">${esc(i)}</option>`).join('');
}

/* --- Fetch --- */
async function checkFetchConfig() {
  try {
    const config = await api('/api/config');
    if (!config.mailto) { toast('Set your email in Settings before fetching (required by arxiv API).', 'error'); return null; }
    if (!config.authors || config.authors.length === 0) { toast('Add at least one author in Settings before fetching.', 'error'); return null; }
    return config;
  } catch (e) { return null; }
}

async function quickFetch() {
  const config = await checkFetchConfig();
  if (!config) return;
  toast('Fetching papers...', 'success');
  document.getElementById('btn-fetch').disabled = true;
  // Use default lookback_days from config
  runFetchSSE();
}

async function openFetchModal() {
  const config = await checkFetchConfig();
  if (!config) return;
  document.getElementById('fetch-days').value = config.lookback_days || 7;
  document.getElementById('fetch-progress').style.display = 'none';
  document.getElementById('fetch-log').innerHTML = '';
  document.getElementById('btn-start-fetch').disabled = false;
  showModal('fetch-modal');
}

function startFetchFromModal() {
  const days = document.getElementById('fetch-days').value || 7;
  document.getElementById('btn-start-fetch').disabled = true;
  runFetchSSE(days);
}

function runFetchSSE(days) {
  const params = days != null ? `?days=${encodeURIComponent(days)}` : '';
  const log = document.getElementById('fetch-log');
  const progress = document.getElementById('fetch-progress');
  // Show progress in modal if it's open, otherwise just toast on completion
  const modalOpen = document.getElementById('fetch-modal').classList.contains('visible');
  if (modalOpen) {
    progress.style.display = 'block';
    log.innerHTML = '<div>Starting fetch...</div>';
  }

  const source = new EventSource(`/api/fetch${params}`);
  source.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (modalOpen) {
      if (msg.type === 'progress') {
        log.innerHTML += `<div>${esc(msg.message)}</div>`;
      } else if (msg.type === 'result') {
        log.innerHTML += `<div>${msg.found} found, ${msg.new} new</div>`;
      } else if (msg.type === 'error') {
        log.innerHTML += `<div style="color:var(--error)">Error: ${esc(msg.message)}</div>`;
      }
      log.scrollTop = log.scrollHeight;
    }
    if (msg.type === 'done') {
      source.close();
      document.getElementById('btn-fetch').disabled = false;
      document.getElementById('btn-start-fetch').disabled = false;
      toast(`Fetch complete: ${msg.total_new} new papers`, 'success');
      refreshCurrent();
      loadFilters();
      if (modalOpen) {
        log.innerHTML += `<div><strong>Done! ${msg.total_new} new papers.</strong></div>`;
        log.scrollTop = log.scrollHeight;
      }
    }
  };
  source.onerror = () => {
    source.close();
    document.getElementById('btn-fetch').disabled = false;
    document.getElementById('btn-start-fetch').disabled = false;
    if (modalOpen) {
      log.innerHTML += '<div style="color:var(--error)">Connection lost.</div>';
    } else {
      toast('Fetch failed: connection lost', 'error');
    }
  };
}

/* --- Search --- */
async function handleSearch(e) {
  e.preventDefault();
  const author = document.getElementById('search-author').value.trim();
  const days = document.getElementById('search-days').value || 30;
  if (!author) return;

  document.getElementById('search-status').textContent = 'Searching...';
  try {
    const data = await api(`/api/search?author=${encodeURIComponent(author)}&days=${days}`);
    state.searchResults = data.results;
    renderSearchResults();
    document.getElementById('search-status').textContent = `${data.results.length} results`;
  } catch (err) {
    document.getElementById('search-status').textContent = `Error: ${err.message}`;
  }
}

function renderSearchResults() {
  const container = document.getElementById('search-results');
  if (state.searchResults.length === 0) {
    container.innerHTML = '<p>No results.</p>';
    return;
  }
  container.innerHTML = state.searchResults.map((r, i) => `
    <div class="search-result-item">
      <input type="checkbox" data-idx="${i}" checked>
      <div class="search-result-info">
        <div class="title"><a href="${esc(r.abs_url)}" target="_blank">${esc(r.title)}</a></div>
        <div class="meta">${esc(r.authors)} &middot; ${r.published.slice(0, 10)}</div>
      </div>
    </div>
  `).join('');
}

async function saveSearchResults() {
  const checks = document.querySelectorAll('#search-results input[type="checkbox"]:checked');
  const papers = [...checks].map(cb => state.searchResults[parseInt(cb.dataset.idx)]);
  if (papers.length === 0) { toast('No papers selected', 'error'); return; }

  try {
    const data = await api('/api/search/save', { method: 'POST', body: JSON.stringify({ papers }) });
    toast(`${data.saved} new papers saved`, 'success');
    hideModal('search-modal');
    refreshCurrent();
    loadFilters();
  } catch (e) {}
}

/* --- Settings / Authors --- */
async function loadSettings() {
  try {
    const data = await api('/api/config');
    document.getElementById('setting-mailto').value = data.mailto || '';
    document.getElementById('setting-lookback').value = data.lookback_days || 7;
    const list = document.getElementById('author-list');
    list.innerHTML = data.authors.map(a => `
      <li>${esc(a)} <button class="btn-small" onclick="removeAuthor('${esc(a)}')">&times;</button></li>
    `).join('');
  } catch (e) {}
}

// Keep for backward compat with existing event wiring
async function loadAuthors() { loadSettings(); }

async function handleAddAuthor(e) {
  e.preventDefault();
  const input = document.getElementById('new-author');
  const name = input.value.trim();
  if (!name) return;
  try {
    await api('/api/authors', { method: 'POST', body: JSON.stringify({ name }) });
    input.value = '';
    loadSettings();
    loadTrackedAuthors().then(() => loadPapers());
    toast(`Added author: ${name}`, 'success');
  } catch (e) {}
}

async function removeAuthor(name) {
  try {
    await api(`/api/authors/${encodeURIComponent(name)}`, { method: 'DELETE' });
    loadSettings();
    loadTrackedAuthors().then(() => loadPapers());
    toast(`Removed author: ${name}`, 'success');
  } catch (e) {}
}

async function saveSettings() {
  const mailto = document.getElementById('setting-mailto').value.trim();
  const lookback_days = parseInt(document.getElementById('setting-lookback').value) || 7;
  try {
    await api('/api/config', { method: 'PUT', body: JSON.stringify({ mailto, lookback_days }) });
    toast('Settings saved', 'success');
  } catch (e) {}
}

/* --- Theme --- */
function initTheme() {
  const saved = localStorage.getItem('theme') || 'light';
  document.documentElement.setAttribute('data-theme', saved);
  updateThemeButton(saved);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
  updateThemeButton(next);
}

function updateThemeButton(theme) {
  document.getElementById('btn-theme').textContent = theme === 'dark' ? '\u2600\uFE0F' : '\uD83C\uDF19';
}

/* --- Auto-refresh --- */
function toggleAutoRefresh() {
  state.autoRefresh = !state.autoRefresh;
  const btn = document.getElementById('btn-auto-refresh');
  if (state.autoRefresh) {
    btn.classList.add('active');
    state.autoRefreshInterval = setInterval(() => { loadPapers(); loadStats(); }, 300000);
  } else {
    btn.classList.remove('active');
    clearInterval(state.autoRefreshInterval);
  }
}

/* --- Helpers --- */
function refreshCurrent() {
  loadPapers();
  loadStats();
}

function showModal(id) { document.getElementById(id).classList.add('visible'); }
function hideModal(id) { document.getElementById(id).classList.remove('visible'); }

function toast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

async function loadTrackedAuthors() {
  try {
    const data = await api('/api/authors');
    state.trackedAuthors = (data.authors || []).map(a => a.toLowerCase());
  } catch (e) {}
}

function formatAuthors(authorsStr) {
  if (!authorsStr) return '';
  return authorsStr.split(', ').map(name => {
    const lower = name.trim().toLowerCase();
    // Normalize both to sorted word sets so "Vaswani, Ashish" matches "Ashish Vaswani"
    const nameWords = new Set(lower.replace(/,/g, '').split(/\s+/).filter(Boolean));
    const tracked = state.trackedAuthors.some(ta => {
      const taWords = new Set(ta.replace(/,/g, '').split(/\s+/).filter(Boolean));
      return taWords.size > 0 && [...taWords].every(w => nameWords.has(w));
    });
    return tracked ? `<strong>${esc(name)}</strong>` : esc(name);
  }).join(', ');
}

function esc(str) {
  if (str == null) return '';
  const d = document.createElement('div');
  d.textContent = String(str);
  return d.innerHTML;
}
