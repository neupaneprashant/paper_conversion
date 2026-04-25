function q(name) {
  return new URLSearchParams(window.location.search).get(name);
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function fetchJson(url, fallbackMessage) {
  const res = await fetch(url);
  let data = null;
  try {
    data = await res.json();
  } catch (err) {
    data = null;
  }
  if (!res.ok) {
    throw new Error(data?.error || `${fallbackMessage} (${res.status})`);
  }
  return data;
}

async function fetchHealth() {
  return await fetchJson('/api/health', 'Failed to fetch engine health');
}

async function fetchJob(jobId) {
  return await fetchJson(`/api/jobs/${jobId}`, 'Failed to fetch job');
}

function renderStatusBanner(message, kind = 'info') {
  const root = document.getElementById('job-page-status');
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

function renderTimeline(timeline = []) {
  return `<div class="timeline">${timeline.map((item) => `
    <div class="timeline-item ${escapeHtml(item.status)}">
      <div class="timeline-agent">${escapeHtml(item.agent)}</div>
      <div class="timeline-meta">Route: ${escapeHtml(item.active_for)}</div>
      <div class="timeline-status">Status: ${escapeHtml(item.status)}</div>
    </div>`).join('')}</div>`;
}

function renderReports(job) {
  const result = job.result || {};
  const reports = result.reports || {};
  return `
    <section class="card detail-card">
      <div class="section-head"><h3>Reports</h3><span>Structured outputs</span></div>
      <pre>${escapeHtml(JSON.stringify(reports, null, 2))}</pre>
    </section>
  `;
}

function renderDownloads(job) {
  const id = encodeURIComponent(job.job_id);
  const result = job.result || {};
  return `
    <section class="card detail-card">
      <div class="section-head"><h3>Downloads</h3><span>Artifacts</span></div>
      <div class="download-row">
        <a class="download-btn" href="/api/jobs/${id}/download">Download ZIP Bundle</a>
        <a class="download-btn" href="/api/jobs/${id}/artifact/main.tex">Download main.tex</a>
      </div>
      <p>Final PDF path: ${escapeHtml(result.final_pdf_path || 'Not available yet')}</p>
    </section>
  `;
}

function renderFailureDetails(job) {
  if (!job.error && !job.failure_kind) return '';
  return `
    <section class="card detail-card">
      <div class="section-head"><h3>Failure Details</h3><span>Debuggable state</span></div>
      <p><strong>Failure kind:</strong> ${escapeHtml(job.failure_kind || 'n/a')}</p>
      <p><strong>Stage:</strong> ${escapeHtml(job.error?.stage || job.stage || 'unknown')}</p>
      <p><strong>Message:</strong> ${escapeHtml(job.error?.message || 'No error message recorded.')}</p>
    </section>
  `;
}

function renderJob(job) {
  const root = document.getElementById('job-detail-root');
  const result = job.result || {};
  const validation = result.validation || {};
  root.innerHTML = `
    <section id="job-page-status" class="status-banner" hidden></section>

    <section class="card detail-card">
      <div class="section-head"><h3>${escapeHtml(job.job_id)}</h3><span>Status: ${escapeHtml(job.status || 'unknown')}</span></div>
      <p><strong>Direction:</strong> ${escapeHtml((job.source_format || '?').toUpperCase())} &rarr; ${escapeHtml((job.target_format || '?').toUpperCase())}</p>
      <p><strong>Input:</strong> ${escapeHtml(job.input_path || 'n/a')}</p>
      <p><strong>Stage:</strong> ${escapeHtml(job.stage || 'unknown')}</p>
      <p><strong>Worker PID:</strong> ${escapeHtml(job.worker_pid || 'n/a')}</p>
      <p><strong>Timeout:</strong> ${escapeHtml(job.timeout_seconds || 'n/a')} seconds</p>
    </section>

    <section class="card detail-card">
      <div class="section-head"><h3>Agent Timeline</h3><span>April / Friday / Comp</span></div>
      ${renderTimeline(job.timeline || [])}
    </section>

    <section class="card detail-card">
      <div class="section-head"><h3>Validation</h3><span>Quality gate</span></div>
      <pre>${escapeHtml(JSON.stringify(validation, null, 2))}</pre>
    </section>

    ${renderFailureDetails(job)}
    ${renderDownloads(job)}
    ${renderReports(job)}
  `;
}

function renderLoadError(message) {
  const root = document.getElementById('job-detail-root');
  root.innerHTML = `
    <section id="job-page-status" class="status-banner status-error">
      Engine unreachable: ${escapeHtml(message)}
    </section>
    <section class="card detail-card">
      <div class="section-head"><h3>Job Detail</h3><span>Unavailable</span></div>
      <p>${escapeHtml(message)}</p>
    </section>
  `;
}

window.addEventListener('DOMContentLoaded', async () => {
  const jobId = q('job');
  if (!jobId) return;

  const refresh = async () => {
    try {
      const health = await fetchHealth();
      const job = await fetchJob(jobId);
      renderJob(job);
      const count = Number(health.active_worker_count || 0);
      const message = count > 0
        ? `Engine online. ${count} worker${count === 1 ? '' : 's'} active.`
        : 'Engine online and ready.';
      renderStatusBanner(message, 'ok');
    } catch (err) {
      console.error(err);
      renderLoadError(err.message);
    }
  };

  await refresh();
  window.setInterval(refresh, 3000);
});
