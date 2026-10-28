(() => {
  'use strict';

  const PAGE_SIZE = 60;
  const SEARCH_DEBOUNCE_MS = 120;
  const COPY_FEEDBACK_MS = 1600;

  const grid = document.getElementById('grid');
  const emptyBox = document.getElementById('empty');
  const emptyTitle = emptyBox.querySelector('.empty-title');
  const emptyText = emptyBox.querySelector('.empty-text');
  const statusLine = document.getElementById('status-line');
  const errorBox = document.getElementById('error-box');
  const errorText = document.getElementById('error-text');
  const retryButton = document.getElementById('retry-button');
  const resultCount = document.getElementById('result-count');
  const searchInput = document.getElementById('search');
  const sortSelect = document.getElementById('sort');
  const loadMoreWrap = document.getElementById('load-more-wrap');
  const loadMoreButton = document.getElementById('load-more');
  const liveRegion = document.getElementById('live');
  const header = document.querySelector('.site-header');
  const catalogSection = document.querySelector('.catalog');
  const cardTemplate = document.getElementById('card-template');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  const collator = new Intl.Collator('zh', { numeric: true, sensitivity: 'base' });
  const numberFormat = new Intl.NumberFormat('zh-CN');
  const copyTimers = new WeakMap();

  let voices = [];
  let bases = { favorites: [], label: [] };
  let view = [];
  let renderedCount = 0;
  let activeSort = 'favorites';
  let query = '';
  let searchTimer = 0;
  let composing = false;
  let state = 'idle';

  function normalizeCatalog(raw) {
    if (!raw || typeof raw !== 'object') {
      throw new Error('catalog.json 不是有效的对象');
    }

    const items = [];
    for (const [slug, value] of Object.entries(raw)) {
      if (!slug || !value || typeof value !== 'object') continue;

      const label = typeof value.label === 'string' && value.label.trim()
        ? value.label.trim()
        : slug;

      items.push({
        slug,
        label,
        url: toSafeUrl(value.url),
        favorites: toFavorites(value.favorites),
        searchText: `${label}\n${slug}`.toLowerCase(),
      });
    }
    return items;
  }

  function toFavorites(value) {
    if (value === null || value === undefined || value === '') return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }

  function toSafeUrl(value) {
    if (typeof value !== 'string' || !value.trim()) return null;
    try {
      const url = new URL(value, window.location.href);
      if (url.protocol === 'http:' || url.protocol === 'https:') {
        return url.href;
      }
    } catch (_) {
      // Ignore malformed URLs; the card simply omits the download link.
    }
    return null;
  }

  function compareFavorites(a, b) {
    const aFav = a.favorites === null ? -1 : a.favorites;
    const bFav = b.favorites === null ? -1 : b.favorites;
    if (aFav !== bFav) return bFav - aFav;
    return compareLabels(a, b);
  }

  function compareLabels(a, b) {
    return collator.compare(a.label, b.label) || collator.compare(a.slug, b.slug);
  }

  function showLoading() {
    state = 'loading';
    voices = [];
    bases = { favorites: [], label: [] };
    view = [];
    renderedCount = 0;
    grid.replaceChildren();
    emptyBox.hidden = true;
    errorBox.hidden = true;
    loadMoreWrap.hidden = true;
    statusLine.hidden = false;
    statusLine.textContent = '正在加载音色目录…';
    resultCount.textContent = '正在加载…';
  }

  function showError() {
    state = 'error';
    grid.replaceChildren();
    emptyBox.hidden = true;
    loadMoreWrap.hidden = true;
    statusLine.hidden = true;
    errorText.textContent = '音色目录加载失败，请检查网络后重试。';
    errorBox.hidden = false;
    resultCount.textContent = '加载失败';
  }

  async function loadCatalog() {
    showLoading();
    try {
      const response = await fetch('./catalog.json', { cache: 'no-cache' });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const raw = await response.json();
      voices = normalizeCatalog(raw);
      bases = {
        favorites: voices.slice().sort(compareFavorites),
        label: voices.slice().sort(compareLabels),
      };
      state = 'ready';
      statusLine.hidden = true;
      errorBox.hidden = true;
      applyView();
    } catch (error) {
      console.error('加载音色目录失败：', error);
      showError();
    }
  }

  function applyView() {
    if (state !== 'ready') return;

    const tokens = query.split(/\s+/).filter(Boolean);
    const base = bases[activeSort];
    view = tokens.length
      ? base.filter((item) => tokens.every((token) => item.searchText.includes(token)))
      : base;

    renderedCount = 0;
    grid.replaceChildren();

    const catalogEmpty = voices.length === 0;
    emptyBox.hidden = view.length !== 0;
    emptyTitle.textContent = catalogEmpty && !query ? '音色目录暂无内容' : '没有找到匹配的音色';
    emptyText.hidden = catalogEmpty && !query;

    const countLabel = numberFormat.format(view.length);
    resultCount.textContent = query ? `找到 ${countLabel} 个音色` : `共 ${countLabel} 个音色`;

    renderNextPage();
  }

  function renderNextPage() {
    if (renderedCount >= view.length) {
      updateLoadMore();
      return;
    }

    const next = view.slice(renderedCount, renderedCount + PAGE_SIZE);
    const fragment = document.createDocumentFragment();
    for (const item of next) {
      fragment.appendChild(createCard(item));
    }
    grid.appendChild(fragment);
    renderedCount += next.length;
    updateLoadMore();
  }

  function updateLoadMore() {
    const remaining = view.length - renderedCount;
    if (remaining <= 0) {
      loadMoreWrap.hidden = true;
      return;
    }
    loadMoreWrap.hidden = false;
    loadMoreButton.textContent = `加载更多（还有 ${numberFormat.format(remaining)} 个）`;
  }

  function createCard(item) {
    const card = cardTemplate.content.firstElementChild.cloneNode(true);

    const label = card.querySelector('.card-label');
    label.textContent = item.label;
    label.title = item.label;

    const favorites = card.querySelector('.card-fav');
    if (item.favorites === null) {
      favorites.remove();
    } else {
      const text = numberFormat.format(item.favorites);
      favorites.querySelector('.fav-count').textContent = text;
      favorites.title = `收藏 ${text}`;
    }

    const slug = card.querySelector('.slug');
    slug.textContent = item.slug;
    slug.title = item.slug;

    const copyButton = card.querySelector('.copy');
    copyButton.dataset.slug = item.slug;
    copyButton.setAttribute('aria-label', `复制音色名 ${item.slug}`);

    const download = card.querySelector('.download');
    if (item.url) {
      download.href = item.url;
    } else {
      download.closest('.card-foot').remove();
    }

    return card;
  }

  function scheduleSearch() {
    clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      const nextQuery = searchInput.value.trim().toLowerCase();
      if (nextQuery === query) return;
      query = nextQuery;
      applyView();
      revealResultsIfNeeded();
    }, SEARCH_DEBOUNCE_MS);
  }

  function revealResultsIfNeeded() {
    if (state !== 'ready' || window.scrollY === 0) return;
    const headerHeight = header.offsetHeight;
    const top = catalogSection.getBoundingClientRect().top;
    if (top >= headerHeight) return;
    window.scrollTo({
      top: window.scrollY + top - headerHeight - 8,
      behavior: reducedMotion.matches ? 'auto' : 'smooth',
    });
  }

  async function copyToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (_) {
        // Fall through to the legacy path.
      }
    }

    const helper = document.createElement('textarea');
    helper.value = text;
    helper.setAttribute('readonly', '');
    helper.style.position = 'fixed';
    helper.style.top = '-9999px';
    helper.style.opacity = '0';
    document.body.appendChild(helper);
    try {
      helper.select();
      helper.setSelectionRange(0, helper.value.length);
      return document.execCommand('copy');
    } catch (_) {
      return false;
    } finally {
      helper.remove();
    }
  }

  function showCopyFeedback(button, succeeded, slug) {
    clearTimeout(copyTimers.get(button));
    button.textContent = succeeded ? '已复制' : '复制失败';
    button.classList.toggle('is-copied', succeeded);
    button.classList.toggle('is-error', !succeeded);
    liveRegion.textContent = succeeded
      ? `已复制音色名 ${slug}`
      : `复制音色名 ${slug} 失败，请手动选择复制`;

    copyTimers.set(button, window.setTimeout(() => {
      button.textContent = '复制';
      button.classList.remove('is-copied', 'is-error');
      copyTimers.delete(button);
    }, COPY_FEEDBACK_MS));
  }

  grid.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target.closest('.copy') : null;
    if (!target || !grid.contains(target)) return;
    const slug = target.dataset.slug;
    if (!slug) return;
    copyToClipboard(slug).then((succeeded) => showCopyFeedback(target, succeeded, slug));
  });

  searchInput.addEventListener('compositionstart', () => {
    composing = true;
  });

  searchInput.addEventListener('compositionend', () => {
    composing = false;
    scheduleSearch();
  });

  searchInput.addEventListener('input', () => {
    if (!composing) scheduleSearch();
  });

  sortSelect.addEventListener('change', () => {
    activeSort = sortSelect.value === 'label' ? 'label' : 'favorites';
    applyView();
    revealResultsIfNeeded();
  });

  loadMoreButton.addEventListener('click', () => {
    const before = renderedCount;
    renderNextPage();
    const added = renderedCount - before;
    if (added > 0) {
      liveRegion.textContent = `已加载 ${numberFormat.format(added)} 个音色`;
    }
  });

  retryButton.addEventListener('click', loadCatalog);

  loadCatalog();
})();
