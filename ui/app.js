async function fetchJobs() {
  const res = await fetch('/api/jobs');
  if (!res.ok) {
    throw new Error(`Failed to fetch jobs (${res.status})`);
  }
  const data = await res.json();
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
  const res = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
  if (!res.ok) {
    throw new Error(`Browse failed: ${res.statusText}`);
  }
  return await res.json();
}

let currentBrowsePath = '.';

async function renderFilePicker() {
  const modal = document.getElementById('file-picker-modal');
  const fileList = document.getElementById('file-list');
  const breadcrumb = document.getElementById('breadcrumb');
  
  try {
    const data = await browsePath(currentBrowsePath);
    currentBrowsePath = data.current_path;
    
    // Render breadcrumb
    const parts = currentBrowsePath.split(/[\\/]/);
    breadcrumb.innerHTML = parts.map((part, idx) => {
      if (!part) return '';
      const fullPath = parts.slice(0, idx + 1).join('/');
      return `<span class="breadcrumb-item" data-path="${fullPath}">${part || 'Root'}</span><span class="breadcrumb-sep">/</span>`;
    }).join('') + '<span class="breadcrumb-item">(current)</span>';
    
    // Add breadcrumb click handlers
    breadcrumb.querySelectorAll('[data-path]').forEach(el => {
      el.addEventListener('click', async () => {
        currentBrowsePath = el.dataset.path;
        await renderFilePicker();
      });
    });
    
    // Sort: folders first, then files (PDFs first among files)
    const sorted = [...data.items].sort((a, b) => {
      if (a.is_dir !== b.is_dir) return b.is_dir - a.is_dir;
      if (!a.is_dir && !b.is_dir) {
        // PDF files first, then other files
        const aIsPdf = a.name.toLowerCase().endsWith('.pdf');
        const bIsPdf = b.name.toLowerCase().endsWith('.pdf');
        if (aIsPdf !== bIsPdf) return bIsPdf - aIsPdf;
      }
      return a.name.localeCompare(b.name);
    });
    
    // Render file list (show both folders and files)
    if (sorted.length === 0) {
      fileList.innerHTML = '<div style="padding:16px;color:var(--muted);text-align:center;">Empty directory</div>';
      return;
    }
    
    fileList.innerHTML = sorted.map(item => {
      const isPdf = !item.is_dir && item.name.toLowerCase().endsWith('.pdf');
      return `
      <div class="file-item ${item.is_dir ? 'is-dir' : 'is-file'} ${isPdf ? 'is-pdf' : ''}" data-path="${item.path}" data-is-dir="${item.is_dir}">
        <span class="file-item-icon">${item.is_dir ? '📁' : isPdf ? '📕' : '📄'}</span>
        <div style="flex:1;">
          <div class="file-item-name">${item.name}</div>
          <div class="file-item-path">${item.path}</div>
        </div>
        ${isPdf ? '<span style="font-size:0.75rem;background:rgba(122,168,255,0.2);color:#5d6bdb;padding:2px 6px;border-radius:4px;white-space:nowrap;">PDF</span>' : ''}
      </div>
    `;
    }).join('');
    
    // Add folder click handlers
    fileList.querySelectorAll('.file-item').forEach(el => {
      el.addEventListener('click', async () => {
        // Select item (highlight it)
        fileList.querySelectorAll('.file-item').forEach(e => e.classList.remove('selected'));
        el.classList.add('selected');
      });
      
      // Double-click to navigate into folder OR select file
      el.addEventListener('dblclick', async () => {
        if (el.dataset.isDir === 'true') {
          // Navigate into folder
          currentBrowsePath = el.dataset.path;
          await renderFilePicker();
        } else {
          // Select file
          document.getElementById('input-path').value = el.dataset.path;
          hideFilePicker();
        }
      });
    });
  } catch (err) {
    fileList.innerHTML = `<div style="color:red;padding:16px;">${err.message}</div>`;
  }
}

function showFilePicker() {
  const modal = document.getElementById('file-picker-modal');
  const inputPath = document.getElementById('input-path');
  currentBrowsePath = inputPath.value.trim() || '.';
  modal.style.display = 'flex';
  renderFilePicker();
}

function hideFilePicker() {
  document.getElementById('file-picker-modal').style.display = 'none';
}

function bindFilePicker() {
  const browseBtn = document.getElementById('browse-btn');
  const closeBtn = document.getElementById('close-picker');
  const cancelBtn = document.getElementById('cancel-picker');
  const selectBtn = document.getElementById('select-current');
  const fileList = document.getElementById('file-list');
  const inputPath = document.getElementById('input-path');
  
  if (browseBtn) browseBtn.addEventListener('click', (e) => {
    e.preventDefault();
    showFilePicker();
  });
  
  if (closeBtn) closeBtn.addEventListener('click', hideFilePicker);
  if (cancelBtn) cancelBtn.addEventListener('click', hideFilePicker);
  
  if (selectBtn) selectBtn.addEventListener('click', () => {
    const selected = fileList.querySelector('.file-item.selected');
    if (selected) {
      inputPath.value = selected.dataset.path;
    } else {
      inputPath.value = currentBrowsePath;
    }
    hideFilePicker();
  });
  
  // Close on background click
  document.getElementById('file-picker-modal').addEventListener('click', (e) => {
    if (e.target.id === 'file-picker-modal') hideFilePicker();
  });
}

function setDirection(value) {
  if (!value) return;
  const radio = document.querySelector(`input[name="direction"][value="${value}"]`);
  if (radio) radio.checked = true;
  document.querySelectorAll('.direction-grid .direction').forEach(btn => {
    btn.classList.toggle('selected', btn.dataset.direction === value);
  });
}

function bindDirectionCards() {
  document.querySelectorAll('.direction-grid .direction').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      setDirection(btn.dataset.direction);
    });
  });
  document.querySelectorAll('input[name="direction"]').forEach(radio => {
    radio.addEventListener('change', () => {
      if (radio.checked) setDirection(radio.value);
    });
  });
  const checked = document.querySelector('input[name="direction"]:checked');
  if (checked) setDirection(checked.value);
}

function bindSampleButtons() {
  document.querySelectorAll('.sample-btn[data-sample-path]').forEach(btn => {
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

function renderJobs(jobs) {
  const root = document.getElementById('jobs');
  if (!root) return;
  const validJobs = jobs.filter(job => job && job.job_id);
  if (!validJobs.length) {
    root.innerHTML = '<div class="card status-card"><h4>No jobs yet</h4><p>Submit a conversion to see status here.</p></div>';
    return;
  }
  root.innerHTML = validJobs.map(job => `
    <div class="card status-card">
      <h4>${job.job_id || 'Unknown job'}</h4>
      <p>Status: ${job.status || 'unknown'}</p>
      <p>Direction: ${(job.source_format || '?').toUpperCase()} → ${(job.target_format || '?').toUpperCase()}</p>
      <p>Input: ${job.input_path || 'n/a'}</p>
      <p><a href="/job.html?job=${job.job_id}">Open job detail</a></p>
    </div>
  `).join('');
}

async function refreshJobs() {
  try {
    const jobs = await fetchJobs();
    renderJobs(jobs);
  } catch (err) {
    console.error(err);
  }
}

function bindForm() {
  const form = document.getElementById('job-form');
  const inputPath = document.getElementById('input-path');
  if (!form || !inputPath) return;
  
  // Drag-drop support: open file picker on drop
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
      // Open file picker on drop (browsers don't allow direct file path access)
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
      const result = await createJob({ input_path: path, source_format, target_format });
      console.log(result);
      inputPath.value = '';
      await refreshJobs();
    } catch (err) {
      console.error(err);
      alert(`Conversion could not be started: ${err.message}`);
    }
  });
}


window.addEventListener('DOMContentLoaded', async () => {
  bindFilePicker();
  bindDirectionCards();
  bindSampleButtons();
  bindForm();
  await refreshJobs();
  setInterval(refreshJobs, 3000);
});
