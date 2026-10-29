(() => {
  'use strict';

  const MAX_BYTES = 300 * 1024 * 1024;
  const form = document.getElementById('upload-form');
  const labelInput = document.getElementById('label');
  const userInput = document.getElementById('flowbee-user');
  const fileField = document.getElementById('file-field');
  const urlField = document.getElementById('url-field');
  const fileInput = document.getElementById('file');
  const urlInput = document.getElementById('url');
  const submitButton = document.getElementById('submit-button');
  const formStatus = document.getElementById('form-status');
  const progress = document.getElementById('progress');
  const progressBar = document.getElementById('progress-bar');
  const resultBox = document.getElementById('result-box');
  const resultTitle = document.getElementById('result-title');
  const resultReason = document.getElementById('result-reason');
  const resultNameRow = document.getElementById('result-name-row');
  const resultName = document.getElementById('result-name');
  const resultUsage = document.getElementById('result-usage');
  const copyName = document.getElementById('copy-name');
  const statusLine = document.getElementById('status-line');
  const liveRegion = document.getElementById('live');

  let busy = false;

  function currentMode() {
    const checked = form.querySelector('input[name="mode"]:checked');
    return checked ? checked.value : 'file';
  }

  function refreshMode() {
    const mode = currentMode();
    fileField.hidden = mode !== 'file';
    urlField.hidden = mode !== 'url';
  }

  form.querySelectorAll('input[name="mode"]').forEach((radio) => {
    radio.addEventListener('change', refreshMode);
  });

  function setStatus(text, isError) {
    formStatus.textContent = text;
    formStatus.classList.toggle('is-error', Boolean(isError));
  }

  function resetResult() {
    resultBox.hidden = true;
    resultTitle.textContent = '';
    resultReason.textContent = '';
    resultNameRow.hidden = true;
    resultUsage.hidden = true;
  }

  function showResult(payload) {
    resetResult();
    const approved = payload.status === 'approved';
    resultTitle.textContent = approved ? '审核通过，音色已可用 🎉' : '未通过审核';
    resultTitle.classList.toggle('is-rejected', !approved);
    resultReason.textContent = payload.reason || '';
    if (approved && payload.name) {
      resultName.textContent = payload.name;
      resultNameRow.hidden = false;
      resultUsage.hidden = false;
      liveRegion.textContent = `审核通过，音色名 ${payload.name}`;
    } else {
      liveRegion.textContent = '提交未通过审核';
    }
    resultBox.hidden = false;
  }

  function validate() {
    const label = labelInput.value.trim();
    const user = userInput.value.trim();
    if (!label) {
      setStatus('请填写音色显示名', true);
      labelInput.focus();
      return null;
    }
    if (!/^[A-Za-z0-9_.@-]{1,64}$/.test(user)) {
      setStatus('请填写有效的 FlowBee 用户名（字母/数字/_.@-）', true);
      userInput.focus();
      return null;
    }
    const mode = currentMode();
    if (mode === 'file') {
      const file = fileInput.files && fileInput.files[0];
      if (!file) {
        setStatus('请选择要上传的 zip 文件', true);
        return null;
      }
      if (!/\.zip$/i.test(file.name)) {
        setStatus('仅支持 .zip 文件', true);
        return null;
      }
      if (file.size > MAX_BYTES) {
        setStatus('文件超过 300MB 上限', true);
        return null;
      }
    } else if (!/^https?:\/\//i.test(urlInput.value.trim())) {
      setStatus('请填写 http/https 直链', true);
      urlInput.focus();
      return null;
    }
    setStatus('');
    return { label, user, mode };
  }

  function submit() {
    if (busy) return;
    const data = validate();
    if (!data) return;
    busy = true;
    submitButton.disabled = true;
    progress.hidden = false;
    progressBar.style.width = '0%';
    setStatus('正在上传并审核…');
    resetResult();

    const body = new FormData();
    body.append('label', data.label);
    body.append('flowbee_user', data.user);
    if (data.mode === 'file') {
      body.append('file', fileInput.files[0]);
    } else {
      body.append('url', urlInput.value.trim());
    }

    const request = new XMLHttpRequest();
    request.open('POST', '/v1/voice-requests');
    request.responseType = 'json';
    request.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable) {
        const percent = Math.round((event.loaded / event.total) * 100);
        progressBar.style.width = `${percent}%`;
        if (percent >= 100) setStatus('上传完成，正在审核…');
      }
    });
    request.addEventListener('load', () => {
      busy = false;
      submitButton.disabled = false;
      progress.hidden = true;
      const payload = request.response;
      if (request.status >= 200 && request.status < 300 && payload) {
        setStatus('');
        showResult(payload);
      } else {
        const detail = payload && (payload.detail || payload.error);
        setStatus(detail ? String(detail) : `提交失败（HTTP ${request.status}）`, true);
      }
    });
    request.addEventListener('error', () => {
      busy = false;
      submitButton.disabled = false;
      progress.hidden = true;
      setStatus('网络错误，请稍后重试', true);
    });
    request.send(body);
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    submit();
  });

  copyName.addEventListener('click', async () => {
    const text = resultName.textContent || '';
    if (!text) return;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const helper = document.createElement('textarea');
        helper.value = text;
        helper.style.position = 'fixed';
        helper.style.top = '-9999px';
        document.body.appendChild(helper);
        helper.select();
        document.execCommand('copy');
        helper.remove();
      }
      copyName.textContent = '已复制';
      liveRegion.textContent = `已复制音色名 ${text}`;
      window.setTimeout(() => {
        copyName.textContent = '复制';
      }, 1600);
    } catch (_) {
      copyName.textContent = '复制失败';
    }
  });

  async function loadStatus(requestId) {
    statusLine.hidden = false;
    statusLine.textContent = '正在查询提交状态…';
    try {
      const response = await fetch(`/v1/voice-requests/${encodeURIComponent(requestId)}`, {
        cache: 'no-cache',
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      statusLine.hidden = true;
      showResult(payload);
    } catch (error) {
      console.error('查询提交状态失败：', error);
      statusLine.textContent = '没有找到这条提交记录（链接可能不完整或已过期）。';
    }
  }

  refreshMode();
  const params = new URLSearchParams(window.location.search);
  const requestId = params.get('id');
  if (requestId) {
    loadStatus(requestId);
  }
})();
