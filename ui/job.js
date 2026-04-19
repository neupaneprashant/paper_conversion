function q(name) {
  return new URLSearchParams(window.location.search).get(name);
}

async function fetchJob(jobId) {
  const res = await fetch(`/api/jobs/${jobId}`);
  return await res.json();
}

function renderTimeline(timeline = []) {
  return `<div class="timeline">${timeline.map(item => `
    <div class="timeline-item ${item.status}">
      <div class="timeline-agent">${item.agent}</div>
      <div class="timeline-meta">Route: ${item.active_for}</div>
      <div class="timeline-status">Status: ${item.status}</div>
    </div>`).join('')}</div>`;
}

function renderReports(job) {
  const result = job.result || {};
  const reports = result.reports || {};
  return `
    <section class="card detail-card">
      <div class="section-head"><h3>Reports</h3><span>Structured outputs</span></div>
      <pre>${JSON.stringify(reports, null, 2)}</pre>
    </section>
  `;
}

function renderDownloads(job) {
  const id = job.job_id;
  const result = job.result || {};
  return `
    <section class="card detail-card">
      <div class="section-head"><h3>Downloads</h3><span>Artifacts</span></div>
      <div class="download-row">
        <a class="download-btn" href="/api/jobs/${id}/download">Download ZIP Bundle</a>
        <a class="download-btn" href="/api/jobs/${id}/artifact/main.tex">Download main.tex</a>
      </div>
      <p>Final PDF path: ${result.final_pdf_path || 'Not available yet'}</p>
    </section>
  `;
}

function renderJob(job) {
  const root = document.getElementById('job-detail-root');
  const result = job.result || {};
  const validation = result.validation || {};
  root.innerHTML = `
    <section class="card detail-card">
      <div class="section-head"><h3>${job.job_id}</h3><span>Status: ${job.status}</span></div>
      <p><strong>Direction:</strong> ${(job.source_format || '?').toUpperCase()} → ${(job.target_format || '?').toUpperCase()}</p>
      <p><strong>Input:</strong> ${job.input_path || 'n/a'}</p>
    </section>

    <section class="card detail-card">
      <div class="section-head"><h3>Agent Timeline</h3><span>April / Friday / Comp</span></div>
      ${renderTimeline(job.timeline || [])}
    </section>

    <section class="card detail-card">
      <div class="section-head"><h3>Validation</h3><span>Quality gate</span></div>
      <pre>${JSON.stringify(validation, null, 2)}</pre>
    </section>

    ${renderDownloads(job)}
    ${renderReports(job)}
  `;
}

window.addEventListener('DOMContentLoaded', async () => {
  const jobId = q('job');
  if (!jobId) return;
  const refresh = async () => {
    const job = await fetchJob(jobId);
    renderJob(job);
  };
  await refresh();
  setInterval(refresh, 3000);
});
