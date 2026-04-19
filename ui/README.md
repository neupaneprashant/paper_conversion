# UI Scaffold

This is a clean static UI mock for the Academic Paper Converter.

Files:
- `index.html`
- `styles.css`

## Purpose

This UI is a frontend scaffold inspired by the provided reference image. It is not yet wired to a backend.

## Planned next step

Connect the UI to a local API that can:
- upload input projects
- create jobs
- stream job status
- download converted source, PDF, and reports

## Suggested backend routes
- `POST /api/jobs`
- `GET /api/jobs/:job_id`
- `GET /api/jobs/:job_id/artifacts`
