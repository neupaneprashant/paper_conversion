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

async function uploadFile(file, sourceFormat, targetFormat) {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('source_format', sourceFormat);
  formData.append('target_format', targetFormat);

  const res = await fetch('/api/jobs', {
    method: 'POST',
    body: formData
  });

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || `Upload failed (${res.status})`);
  }
  return data;
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
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
    <div class="status-card">
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
  const inputName = job.input_path ? job.input_path.split(/[\\/]/).pop() : 'unknown';
  return `
    <div class="status-card">
      <h4>${escapeHtml(job.job_id || 'Unknown job')}</h4>
      <p>Status: <strong>${escapeHtml(job.status || 'unknown')}</strong></p>
      <p>Stage: ${escapeHtml(stage)}</p>
      <p>Direction: ${direction}</p>
      <p>Input: ${escapeHtml(inputName)}</p>
      <p>Timeout: ${escapeHtml(job.timeout_seconds || 'n/a')}s</p>
      ${workerInfo}
      ${failureKind}
      ${errorMessage}
      <p><a href="/job.html?job=${encodeURIComponent(job.job_id)}">Open job detail →</a></p>
    </div>
  `;
}

function renderJobs(jobs) {
  const root = document.getElementById('jobs');
  if (!root) return;
  const validJobs = jobs.filter((job) => job && job.job_id);
  if (!validJobs.length) {
    root.innerHTML = '<div class="status-card"><h4>No jobs yet</h4><p>Upload a file and submit to see conversion status here.</p></div>';
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
  const fileInput = document.getElementById('file-input');
  if (!form || !fileInput) return;

  // Allow drag-and-drop onto the upload card
  const uploadCard = document.querySelector('.upload-card');
  if (uploadCard) {
    uploadCard.addEventListener('dragover', (e) => {
      e.preventDefault();
      uploadCard.style.borderColor = 'var(--rust)';
      uploadCard.style.backgroundColor = 'var(--cream-deep)';
    });
    uploadCard.addEventListener('dragleave', () => {
      uploadCard.style.borderColor = '';
      uploadCard.style.backgroundColor = '';
    });
    uploadCard.addEventListener('drop', (e) => {
      e.preventDefault();
      uploadCard.style.borderColor = '';
      uploadCard.style.backgroundColor = '';

      // Handle dropped files
      if (e.dataTransfer.files.length > 0) {
        fileInput.files = e.dataTransfer.files;
      }
    });
  }

  // Form submission
  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const file = fileInput.files[0];
    const selected = document.querySelector('input[name="direction"]:checked');

    if (!file) {
      alert('Please select a file to upload.');
      return;
    }

    if (!selected) {
      alert('Please select a conversion direction.');
      return;
    }

    const [source_format, target_format] = selected.value.split(':');

    try {
      renderEngineStatus('Uploading file...', 'info');
      const job = await uploadFile(file, source_format, target_format);
      renderEngineStatus('File uploaded successfully. Processing conversion...', 'ok');
      fileInput.value = '';
      await refreshDashboard();
    } catch (err) {
      console.error(err);
      renderEngineStatus(`Upload failed: ${err.message}`, 'error');
      alert(`Upload failed: ${err.message}`);
    }
  });
}

window.addEventListener('DOMContentLoaded', async () => {
  bindDirectionCards();
  bindForm();
  await refreshDashboard();
  window.setInterval(refreshDashboard, 3000);
});
