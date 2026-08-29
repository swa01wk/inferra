from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.services.auth.keys import AuthenticatedContext, require_admin_key
from db.models import Adapter, APIKey, Deployment, Organization, RequestRecord, Worker
from db.session import get_db

router = APIRouter()

_HTML_HEADER = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Inferra Admin</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;padding:24px}
    h1{font-size:1.5rem;margin-bottom:4px;color:#f8fafc}
    .subtitle{color:#94a3b8;font-size:.85rem;margin-bottom:24px}
    nav{display:flex;gap:12px;margin-bottom:32px;border-bottom:1px solid #1e293b;padding-bottom:16px}
    nav a{color:#7dd3fc;text-decoration:none;font-size:.9rem}
    nav a:hover{text-decoration:underline}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:32px}
    .card{background:#1e293b;border-radius:8px;padding:20px;border:1px solid #334155}
    .card .value{font-size:2rem;font-weight:700;color:#38bdf8;margin:8px 0 4px}
    .card .label{font-size:.8rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em}
    section{margin-bottom:32px}
    h2{font-size:1.1rem;margin-bottom:12px;color:#cbd5e1;border-bottom:1px solid #1e293b;padding-bottom:8px}
    table{width:100%;border-collapse:collapse;font-size:.85rem}
    th{text-align:left;padding:8px 12px;background:#1e293b;color:#94a3b8;font-weight:500}
    td{padding:8px 12px;border-top:1px solid #1e293b}
    .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.75rem;font-weight:600}
    .badge-green{background:#14532d;color:#86efac}
    .badge-red{background:#450a0a;color:#fca5a5}
    .badge-yellow{background:#422006;color:#fde68a}
    .badge-gray{background:#1e293b;color:#94a3b8}
  </style>
</head>
<body>
  <h1>Inferra Admin</h1>
  <p class="subtitle">V1 Inference Platform — Internal Dashboard</p>
  <nav>
    <a href="/admin">Overview</a>
    <a href="/admin/orgs">Organizations</a>
    <a href="/admin/adapters">Adapters</a>
    <a href="/admin/workers">Workers</a>
    <a href="/admin/usage">Recent Usage</a>
    <a href="/metrics" target="_blank">Metrics</a>
  </nav>
"""

_HTML_FOOTER = "</body></html>"


def _badge(status: str) -> str:
    cls = {
        "active": "badge-green", "healthy": "badge-green", "running": "badge-green",
        "revoked": "badge-red", "failed": "badge-red", "offline": "badge-red",
        "suspended": "badge-red",
        "downloading": "badge-yellow", "available": "badge-yellow", "loaded": "badge-yellow",
        "degraded": "badge-yellow",
    }.get(status, "badge-gray")
    return f'<span class="badge {cls}">{status}</span>'


@router.get("/admin", response_class=HTMLResponse)
async def admin_overview(
    _auth: AuthenticatedContext = Depends(require_admin_key),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    org_count = (await db.execute(select(func.count()).select_from(Organization))).scalar() or 0
    key_count = (await db.execute(
        select(func.count()).select_from(APIKey).where(APIKey.status == "active")
    )).scalar() or 0
    adapter_count = (await db.execute(
        select(func.count()).select_from(Adapter).where(Adapter.status.in_(["loaded", "active"]))
    )).scalar() or 0
    req_count = (await db.execute(select(func.count()).select_from(RequestRecord))).scalar() or 0
    worker_count = (await db.execute(
        select(func.count()).select_from(Worker).where(Worker.status == "healthy")
    )).scalar() or 0

    body = f"""
  <div class="grid">
    <div class="card"><div class="label">Organizations</div><div class="value">{org_count}</div></div>
    <div class="card"><div class="label">Active API Keys</div><div class="value">{key_count}</div></div>
    <div class="card"><div class="label">Loaded Adapters</div><div class="value">{adapter_count}</div></div>
    <div class="card"><div class="label">Healthy Workers</div><div class="value">{worker_count}</div></div>
    <div class="card"><div class="label">Total Requests</div><div class="value">{req_count}</div></div>
  </div>
"""
    # Recent requests
    recent = (await db.execute(
        select(RequestRecord).order_by(RequestRecord.received_at.desc()).limit(10)
    )).scalars().all()
    rows = "".join(
        f"<tr><td>{str(r.id)[:8]}…</td><td>{r.logical_model}</td><td>{_badge(r.status)}</td>"
        f"<td>{r.received_at.strftime('%H:%M:%S') if r.received_at else '—'}</td></tr>"
        for r in recent
    )
    body += f"""
  <section>
    <h2>Recent Requests</h2>
    <table>
      <tr><th>ID</th><th>Model</th><th>Status</th><th>Received</th></tr>
      {rows or '<tr><td colspan="4" style="color:#64748b">No requests yet</td></tr>'}
    </table>
  </section>
"""
    return HTMLResponse(_HTML_HEADER + body + _HTML_FOOTER)


@router.get("/admin/orgs", response_class=HTMLResponse)
async def admin_orgs(
    _auth: AuthenticatedContext = Depends(require_admin_key),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    orgs = (await db.execute(select(Organization).order_by(Organization.created_at.desc()))).scalars().all()
    rows = "".join(
        f"<tr><td>{o.name}</td><td>{str(o.id)[:8]}…</td><td>{_badge(o.status)}</td>"
        f"<td>{o.created_at.strftime('%Y-%m-%d') if o.created_at else '—'}</td></tr>"
        for o in orgs
    )
    body = f"""
  <section>
    <h2>Organizations</h2>
    <table>
      <tr><th>Name</th><th>ID</th><th>Status</th><th>Created</th></tr>
      {rows or '<tr><td colspan="4" style="color:#64748b">None</td></tr>'}
    </table>
  </section>
"""
    return HTMLResponse(_HTML_HEADER + body + _HTML_FOOTER)


@router.get("/admin/adapters", response_class=HTMLResponse)
async def admin_adapters(
    _auth: AuthenticatedContext = Depends(require_admin_key),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    adapters = (await db.execute(
        select(Adapter).where(Adapter.status != "deleted").order_by(Adapter.created_at.desc())
    )).scalars().all()
    rows = "".join(
        f"<tr><td>{a.name}</td><td>{str(a.id)[:8]}…</td><td>rank {a.rank}</td>"
        f"<td>{_badge(a.status)}</td>"
        f"<td style='color:#ef4444;font-size:.8rem'>{a.error_message or ''}</td></tr>"
        for a in adapters
    )
    body = f"""
  <section>
    <h2>Adapters</h2>
    <table>
      <tr><th>Name</th><th>ID</th><th>Rank</th><th>Status</th><th>Error</th></tr>
      {rows or '<tr><td colspan="5" style="color:#64748b">No adapters registered</td></tr>'}
    </table>
  </section>
"""
    return HTMLResponse(_HTML_HEADER + body + _HTML_FOOTER)


@router.get("/admin/workers", response_class=HTMLResponse)
async def admin_workers(
    _auth: AuthenticatedContext = Depends(require_admin_key),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    workers = (await db.execute(select(Worker))).scalars().all()
    deployments = (await db.execute(select(Deployment))).scalars().all()
    dep_by_worker = {str(d.worker_id): d for d in deployments}

    rows = ""
    for w in workers:
        dep = dep_by_worker.get(str(w.id))
        rows += (
            f"<tr><td>{w.hostname}</td><td>{w.gpu_type}</td>"
            f"<td>{w.gpu_vram_mb // 1024} GB</td><td>{w.endpoint}</td>"
            f"<td>{_badge(w.status)}</td>"
            f"<td>{_badge(dep.status) if dep else '—'}</td></tr>"
        )
    body = f"""
  <section>
    <h2>Workers</h2>
    <table>
      <tr><th>Hostname</th><th>GPU</th><th>VRAM</th><th>Endpoint</th><th>Worker Status</th><th>Deployment</th></tr>
      {rows or '<tr><td colspan="6" style="color:#64748b">No workers registered</td></tr>'}
    </table>
  </section>
"""
    return HTMLResponse(_HTML_HEADER + body + _HTML_FOOTER)


@router.get("/admin/usage", response_class=HTMLResponse)
async def admin_usage(
    _auth: AuthenticatedContext = Depends(require_admin_key),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    recent = (await db.execute(
        select(RequestRecord).order_by(RequestRecord.received_at.desc()).limit(50)
    )).scalars().all()
    rows = "".join(
        f"<tr><td>{str(r.id)[:8]}…</td>"
        f"<td>{str(r.organization_id)[:8]}…</td>"
        f"<td>{r.logical_model}</td>"
        f"<td>{_badge(r.status)}</td>"
        f"<td>{r.received_at.strftime('%Y-%m-%d %H:%M:%S') if r.received_at else '—'}</td>"
        f"</tr>"
        for r in recent
    )
    body = f"""
  <section>
    <h2>Recent Requests (last 50)</h2>
    <table>
      <tr><th>ID</th><th>Org</th><th>Model</th><th>Status</th><th>Received At</th></tr>
      {rows or '<tr><td colspan="5" style="color:#64748b">No requests yet</td></tr>'}
    </table>
  </section>
"""
    return HTMLResponse(_HTML_HEADER + body + _HTML_FOOTER)
