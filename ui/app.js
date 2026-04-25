async function fetchJson(url, fallbackMessage) {
  const res = await fetch(url);
  let data = null;
  try {
    data = await res.json();
  } catch (err) {
    data = null;
  }
  if (!res.ok) {
    const message = data?.error || `${fallbackMessage} (${res.status})`;
    throw new Error(message);
  }
  return data;
}

async function fetchHealth() {
  return await fetchJson('/api/health', 'Failed to fetch engine health');
}

async function fetchJobs() {
  const data = await fetchJson('/api/jobs', 'Failed to fetch jobs');
  return Array.isArray(data) ? data : [];
}

async function createJob(payload) {
  const res = await fetch('/api/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || `Job creation failed (${res.status})`);
  }
  return data;
}

async function browsePath(path) {
  return await fetchJson(`/api/browse?path=${encodeURIComponent(path)}`, 'Browse failed');
}

const BROWSE_STORAGE_KEY = 'paper-conversion:last-browse-path';
let currentBrowsePath = '';
let currentBrowseData = null;

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function rememberBrowsePath(path) {
  if (!path) return;
  try {
    window.localStorage.setItem(BROWSE_STORAGE_KEY, path);
  } catch (err) {
    console.warn('Could not store browse path', err);
  }
}

function getRememberedBrowsePath() {
  try {
    return window.localStorage.getItem(BROWSE_STORAGE_KEY) || '';
  } catch (err) {
    return '';
  }
}

function buildBreadcrumbParts(path) {
  if (!path) return [];

  const windowsMatch = String(path).match(/^([A-Za-z]:)([\\/].*)?$/);
  if (windowsMatch) {
    const drive = windowsMatch[1];
    const tail = (windowsMatch[2] || '').split(/[\\/]/).filter(Boolean);
    const parts = [{ label: drive, path: `${drive}\\` }];
    let current = `${drive}\\`;
    tail.forEach((segment) => {
      current = current.endsWith('\\') ? `${current}${segment}` : `${current}\\${segment}`;
      parts.push({ label: segment, path: current });
    });
    return parts;
  }

  const segments = String(path).split('/').filter(Boolean);
  const parts = [{ label: '/', path: '/' }];
  let current = '';
  segments.forEach((segment) => {
    current = `${current}/${segment}`;
    parts.push({ label: segment, path: current });
  });
  return parts;
}

function renderBreadcrumb(path) {
  const breadcrumb = document.getElementById('breadcrumb');
  if (!breadcrumb) return;
  const parts = buildBreadcrumbParts(path);
  breadcrumb.innerHTML = parts.map((part, idx) => {
    const separator = idx < parts.length - 1 ? '<span class="breadcrumb-sep">/</span>' : '';
    return `<span class="breadcrumb-item" data-path="${escapeHtml(part.path)}">${escapeHtml(part.label)}</span>${separator}`;
  }).join('');
  breadcrumb.querySelectorAll('[data-path]').forEach((el) => {
    el.addEventListener('click', async () => {
      currentBrowsePath = el.dataset.path;
      await renderFilePicker();
    });
  });
}

function renderShortcuts(shortcuts) {
  const root = document.getElementById('picker-shortcuts');
  if (!root) return;
  const items = Array.isArray(shortcuts) ? shortcuts : [];
  root.innerHTML = items.map((item) =>
    `<button type="button" class="shortcut-chip" data-path="${escapeHtml(item.path)}">${escapeHtml(item.label)}</button>`
  ).join('');
  root.querySelectorAll('[data-path]').forEach((el) => {
    el.addEventListener('click', async () => {
      currentBrowsePath = el.dataset.path;
      await renderFilePicker();
    });
  });
}

function renderPickerLocation(data) {
  const location = document.getElementById('picker-location');
  const upBtn = document.getElementById('browse-up');
  if (location) {
    location.textContent = data?.current_path || '';
    location.title = data?.current_path || '';
  }
  if (upBtn) {
    upBtn.disabled = !data?.parent_path;
  }
}

function getSelectedPickerItem() {
  return document.querySelector('#file-list .file-item.selected');
}

function chooseInitialBrowsePath() {
  const inputPath = document.getElementById('input-path');
  const typedPath = inputPath?.value.trim() || '';
  return typedPath || getRememberedBrowsePath() || '';
}

function renderFileItems(items) {
  const fileList = document.getElementById('file-list');
  if (!fileList) return;

  const sorted = [...items].sort((a, b) => {
    if (a.is_dir !== b.is_dir) return b.is_dir - a.is_dir;
    if (!a.is_dir && !b.is_dir) {
      const aIsPdf = a.name.toLowerCase().endsWith('.pdf');
      const bIsPdf = b.name.toLowerCase().endsWith('.pdf');
      if (aIsPdf !== bIsPdf) return bIsPdf - aIsPdf;
    }
    return a.name.localeCompare(b.name);
  });

  if (!sorted.length) {
    fileList.innerHTML = '<div style="padding:16px;color:var(--muted);text-align:center;">Empty directory</div>';
    return;
  }

  fileList.innerHTML = sorted.map((item) => {
    const isPdf = !item.is_dir && item.name.toLowerCase().endsWith('.pdf');
    const badge = item.is_dir ? 'Folder' : isPdf ? 'PDF' : (item.extension || 'File').replace('.', '').toUpperCase();
    const icon = item.is_dir ? 'DIR' : isPdf ? 'PDF' : 'FILE';
    return `
      <div class="file-item ${item.is_dir ? 'is-dir' : 'is-file'} ${isPdf ? 'is-pdf' : ''}" data-path="${escapeHtml(item.path)}" data-is-dir="${item.is_dir}">
        <span class="file-item-icon">${icon}</span>
        <div style="flex:1;">
          <div class="file-item-name">${escapeHtml(item.name)}</div>
          <div class="file-item-path">${escapeHtml(item.path)}</div>
        </div>
        <span class="picker-badge">${escapeHtml(badge)}</span>
      </div>
    `;
  }).join('');

  fileList.querySelectorAll('.file-item').forEach((el) => {
    el.addEventListener('click', () => {
      fileList.querySelectorAll('.file-item').forEach((node) => node.classList.remove('selected'));
      el.classList.add('selected');
    });

    el.addEventListener('dblclick', async () => {
      if (el.dataset.isDir === 'true') {
        currentBrowsePath = el.dataset.path;
        await renderFilePicker();
      } else {
        document.getElementById('input-path').value = el.dataset.path;
        hideFilePicker();
      }
    });
  });
}

async function renderFilePicker() {
  const fileList = document.getElementById('file-list');
  if (!fileList) return;

  try {
    const data = await browsePath(currentBrowsePath);
    currentBrowseData = data;
    currentBrowsePath = data.current_path;
    rememberBrowsePath(currentBrowsePath);
    renderShortcuts(data.shortcuts);
    renderPickerLocation(data);
    renderBreadcrumb(currentBrowsePath);
    renderFileItems(Array.isArray(data.items) ? data.items : []);
  } catch (err) {
    fileList.innerHTML = `<div style="color:red;padding:16px;">${escapeHtml(err.message)}</div>`;
  }
}

function showFilePicker() {
  const modal = document.getElementById('file-picker-modal');
  if (!modal) return;
  currentBrowsePath = chooseInitialBrowsePath();
  currentBrowseData = null;
  modal.style.display = 'flex';
  renderFilePicker();
}

function hideFilePicker() {
  const modal = document.getElementById('file-picker-modal');
  if (modal) {
    modal.style.display = 'none';
  }
}

function bindFilePicker() {
  const browseBtn = document.getElementById('browse-btn');
  const closeBtn = document.getElementById('close-picker');
  const cancelBtn = document.getElementById('cancel-picker');
  const selectItemBtn = document.getElementById('select-item');
  const selectCurrentBtn = document.getElementById('select-current');
  const upBtn = document.getElementById('browse-up');
  const inputPath = document.getElementById('input-path');

  if (browseBtn) browseBtn.addEventListener('click', (e) => {
    e.preventDefault();
    showFilePicker();
  });

  if (closeBtn) closeBtn.addEventListener('click', hideFilePicker);
  if (cancelBtn) cancelBtn.addEventListener('click', hideFilePicker);

  if (selectItemBtn) selectItemBtn.addEventListener('click', () => {
    const selected = getSelectedPickerItem();
    if (selected) {
      inputPath.value = selected.dataset.path;
      hideFilePicker();
      return;
    }
    inputPath.value = currentBrowsePath;
    hideFilePicker();
  });

  if (selectCurrentBtn) selectCurrentBtn.addEventListener('click', () => {
    inputPath.value = currentBrowsePath;
    hideFilePicker();
  });

  if (upBtn) upBtn.addEventListener('click', async () => {
    if (!currentBrowseData?.parent_path) return;
    currentBrowsePath = currentBrowseData.parent_path;
    await renderFilePicker();
  });

  const modal = document.getElementById('file-picker-modal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target.id === 'file-picker-modal') hideFilePicker();
    });
  }
}

function setDirection(value) {
  if (!value) return;
  const radio = document.querySelector(`input[name="direction"][value="${value}"]`);
  if (radio) radio.checked = true;
  document.querySelectorAll('.direction-grid .direction').forEach((btn) => {
    btn.classList.toggle('selected', btn.dataset.direction === value);
  });
}

function bindDirectionCards() {
  document.querySelectorAll('.direction-grid .direction').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      setDirection(btn.dataset.direction);
    });
  });
  document.querySelectorAll('input[name="direction"]').forEach((radio) => {
    radio.addEventListener('change', () => {
      if (radio.checked) setDirection(radio.value);
    });
  });
  const checked = document.querySelector('input[name="direction"]:checked');
  if (checked) setDirection(checked.value);
}

function bindSampleButtons() {
  document.querySelectorAll('.sample-btn[data-sample-path]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      const path = btn.dataset.samplePath || '';
      const direction = btn.dataset.sampleDirection;
      const input = document.getElementById('input-path');
      if (input) input.value = path;
      if (direction) setDirection(direction);
    });
  });
}

function renderEngineStatus(message, kind = 'info') {
  const root = document.getElementById('engine-status');
  if (!root) return;
  if (!message) {
    root.hidden = true;
    root.className = 'status-banner';
    root.textContent = '';
    return;
  }
  root.hidden = false;
  root.className = `status-banner status-${kind}`;
  root.textContent = message;
}

function renderJobsUnavailable(message) {
  const root = document.getElementById('jobs');
  if (!root) return;
  root.innerHTML = `
    <div class="card status-card">
      <h4>Engine Unreachable</h4>
      <p>${escapeHtml(message)}</p>
    </div>
  `;
}

function renderJobSummary(job) {
  const direction = `${(job.source_format || '?').toUpperCase()} &rarr; ${(job.target_format || '?').toUpperCase()}`;
  const stage = job.stage || 'unknown';
  const errorMessage = job.error?.message ? `<p class="job-error">Error: ${escapeHtml(job.error.message)}</p>` : '';
  const failureKind = job.failure_kind ? `<p>Failure: ${escapeHtml(job.failure_kind)}</p>` : '';
  const workerInfo = job.worker_pid ? `<p>Worker PID: ${job.worker_pid}</p>` : '';
  return `
    <div class="card status-card">
      <h4>${escapeHtml(job.job_id || 'Unknown job')}</h4>
      <p>Status: <strong>${escapeHtml(job.status || 'unknown')}</strong></p>
      <p>Stage: ${escapeHtml(stage)}</p>
      <p>Direction: ${direction}</p>
      <p>Input: ${escapeHtml(job.input_path || 'n/a')}</p>
      <p>Timeout: ${escapeHtml(job.timeout_seconds || 'n/a')}s</p>
      ${workerInfo}
      ${failureKind}
      ${errorMessage}
      <p><a href="/job.html?job=${encodeURIComponent(job.job_id)}">Open job detail</a></p>
    </div>
  `;
}

function renderJobs(jobs) {
  const root = document.getElementById('jobs');
  if (!root) return;
  const validJobs = jobs.filter((job) => job && job.job_id);
  if (!validJobs.length) {
    root.innerHTML = '<div class="card status-card"><h4>No jobs yet</h4><p>Submit a conversion to see status here.</p></div>';
    return;
  }
  root.innerHTML = validJobs.map(renderJobSummary).join('');
}

async function refreshDashboard() {
  try {
    const health = await fetchHealth();
    const jobs = await fetchJobs();
    const count = Number(health.active_worker_count || 0);
    const onlineMessage = count > 0
      ? `Engine online. ${count} worker${count === 1 ? '' : 's'} active.`
      : 'Engine online and ready.';
    renderEngineStatus(onlineMessage, 'ok');
    renderJobs(jobs);
  } catch (err) {
    console.error(err);
    renderEngineStatus(`Engine unreachable: ${err.message}`, 'error');
    renderJobsUnavailable(err.message);
  }
}

function bindForm() {
  const form = document.getElementById('job-form');
  const inputPath = document.getElementById('input-path');
  if (!form || !inputPath) return;

  const uploadCard = document.querySelector('.upload-card');
  if (uploadCard) {
    uploadCard.addEventListener('dragover', (e) => {
      e.preventDefault();
      uploadCard.style.borderColor = '#007bff';
      uploadCard.style.backgroundColor = '#f0f8ff';
    });
    uploadCard.addEventListener('dragleave', () => {
      uploadCard.style.borderColor = '';
      uploadCard.style.backgroundColor = '';
    });
    uploadCard.addEventListener('drop', (e) => {
      e.preventDefault();
      uploadCard.style.borderColor = '';
      uploadCard.style.backgroundColor = '';
      showFilePicker();
    });
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const path = inputPath.value.trim();
    const selected = document.querySelector('input[name="direction"]:checked');
    if (!path || !selected) {
      alert('Provide an input folder/file path and conversion direction.');
      return;
    }
    const [source_format, target_format] = selected.value.split(':');
    try {
      await createJob({ input_path: path, source_format, target_format });
      inputPath.value = '';
      renderEngineStatus('Job created successfully. Polling for status updates.', 'ok');
      await refreshDashboard();
    } catch (err) {
      console.error(err);
      renderEngineStatus(`Conversion could not be started: ${err.message}`, 'error');
      alert(`Conversion could not be started: ${err.message}`);
    }
  });
}

window.addEventListener('DOMContentLoaded', async () => {
  bindFilePicker();
  bindDirectionCards();
  bindSampleButtons();
  bindForm();
  await refreshDashboard();
  window.setInterval(refreshDashboard, 3000);
});
