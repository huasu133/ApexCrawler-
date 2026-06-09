"""Dashboard web application with task management and real-time metrics."""
from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from pydantic import BaseModel, validator

from apexcrawler.task_manager import TaskManager

logger = logging.getLogger(__name__)

# ── API Key auth ──
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
_EXPECTED_API_KEY = os.environ.get("APEX_API_KEY", "")

async def _require_api_key(api_key: str = Depends(_API_KEY_HEADER)):
    if not _EXPECTED_API_KEY:
        raise HTTPException(
            503,
            "API key not configured. Set APEX_API_KEY environment variable.",
        )
    if api_key != _EXPECTED_API_KEY:
        raise HTTPException(403, "Invalid API key")

# ── Models ──
class CreateTaskRequest(BaseModel):
    url: str
    engine: str = ""

class AskRequest(BaseModel):
    query: str

# ── Helpers ──
def _task_to_dict(t):
    """Convert a CrawlTask to a serializable dict."""
    created = ""
    if t.created_at:
        created = datetime.fromtimestamp(t.created_at).isoformat()
    return {
        "task_id": t.id,
        "url": t.url,
        "engine": t.engine,
        "status": t.status.value if hasattr(t.status, 'value') else str(t.status),
        "progress": t.progress,
        "created_at": created,
        "result": t.result,
        "error": t.error,
    }

# ── App factory ──
def create_app() -> FastAPI:
    app = FastAPI(title="ApexCrawler Dashboard", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    tm = TaskManager()

    # ── API Routes ──

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "timestamp": time.time()}

    @app.get("/api/metrics")
    async def metrics(_=Depends(_require_api_key)):
        raw = await tm.get_metrics()
        # Ensure all status keys exist for the frontend
        result = {
            "total": raw.get("total", 0),
            "pending": raw.get("pending", 0),
            "running": raw.get("running", 0),
            "paused": raw.get("paused", 0),
            "completed": raw.get("completed", 0),
            "failed": raw.get("failed", 0),
            "cancelled": raw.get("cancelled", 0),
        }
        return result

    @app.get("/api/tasks")
    async def list_tasks(
        limit: int = Query(50, le=200),
        status: str = Query(""),
        _=Depends(_require_api_key),
    ):
        tasks = await tm.list_tasks(limit=limit, status=status or None)
        return [_task_to_dict(t) for t in tasks]

    @app.post("/api/tasks")
    async def create_task(req: CreateTaskRequest, _=Depends(_require_api_key)):
        task = await tm.create_task(url=req.url, engine=req.engine)
        return {
            "task_id": task.id,
            "url": task.url,
            "status": task.status.value if hasattr(task.status, 'value') else str(task.status),
            "message": "Task created",
        }

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str, _=Depends(_require_api_key)):
        task = await tm.get_task(task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        return _task_to_dict(task)

    @app.post("/api/tasks/{task_id}/pause")
    async def pause_task(task_id: str, _=Depends(_require_api_key)):
        ok = await tm.pause_task(task_id)
        return {"success": ok}

    @app.post("/api/tasks/{task_id}/resume")
    async def resume_task(task_id: str, _=Depends(_require_api_key)):
        ok = await tm.resume_task(task_id)
        return {"success": ok}

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str, _=Depends(_require_api_key)):
        ok = await tm.cancel_task(task_id)
        return {"success": ok}

    # SSE endpoint for real-time metrics push
    @app.get("/api/events")
    async def sse_events(_=Depends(_require_api_key)):
        async def event_generator():
            try:
                while True:
                    raw = await tm.get_metrics()
                    payload = {
                        "total": raw.get("total", 0),
                        "pending": raw.get("pending", 0),
                        "running": raw.get("running", 0),
                        "paused": raw.get("paused", 0),
                        "completed": raw.get("completed", 0),
                        "failed": raw.get("failed", 0),
                        "cancelled": raw.get("cancelled", 0),
                    }
                    yield {"event": "metrics", "data": json.dumps(payload)}
                    await asyncio.sleep(3)
            except asyncio.CancelledError:
                logger.debug("SSE client disconnected, cleaning up")
                raise
            except Exception as e:
                logger.warning(f"SSE generator error: {e}")
        return EventSourceResponse(event_generator())

    # ── Frontend HTML ──

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return DASHBOARD_HTML

    return app


# ── Frontend (single-file HTML, dark theme) ──
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ApexCrawler Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,'Segoe UI',sans-serif;background:#0f0f23;color:#e0e0e0;min-height:100vh}
.header{background:#1a1a2e;padding:16px 24px;border-bottom:2px solid #e94560;display:flex;align-items:center;justify-content:space-between}
.header h1{color:#e94560;font-size:20px}.header .sub{color:#666;font-size:12px}
.container{max-width:1200px;margin:0 auto;padding:20px}

/* Metric cards */
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:20px}
.metric-card{background:#1a1a2e;border-radius:10px;padding:16px;text-align:center}
.metric-card .label{color:#888;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.metric-card .value{font-size:28px;font-weight:700;margin-top:6px}
.metric-card .value.green{color:#4ecca3}
.metric-card .value.red{color:#e94560}
.metric-card .value.blue{color:#5dade2}
.metric-card .value.yellow{color:#f5b041}
.metric-card .value.gray{color:#888}

/* Actions bar */
.actions{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}
.actions input[type=text]{flex:1;min-width:200px;padding:10px 14px;background:#1a1a2e;border:1px solid #333;border-radius:8px;color:#e0e0e0;font-size:14px}
.actions input:focus{outline:none;border-color:#e94560}
.actions select{padding:10px 14px;background:#1a1a2e;border:1px solid #333;border-radius:8px;color:#e0e0e0;font-size:14px}
.actions button{padding:10px 20px;background:#e94560;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:600}
.actions button:hover{background:#d63850}

/* Table */
.table-wrap{background:#1a1a2e;border-radius:10px;overflow:hidden}
table{width:100%;border-collapse:collapse}
th{background:#16213e;padding:10px 14px;text-align:left;font-size:12px;color:#888;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid #333}
td{padding:10px 14px;font-size:13px;border-bottom:1px solid #222}
tr:hover{background:#16213e}
tr:last-child td{border-bottom:none}
.status{padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600}
.status.running{background:#1a3a1a;color:#4ecca3}
.status.completed{background:#1a2a1a;color:#2ecc71}
.status.failed{background:#3a1a1a;color:#e94560}
.status.paused{background:#2a2a1a;color:#f5b041}
.status.pending{background:#1a1a2a;color:#5dade2}
.status.cancelled{background:#2a1a1a;color:#888}
.url-cell{max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.action-btn{background:none;border:1px solid #444;color:#888;padding:4px 10px;border-radius:6px;cursor:pointer;font-size:11px;margin:0 2px}
.action-btn:hover{color:#e94560;border-color:#e94560}
.action-btn.pause:hover{color:#f5b041;border-color:#f5b041}
.action-btn.cancel:hover{color:#e94560;border-color:#e94560}

/* Detail panel */
.detail-panel{display:none;background:#1a1a2e;border-radius:10px;padding:20px;margin-top:12px}
.detail-panel.visible{display:block}
.detail-panel h3{color:#e94560;margin-bottom:12px;font-size:15px}
.detail-panel .row{display:flex;margin:6px 0;font-size:13px}
.detail-panel .row .label{color:#888;width:120px;flex-shrink:0}
.detail-panel .row .val{color:#e0e0e0;word-break:break-all}
.detail-panel pre{background:#0f0f23;padding:12px;border-radius:6px;margin-top:8px;font-size:12px;max-height:300px;overflow:auto}

/* SSE / realtime indicator */
.live-indicator{display:inline-flex;align-items:center;gap:6px;color:#4ecca3;font-size:12px}
.live-dot{width:8px;height:8px;border-radius:50%;background:#4ecca3;animation:pulse 2s infinite}
@keyframes pulse{0%{opacity:1}50%{opacity:.3}100%{opacity:1}}
</style>
</head>
<body>
<div class="header">
  <div><h1>ApexCrawler</h1><div class="sub">爬虫监控面板</div></div>
  <div class="live-indicator"><span class="live-dot"></span>实时</div>
</div>
<div class="container">
  <!-- Metrics -->
  <div class="metrics" id="metrics">
    <div class="metric-card"><div class="label">总任务</div><div class="value blue" id="m-total">0</div></div>
    <div class="metric-card"><div class="label">运行中</div><div class="value green" id="m-running">0</div></div>
    <div class="metric-card"><div class="label">已完成</div><div class="value green" id="m-completed">0</div></div>
    <div class="metric-card"><div class="label">失败</div><div class="value red" id="m-failed">0</div></div>
    <div class="metric-card"><div class="label">暂停</div><div class="value yellow" id="m-paused">0</div></div>
  </div>

  <!-- Actions -->
  <div class="actions">
    <input type="text" id="new-url" placeholder="输入 URL 创建新任务...">
    <select id="new-engine"><option value="">自动引擎</option><option value="vanilla">vanilla</option><option value="camoufox">camoufox</option><option value="cloaked_v2">cloaked_v2</option></select>
    <button onclick="createTask()">创建任务</button>
  </div>

  <!-- Table -->
  <div class="table-wrap">
    <table>
      <thead><tr><th>状态</th><th>URL</th><th>引擎</th><th>进度</th><th>创建时间</th><th>操作</th></tr></thead>
      <tbody id="task-list"><tr><td colspan="6" style="text-align:center;color:#666;padding:40px">加载中...</td></tr></tbody>
    </table>
  </div>

  <!-- Detail panel -->
  <div class="detail-panel" id="detail-panel">
    <h3>任务详情 <span style="font-weight:400;color:#888;font-size:13px" id="detail-id"></span></h3>
    <div id="detail-content"></div>
  </div>
</div>

<script>
// ── State ──
let tasks = [];
let selectedTaskId = null;
let API_KEY = sessionStorage.getItem('apex_api_key');

function ensureApiKey() {
    if (!API_KEY) {
        API_KEY = prompt('请输入 API Key：');
        if (API_KEY) {
            sessionStorage.setItem('apex_api_key', API_KEY);
        }
    }
    return API_KEY;
}

async function apiFetch(url, options = {}) {
    const key = ensureApiKey();
    const headers = options.headers || {};
    if (key) headers['X-API-Key'] = key;
    const r = await fetch(url, { ...options, headers });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    return r.json();
}

// ── Fetch tasks ──
async function fetchTasks() {
  try {
    tasks = await apiFetch('/api/tasks?limit=50');
    renderTable();
  } catch(e) { console.error('fetch tasks error:', e); }
}

// ── Fetch metrics ──
async function fetchMetrics() {
  try {
    const m = await apiFetch('/api/metrics');
    document.getElementById('m-total').textContent = m.total || 0;
    document.getElementById('m-running').textContent = m.running || 0;
    document.getElementById('m-completed').textContent = m.completed || 0;
    document.getElementById('m-failed').textContent = m.failed || 0;
    document.getElementById('m-paused').textContent = m.paused || 0;
  } catch(e) {}
}

// ── Render table ──
function renderTable() {
  const tbody = document.getElementById('task-list');
  if (!tasks.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#666;padding:40px">暂无任务</td></tr>';
    return;
  }
  tbody.innerHTML = tasks.map(t => {
    const statusColors = {
      running: 'running', completed: 'completed', failed: 'failed',
      paused: 'paused', pending: 'pending', cancelled: 'cancelled'
    };
    return '<tr>' +
      '<td><span class="status ' + (statusColors[t.status]||'') + '">' + t.status + '</span></td>' +
      '<td class="url-cell" title="' + t.url + '">' + t.url + '</td>' +
      '<td>' + (t.engine || '-') + '</td>' +
      '<td>' + t.progress + '%</td>' +
      '<td>' + (t.created_at ? t.created_at.slice(0,19) : '-') + '</td>' +
      '<td>' +
        (t.status === 'running' ? '<button class="action-btn pause" onclick="doAction(\''+t.task_id+'\',\'pause\')">暂停</button>' : '') +
        (t.status === 'paused' ? '<button class="action-btn" onclick="doAction(\''+t.task_id+'\',\'resume\')">恢复</button>' : '') +
        (t.status === 'running' || t.status === 'paused' ? '<button class="action-btn cancel" onclick="doAction(\''+t.task_id+'\',\'cancel\')">取消</button>' : '') +
        '<button class="action-btn" onclick="showDetail(\''+t.task_id+'\')">详情</button>' +
      '</td></tr>';
  }).join('');
}

// ── Actions ──
async function doAction(taskId, action) {
  try {
    await apiFetch('/api/tasks/' + taskId + '/' + action, {method:'POST'});
    await fetchTasks();
    await fetchMetrics();
  } catch(e) { alert('操作失败: ' + e.message); }
}

// ── Create task ──
async function createTask() {
  const url = document.getElementById('new-url').value.trim();
  if (!url) return alert('请输入 URL');
  const engine = document.getElementById('new-engine').value;
  try {
    await apiFetch('/api/tasks', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({url, engine})
    });
    document.getElementById('new-url').value = '';
    await fetchTasks();
    await fetchMetrics();
  } catch(e) { alert('创建失败: ' + e.message); }
}

// ── Show detail ──
async function showDetail(taskId) {
  const panel = document.getElementById('detail-panel');
  const content = document.getElementById('detail-content');
  document.getElementById('detail-id').textContent = '#' + taskId.slice(0,8);
  try {
    const t = await apiFetch('/api/tasks/' + taskId);
    let html = '<div class="row"><span class="label">URL</span><span class="val">' + t.url + '</span></div>';
    html += '<div class="row"><span class="label">状态</span><span class="val">' + t.status + '</span></div>';
    html += '<div class="row"><span class="label">引擎</span><span class="val">' + (t.engine||'-') + '</span></div>';
    html += '<div class="row"><span class="label">进度</span><span class="val">' + t.progress + '%</span></div>';
    html += '<div class="row"><span class="label">创建时间</span><span class="val">' + (t.created_at||'-') + '</span></div>';
    if (t.result) html += '<div class="row"><span class="label">结果</span><span class="val"><pre>' + JSON.stringify(t.result,null,2).substring(0,2000) + '</pre></span></div>';
    if (t.error) html += '<div class="row"><span class="label">错误</span><span class="val" style="color:#e94560">' + t.error + '</span></div>';
    content.innerHTML = html;
    panel.classList.add('visible');
  } catch(e) {
    content.innerHTML = '<div style="color:#e94560">加载失败: ' + e.message + '</div>';
    panel.classList.add('visible');
  }
}

// ── Auto refresh ──
fetchTasks();
fetchMetrics();
setInterval(fetchTasks, 3000);
setInterval(fetchMetrics, 5000);
</script>
</body></html>"""
