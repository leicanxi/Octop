(() => {
  'use strict';

  const authCard = document.getElementById('auth-card');
  const loginBanner = document.getElementById('login-banner');
  const accountBar = document.getElementById('account-bar');
  const accountName = document.getElementById('account-name');
  const logoutButton = document.getElementById('logout-button');
  const statusLine = document.getElementById('status-line');
  const list = document.getElementById('voice-list');
  const empty = document.getElementById('empty');
  const liveRegion = document.getElementById('live');

  const STATUS_TEXT = {
    approved: '已通过',
    rejected: '未通过',
    deleted: '已删除',
  };

  function formatTime(seconds) {
    if (!seconds) return '';
    try {
      return new Date(seconds * 1000).toLocaleString('zh-CN', { hour12: false });
    } catch (_) {
      return '';
    }
  }

  function showAuthGate(message) {
    authCard.hidden = false;
    accountBar.hidden = true;
    list.hidden = true;
    empty.hidden = true;
    statusLine.hidden = true;
    if (message) {
      loginBanner.hidden = false;
      loginBanner.textContent = message;
    }
  }

  function copyText(text, button) {
    const write = async () => {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return;
      }
      const helper = document.createElement('textarea');
      helper.value = text;
      helper.style.position = 'fixed';
      helper.style.top = '-9999px';
      document.body.appendChild(helper);
      helper.select();
      document.execCommand('copy');
      helper.remove();
    };
    write()
      .then(() => {
        button.textContent = '已复制';
        liveRegion.textContent = `已复制音色名 ${text}`;
        window.setTimeout(() => {
          button.textContent = '复制';
        }, 1600);
      })
      .catch(() => {
        button.textContent = '复制失败';
      });
  }

  function renderRow(item) {
    const row = document.createElement('li');
    row.className = 'voice-row';

    const main = document.createElement('div');
    main.className = 'voice-main';

    const title = document.createElement('p');
    title.className = 'voice-label';
    title.textContent = item.label || item.name || '';
    main.appendChild(title);

    const meta = document.createElement('p');
    meta.className = 'voice-meta';
    const badge = document.createElement('span');
    const status = String(item.status || '');
    badge.className = `badge badge-${status}`;
    badge.textContent = STATUS_TEXT[status] || status;
    meta.appendChild(badge);
    const created = formatTime(item.created_at);
    if (created) {
      const time = document.createElement('span');
      time.textContent = created;
      meta.appendChild(time);
    }
    main.appendChild(meta);

    if (item.status === 'approved' && item.name) {
      const nameRow = document.createElement('p');
      nameRow.className = 'result-name-row';
      const code = document.createElement('code');
      code.className = 'slug';
      code.textContent = item.name;
      nameRow.appendChild(code);
      const copy = document.createElement('button');
      copy.className = 'copy';
      copy.type = 'button';
      copy.textContent = '复制';
      copy.addEventListener('click', () => copyText(item.name, copy));
      nameRow.appendChild(copy);
      main.appendChild(nameRow);
      const usage = document.createElement('p');
      usage.className = 'voice-usage';
      usage.textContent = '用法：聊天里发「/点歌 歌名 音色名」。';
      main.appendChild(usage);
    } else if (item.status === 'rejected' && item.reason) {
      const reason = document.createElement('p');
      reason.className = 'voice-reason';
      reason.textContent = `原因：${item.reason}`;
      main.appendChild(reason);
    }

    row.appendChild(main);

    const actions = document.createElement('div');
    actions.className = 'voice-actions';
    const remove = document.createElement('button');
    remove.className = 'link-button danger';
    remove.type = 'button';
    remove.textContent = '删除';
    remove.addEventListener('click', () => removeItem(item, remove));
    actions.appendChild(remove);
    row.appendChild(actions);

    return row;
  }

  async function removeItem(item, button) {
    const name = item.name || item.label || '';
    const confirmed = window.confirm(
      `确定删除「${name}」吗？删除后该音色名将无法再用于点歌，已上传的模型文件也会一并删除。`
    );
    if (!confirmed) return;
    button.disabled = true;
    try {
      const response = await fetch(`/v1/voice-requests/${encodeURIComponent(item.id)}`, {
        method: 'DELETE',
      });
      if (response.status === 401) {
        showAuthGate('登录已过期，请重新登录。');
        return;
      }
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      liveRegion.textContent = `已删除音色 ${name}`;
      await loadMine();
    } catch (error) {
      console.error('删除失败：', error);
      button.disabled = false;
      loginBanner.hidden = false;
      loginBanner.textContent = '删除失败，请稍后重试。';
    }
  }

  function render(submissions, me) {
    accountBar.hidden = false;
    accountName.textContent = me.name || me.username;
    list.replaceChildren();
    if (!submissions.length) {
      empty.hidden = false;
      list.hidden = true;
      return;
    }
    empty.hidden = true;
    list.hidden = false;
    const fragment = document.createDocumentFragment();
    for (const item of submissions) {
      fragment.appendChild(renderRow(item));
    }
    list.appendChild(fragment);
  }

  async function loadMine() {
    statusLine.hidden = false;
    statusLine.textContent = '正在加载…';
    try {
      const meResponse = await fetch('/v1/auth/me', { cache: 'no-store' });
      if (meResponse.status === 401) {
        showAuthGate('请先登录后再查看你的音色。');
        return;
      }
      const me = await meResponse.json();
      const response = await fetch('/v1/voice-requests/mine', { cache: 'no-store' });
      if (response.status === 401) {
        showAuthGate('登录已过期，请重新登录。');
        return;
      }
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      statusLine.hidden = true;
      render(Array.isArray(payload.submissions) ? payload.submissions : [], me);
    } catch (error) {
      console.error('加载我的音色失败：', error);
      statusLine.hidden = false;
      statusLine.textContent = '加载失败，请稍后刷新重试。';
    }
  }

  logoutButton.addEventListener('click', async () => {
    try {
      await fetch('/v1/auth/logout', { method: 'POST' });
    } catch (_) {
      // ignore; the gate shows either way
    }
    showAuthGate();
  });

  const params = new URLSearchParams(window.location.search);
  if (params.get('login') === 'failed') {
    const reason = params.get('reason');
    loginBanner.hidden = false;
    loginBanner.textContent = reason ? `登录失败：${reason}` : '登录失败，请重试。';
  }

  loadMine();
})();
