"""Multi-threaded HTTP media server with security headers, traversal guards, and stderr logging."""

from __future__ import annotations

import functools
import html
import http.server
import json
import logging
import mimetypes
import re
import socketserver
import sys
import threading
import urllib.parse
from pathlib import Path
from typing import Any

logger = logging.getLogger("media_server")

# MIME type mapping for WebP and common image assets
MIME_MAP = {
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}


def is_safe_path(base_dir: Path, requested_rel_path: str) -> bool:
    """Ensure that the requested relative path stays strictly within the base directory."""
    try:
        base = base_dir.resolve()
        target = (base / requested_rel_path).resolve()
        # In Python 3.9+, Path.is_relative_to checks boundary containment
        return target.is_relative_to(base)
    except Exception:
        return False


def render_dashboard_html(loaded_packs: dict[str, Any]) -> str:
    """Generate modern, responsive HTML dashboard for Action Packs and System Prompts."""
    packs_data: dict[str, Any] = {}
    for pack_name, pack in loaded_packs.items():
        packs_data[pack_name] = {
            "active": getattr(pack, "active_prompt", "default"),
            "prompts": getattr(pack, "prompts", {}),
        }

    first_pack_name = next(iter(loaded_packs.keys()), "") if loaded_packs else ""
    first_pack = loaded_packs.get(first_pack_name) if first_pack_name else None
    if first_pack:
        initial_prompt = first_pack.prompts.get(first_pack.active_prompt, "")
    else:
        initial_prompt = "No system prompt configured for loaded pack(s)."

    escaped_prompt = html.escape(initial_prompt)
    packs_json = json.dumps(packs_data)
    pack_select_style = "display: none;" if len(loaded_packs) <= 1 else ""

    cards_html_list: list[str] = []
    for pack_name, pack in loaded_packs.items():
        for action in pack.manifest.actions:
            media_files = action.get_media_files()
            files_html = []
            for mf in media_files:
                file_url = f"/{pack_name}/{mf}"
                files_html.append(
                    f'<a href="{html.escape(file_url)}" target="_blank" class="card-file" title="View file {html.escape(mf)}">📄 <code>{html.escape(mf)}</code> ↗</a>'
                )
            files_rendered = " ".join(files_html) if files_html else '<span class="card-file-none">None</span>'

            params_html = ""
            if action.parameters:
                p_items = []
                for p_name, p_def in action.parameters.items():
                    req_label = "required" if p_def.required else "optional"
                    p_items.append(
                        f"<code>{html.escape(p_name)}</code> ({html.escape(p_def.type)}, {req_label})"
                    )
                params_html = f'<div class="card-params"><strong>Params:</strong> {", ".join(p_items)}</div>'

            card = f"""
            <div class="action-card">
              <div class="card-body">
                <div class="card-header-row">
                  <span class="card-badge">{html.escape(pack_name)}</span>
                </div>
                <h3 class="card-title">{html.escape(action.name)}</h3>
                <div class="card-filename-row">
                  <span class="file-label">File:</span>
                  {files_rendered}
                </div>
                <p class="card-desc">{html.escape(action.description or 'No description provided.')}</p>
                {params_html}
              </div>
            </div>"""
            cards_html_list.append(card)

    cards_html = (
        "\n".join(cards_html_list)
        if cards_html_list
        else "<p style='color: var(--text-muted);'>No actions found in loaded packs.</p>"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Media-MCP Action Dashboard</title>
  <style>
    :root {{
      --bg: #090d16;
      --card-bg: #131b2e;
      --border: #232f48;
      --accent: #6366f1;
      --accent-hover: #4f46e5;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --success: #10b981;
      --danger: #ef4444;
      --code-bg: #0b1120;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      line-height: 1.5;
      padding: 2rem 1.5rem;
    }}
    .container {{
      max-width: 1040px;
      margin: 0 auto;
    }}
    header {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      margin-bottom: 2rem;
      padding-bottom: 1.5rem;
      border-bottom: 1px solid var(--border);
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }}
    .brand h1 {{
      font-size: 1.5rem;
      font-weight: 700;
      letter-spacing: -0.025em;
    }}
    .badge-status {{
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      background: rgba(16, 185, 129, 0.15);
      color: var(--success);
      padding: 0.25rem 0.6rem;
      border-radius: 9999px;
      font-size: 0.8rem;
      font-weight: 600;
      border: 1px solid rgba(16, 185, 129, 0.3);
    }}
    .badge-status::before {{
      content: "";
      width: 0.45rem;
      height: 0.45rem;
      background-color: var(--success);
      border-radius: 50%;
      display: inline-block;
      box-shadow: 0 0 8px var(--success);
    }}
    .header-actions {{
      display: flex;
      gap: 0.75rem;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.5rem 1rem;
      border-radius: 0.5rem;
      font-size: 0.875rem;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;
      border: none;
      transition: all 0.15s ease;
    }}
    .btn-sm {{
      padding: 0.35rem 0.75rem;
      font-size: 0.8rem;
    }}
    .btn-primary {{
      background-color: var(--accent);
      color: white;
    }}
    .btn-primary:hover {{
      background-color: var(--accent-hover);
      transform: translateY(-1px);
    }}
    .btn-secondary {{
      background-color: var(--card-bg);
      color: var(--text);
      border: 1px solid var(--border);
    }}
    .btn-secondary:hover {{
      background-color: var(--border);
    }}
    .btn-danger {{
      color: #f87171;
      border-color: rgba(239, 68, 68, 0.3);
    }}
    .btn-danger:hover {{
      background-color: rgba(239, 68, 68, 0.15);
      border-color: var(--danger);
    }}
    .section-card {{
      background-color: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      padding: 1.5rem;
      margin-bottom: 2rem;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
    }}
    .section-header {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 0.75rem;
      margin-bottom: 1rem;
    }}
    .section-title {{
      font-size: 1.15rem;
      font-weight: 600;
    }}
    .prompt-header-actions {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem;
    }}
    .prompt-toolbar {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 0.75rem;
      margin-bottom: 1rem;
      padding-bottom: 0.75rem;
      border-bottom: 1px solid var(--border);
    }}
    .prompt-selectors {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.75rem;
    }}
    .toolbar-label {{
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      font-size: 0.85rem;
      color: var(--text-muted);
      font-weight: 500;
    }}
    .select-input {{
      background-color: var(--code-bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 0.4rem;
      padding: 0.35rem 0.65rem;
      font-size: 0.85rem;
      outline: none;
      cursor: pointer;
    }}
    .select-input:focus {{
      border-color: var(--accent);
    }}
    .prompt-manage-actions {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}
    .prompt-box {{
      background-color: var(--code-bg);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      padding: 1rem;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 0.9rem;
      white-space: pre-wrap;
      word-break: break-word;
      color: #e2e8f0;
      line-height: 1.6;
      max-height: 360px;
      overflow-y: auto;
    }}
    .prompt-textarea {{
      width: 100%;
      min-height: 200px;
      resize: vertical;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      outline: none;
    }}
    .prompt-textarea:focus {{
      border-color: var(--accent);
    }}
    .status-toast {{
      padding: 0.5rem 0.85rem;
      border-radius: 0.5rem;
      font-size: 0.85rem;
      font-weight: 600;
      margin-bottom: 0.75rem;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}
    .status-toast.success {{
      background: rgba(16, 185, 129, 0.15);
      color: var(--success);
      border: 1px solid rgba(16, 185, 129, 0.3);
    }}
    .status-toast.error {{
      background: rgba(239, 68, 68, 0.15);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.3);
    }}
    .prompt-hint {{
      margin-top: 0.75rem;
      font-size: 0.825rem;
      color: var(--text-muted);
    }}
    .prompt-hint code {{
      background: var(--code-bg);
      padding: 0.15rem 0.4rem;
      border-radius: 0.25rem;
      border: 1px solid var(--border);
      color: #cbd5e1;
    }}
    .cards-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 1.25rem;
      margin-top: 1rem;
    }}
    .action-card {{
      background-color: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      transition: transform 0.15s ease, border-color 0.15s ease;
    }}
    .action-card:hover {{
      border-color: #3b82f6;
      transform: translateY(-2px);
    }}
    .card-body {{
      padding: 1.25rem;
      display: flex;
      flex-direction: column;
      flex-grow: 1;
    }}
    .card-header-row {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.4rem;
    }}
    .card-badge {{
      display: inline-block;
      font-size: 0.75rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: #818cf8;
      background: rgba(99, 102, 241, 0.12);
      border: 1px solid rgba(99, 102, 241, 0.25);
      padding: 0.15rem 0.5rem;
      border-radius: 0.375rem;
    }}
    .card-title {{
      font-size: 1.15rem;
      font-weight: 600;
      color: #f8fafc;
      margin-bottom: 0.4rem;
    }}
    .card-filename-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.4rem;
      margin-bottom: 0.75rem;
      font-size: 0.85rem;
    }}
    .file-label {{
      color: var(--text-muted);
      font-weight: 500;
      font-size: 0.8rem;
    }}
    .card-file {{
      display: inline-flex;
      align-items: center;
      gap: 0.25rem;
      background: var(--code-bg);
      border: 1px solid var(--border);
      color: #38bdf8;
      padding: 0.2rem 0.5rem;
      border-radius: 0.375rem;
      text-decoration: none;
      font-size: 0.8rem;
      transition: all 0.15s ease;
    }}
    .card-file:hover {{
      border-color: #38bdf8;
      background: rgba(56, 189, 248, 0.1);
      text-decoration: underline;
    }}
    .card-file code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }}
    .card-file-none {{
      color: var(--text-muted);
      font-style: italic;
      font-size: 0.8rem;
    }}
    .card-desc {{
      font-size: 0.875rem;
      color: var(--text-muted);
      line-height: 1.5;
      margin-bottom: 1rem;
      flex-grow: 1;
    }}
    .card-params {{
      font-size: 0.8rem;
      background: var(--code-bg);
      border: 1px solid var(--border);
      padding: 0.5rem 0.75rem;
      border-radius: 0.375rem;
      color: #94a3b8;
    }}
    .endpoints-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      margin-top: 2rem;
      padding-top: 1.5rem;
      border-top: 1px solid var(--border);
      font-size: 0.85rem;
      color: var(--text-muted);
    }}
    .endpoints-nav a {{
      color: #818cf8;
      text-decoration: none;
    }}
    .endpoints-nav a:hover {{
      text-decoration: underline;
    }}
    .modal-overlay {{
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.75);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1000;
      padding: 1rem;
    }}
    .modal-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      padding: 1.5rem;
      width: 100%;
      max-width: 520px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
    }}
    .modal-title {{
      font-size: 1.15rem;
      font-weight: 700;
      margin-bottom: 1rem;
    }}
    .form-group {{
      margin-bottom: 1rem;
    }}
    .form-label {{
      display: block;
      font-size: 0.825rem;
      color: var(--text-muted);
      margin-bottom: 0.35rem;
      font-weight: 500;
    }}
    .text-input {{
      width: 100%;
      background: var(--code-bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      padding: 0.5rem 0.75rem;
      font-size: 0.9rem;
      outline: none;
    }}
    .text-input:focus {{
      border-color: var(--accent);
    }}
    .modal-actions {{
      display: flex;
      justify-content: flex-end;
      gap: 0.5rem;
      margin-top: 1.25rem;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <h1>Media-MCP Action Dashboard</h1>
        <span class="badge-status">Active</span>
      </div>
      <div class="header-actions">
        <a id="webui-btn" class="btn btn-primary" href="#" target="_blank">
          Open llama-server WebUI (:8080) ↗
        </a>
      </div>
    </header>

    <section class="section-card">
      <div class="section-header">
        <h2 class="section-title">📋 System Prompt</h2>
        <div class="prompt-header-actions">
          <button id="copy-btn" class="btn btn-primary" onclick="copyCurrentPrompt()">📋 Copy Prompt</button>
          <button id="edit-btn" class="btn btn-secondary" onclick="toggleEditMode()">✏️ Edit Prompt</button>
          <button id="save-btn" class="btn btn-primary" style="display:none;" onclick="saveCurrentPrompt()">💾 Save</button>
          <button id="cancel-btn" class="btn btn-secondary" style="display:none;" onclick="cancelEditMode()">✕ Cancel</button>
        </div>
      </div>

      <div class="prompt-toolbar">
        <div class="prompt-selectors">
          <label id="pack-select-group" class="toolbar-label" style="{pack_select_style}">
            <span>Pack:</span>
            <select id="pack-select" class="select-input" onchange="onPackChange()"></select>
          </label>
          <label class="toolbar-label">
            <span>Prompt:</span>
            <select id="prompt-select" class="select-input" onchange="onPromptChange()"></select>
          </label>
        </div>
        <div class="prompt-manage-actions">
          <button id="new-btn" class="btn btn-secondary btn-sm" onclick="openNewPromptModal()">➕ New Prompt</button>
          <button id="delete-btn" class="btn btn-secondary btn-sm btn-danger" onclick="deleteCurrentPrompt()">🗑️ Delete</button>
        </div>
      </div>

      <div id="status-toast" class="status-toast" style="display:none;"></div>

      <div id="prompt-view-container">
        <pre id="prompt-box" class="prompt-box">{escaped_prompt}</pre>
        <textarea id="prompt-edit" class="prompt-box prompt-textarea" style="display:none;"></textarea>
      </div>

      <p class="prompt-hint">
        💡 Paste this prompt into the <strong>System Message</strong> box in the WebUI, or pipe directly via terminal:
        <code id="curl-hint">curl -s http://<span class="host-placeholder">localhost:8088</span>/prompt | pbcopy</code>
      </p>
    </section>

    <!-- Modal for New Prompt -->
    <div id="new-prompt-modal" class="modal-overlay" style="display:none;">
      <div class="modal-card">
        <h3 class="modal-title">➕ Create New Prompt</h3>
        <div class="form-group">
          <label class="form-label" for="new-prompt-name">Prompt Name (e.g. roleplay, concise, friendly):</label>
          <input type="text" id="new-prompt-name" class="text-input" placeholder="e.g. roleplay" />
        </div>
        <div class="form-group">
          <label class="form-label" for="new-prompt-content">Prompt Instructions:</label>
          <textarea id="new-prompt-content" class="prompt-box prompt-textarea" style="min-height: 140px;" placeholder="Write system instructions for this prompt..."></textarea>
        </div>
        <div class="modal-actions">
          <button class="btn btn-secondary" onclick="closeNewPromptModal()">Cancel</button>
          <button class="btn btn-primary" onclick="submitNewPrompt()">Create & Activate</button>
        </div>
      </div>
    </div>

    <section>
      <h2 class="section-title">🎭 Available Actions</h2>
      <div class="cards-grid">
        {cards_html}
      </div>
    </section>

    <footer class="endpoints-nav">
      <span>Endpoints:</span>
      <a href="/prompt">Raw Prompt (/prompt)</a> •
      <a href="/healthz">Health Check (/healthz)</a> •
      <a id="webui-footer-link" href="#" target="_blank">llama-server WebUI (:8080)</a>
    </footer>
  </div>

  <script>
    const host = window.location.hostname || 'localhost';
    const webuiUrl = 'http://' + host + ':8080';
    document.getElementById('webui-btn').href = webuiUrl;
    document.getElementById('webui-footer-link').href = webuiUrl;
    document.querySelectorAll('.host-placeholder').forEach(el => el.textContent = host + ':8088');

    const packsData = {packs_json};
    const packNames = Object.keys(packsData);
    let currentPack = packNames.length > 0 ? packNames[0] : '';
    let currentPrompt = currentPack && packsData[currentPack] ? packsData[currentPack].active : 'default';
    let isEditing = false;

    function showToast(message, isError = false) {{
      const toast = document.getElementById('status-toast');
      toast.textContent = message;
      toast.className = 'status-toast ' + (isError ? 'error' : 'success');
      toast.style.display = 'flex';
      setTimeout(() => {{
        toast.style.display = 'none';
      }}, 4000);
    }}

    function updateCurlHint() {{
      const curlHint = document.getElementById('curl-hint');
      const packPart = packNames.length > 1 ? `&pack=${{encodeURIComponent(currentPack)}}` : '';
      let url = `http://${{host}}:8088/prompt`;
      if (currentPrompt !== 'default' || packPart) {{
        url += `?name=${{encodeURIComponent(currentPrompt)}}${{packPart}}`;
      }}
      curlHint.textContent = `curl -s "${{url}}" | pbcopy`;
    }}

    function initUI() {{
      const packSelect = document.getElementById('pack-select');
      packSelect.innerHTML = '';
      packNames.forEach(p => {{
        const opt = document.createElement('option');
        opt.value = p;
        opt.textContent = p;
        if (p === currentPack) opt.selected = true;
        packSelect.appendChild(opt);
      }});

      refreshPromptDropdown();
    }}

    function refreshPromptDropdown() {{
      const promptSelect = document.getElementById('prompt-select');
      promptSelect.innerHTML = '';
      if (!currentPack || !packsData[currentPack]) return;

      const prompts = packsData[currentPack].prompts || {{}};
      const promptKeys = Object.keys(prompts);
      if (promptKeys.length === 0) {{
        prompts['default'] = '';
        promptKeys.push('default');
      }}

      if (!prompts.hasOwnProperty(currentPrompt)) {{
        currentPrompt = promptKeys[0];
      }}

      promptKeys.forEach(k => {{
        const opt = document.createElement('option');
        opt.value = k;
        opt.textContent = k + (k === packsData[currentPack].active ? ' (active)' : '');
        if (k === currentPrompt) opt.selected = true;
        promptSelect.appendChild(opt);
      }});

      const deleteBtn = document.getElementById('delete-btn');
      if (deleteBtn) {{
        deleteBtn.disabled = promptKeys.length <= 1;
        deleteBtn.style.opacity = promptKeys.length <= 1 ? '0.5' : '1';
      }}

      const text = prompts[currentPrompt] || '';
      document.getElementById('prompt-box').textContent = text;
      document.getElementById('prompt-edit').value = text;
      updateCurlHint();
    }}

    function onPackChange() {{
      const sel = document.getElementById('pack-select');
      currentPack = sel.value;
      currentPrompt = packsData[currentPack]?.active || Object.keys(packsData[currentPack]?.prompts || {{}})[0] || 'default';
      cancelEditMode();
      refreshPromptDropdown();
    }}

    function onPromptChange() {{
      const sel = document.getElementById('prompt-select');
      currentPrompt = sel.value;
      cancelEditMode();
      refreshPromptDropdown();

      fetch('/api/prompts', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ action: 'select', pack: currentPack, name: currentPrompt }})
      }}).then(r => r.json()).then(data => {{
        if (data.status === 'ok') {{
          packsData[currentPack].active = currentPrompt;
          refreshPromptDropdown();
        }}
      }}).catch(e => console.warn('Select prompt error:', e));
    }}

    function toggleEditMode() {{
      isEditing = true;
      document.getElementById('prompt-box').style.display = 'none';
      document.getElementById('prompt-edit').style.display = 'block';
      document.getElementById('edit-btn').style.display = 'none';
      document.getElementById('save-btn').style.display = 'inline-flex';
      document.getElementById('cancel-btn').style.display = 'inline-flex';
      document.getElementById('prompt-edit').focus();
    }}

    function cancelEditMode() {{
      isEditing = false;
      document.getElementById('prompt-box').style.display = 'block';
      document.getElementById('prompt-edit').style.display = 'none';
      document.getElementById('edit-btn').style.display = 'inline-flex';
      document.getElementById('save-btn').style.display = 'none';
      document.getElementById('cancel-btn').style.display = 'none';
      const text = packsData[currentPack]?.prompts[currentPrompt] || '';
      document.getElementById('prompt-box').textContent = text;
      document.getElementById('prompt-edit').value = text;
    }}

    function saveCurrentPrompt() {{
      const text = document.getElementById('prompt-edit').value;
      const saveBtn = document.getElementById('save-btn');
      saveBtn.textContent = 'Saving...';
      saveBtn.disabled = true;

      fetch('/api/prompts', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{
          action: 'save',
          pack: currentPack,
          name: currentPrompt,
          content: text,
          set_active: true
        }})
      }})
      .then(r => r.json())
      .then(data => {{
        saveBtn.textContent = '💾 Save';
        saveBtn.disabled = false;
        if (data.status === 'ok') {{
          packsData[currentPack].prompts = data.prompts;
          packsData[currentPack].active = data.active;
          cancelEditMode();
          refreshPromptDropdown();
          showToast('✓ Saved prompt and updated archive!');
        }} else {{
          showToast('Error saving: ' + (data.message || 'Unknown error'), true);
        }}
      }})
      .catch(err => {{
        saveBtn.textContent = '💾 Save';
        saveBtn.disabled = false;
        showToast('Error saving: ' + err.message, true);
      }});
    }}

    function openNewPromptModal() {{
      document.getElementById('new-prompt-name').value = '';
      document.getElementById('new-prompt-content').value = '';
      document.getElementById('new-prompt-modal').style.display = 'flex';
      document.getElementById('new-prompt-name').focus();
    }}

    function closeNewPromptModal() {{
      document.getElementById('new-prompt-modal').style.display = 'none';
    }}

    function submitNewPrompt() {{
      const name = document.getElementById('new-prompt-name').value.trim();
      const content = document.getElementById('new-prompt-content').value;

      if (!name) {{
        alert('Please enter a prompt name.');
        return;
      }}

      fetch('/api/prompts', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{
          action: 'save',
          pack: currentPack,
          name: name,
          content: content,
          set_active: true
        }})
      }})
      .then(r => r.json())
      .then(data => {{
        if (data.status === 'ok') {{
          packsData[currentPack].prompts = data.prompts;
          packsData[currentPack].active = data.active;
          currentPrompt = data.active;
          closeNewPromptModal();
          cancelEditMode();
          refreshPromptDropdown();
          showToast(`✓ Created prompt '${{name}}' and updated archive!`);
        }} else {{
          alert('Error creating prompt: ' + (data.message || 'Unknown error'));
        }}
      }})
      .catch(err => {{
        alert('Error: ' + err.message);
      }});
    }}

    function deleteCurrentPrompt() {{
      const keys = Object.keys(packsData[currentPack]?.prompts || {{}});
      if (keys.length <= 1) {{
        alert('Cannot delete the only remaining prompt.');
        return;
      }}

      if (!confirm(`Are you sure you want to delete prompt '${{currentPrompt}}'?`)) {{
        return;
      }}

      fetch('/api/prompts', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{
          action: 'delete',
          pack: currentPack,
          name: currentPrompt
        }})
      }})
      .then(r => r.json())
      .then(data => {{
        if (data.status === 'ok') {{
          packsData[currentPack].prompts = data.prompts;
          packsData[currentPack].active = data.active;
          currentPrompt = data.active;
          cancelEditMode();
          refreshPromptDropdown();
          showToast('✓ Deleted prompt and updated archive!');
        }} else {{
          alert('Error deleting prompt: ' + (data.message || 'Unknown error'));
        }}
      }})
      .catch(err => {{
        alert('Error: ' + err.message);
      }});
    }}

    async function copyTextToClipboard(text) {{
      if (navigator.clipboard && window.isSecureContext) {{
        try {{
          await navigator.clipboard.writeText(text);
          return true;
        }} catch (e) {{}}
      }}
      try {{
        const textArea = document.createElement("textarea");
        textArea.value = text;
        textArea.style.position = "fixed";
        textArea.style.top = "0";
        textArea.style.left = "0";
        textArea.style.width = "2em";
        textArea.style.height = "2em";
        textArea.style.padding = "0";
        textArea.style.border = "none";
        textArea.style.outline = "none";
        textArea.style.boxShadow = "none";
        textArea.style.background = "transparent";
        textArea.style.opacity = "0";
        textArea.setAttribute("readonly", "");
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        textArea.setSelectionRange(0, text.length);
        const successful = document.execCommand("copy");
        document.body.removeChild(textArea);
        return successful;
      }} catch (err) {{
        console.error("Copy failed:", err);
        return false;
      }}
    }}

    async function copyCurrentPrompt() {{
      const text = document.getElementById('prompt-box').textContent;
      const btn = document.getElementById('copy-btn');
      const originalText = btn.textContent;

      const success = await copyTextToClipboard(text);
      if (success) {{
        btn.textContent = '✓ Copied!';
        btn.style.backgroundColor = '#10b981';
        if (navigator.vibrate) navigator.vibrate(40);
        showToast('✓ Prompt copied to clipboard!');
        setTimeout(() => {{
          btn.textContent = originalText;
          btn.style.backgroundColor = '';
        }}, 2000);
      }} else {{
        const box = document.getElementById('prompt-box');
        const range = document.createRange();
        range.selectNodeContents(box);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        showToast('Text selected! Tap Copy in your mobile popup.', false);
      }}
    }}

    initUI();
  </script>
</body>
</html>"""


class MediaRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP Request Handler serving media assets with CORP, CORS, and PNA headers."""

    # Dictionary of pack_name -> Path(media_dir)
    pack_mounts: dict[str, Path] = {}
    # Dictionary of pack_name -> LoadedPack
    loaded_packs: dict[str, Any] = {}

    def log_message(self, format: str, *args: Any) -> None:
        """Route HTTP logs exclusively to the Python logger on sys.stderr to protect MCP stdio."""
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def _send_cors_headers(self) -> None:
        """Inject required headers for llama-server WebUI cross-origin isolation and private network access."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cache-Control", "no-cache")

    def _send_json_response(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        """Handle pre-flight requests."""
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:
        """Handle HEAD requests."""
        self._serve_file(send_body=False)

    def do_GET(self) -> None:
        """Handle GET requests."""
        self._serve_file(send_body=True)

    def do_POST(self) -> None:
        """Handle POST requests for prompt management API."""
        parsed_url = urllib.parse.urlparse(self.path)
        decoded_path = urllib.parse.unquote(parsed_url.path).strip("/")

        if decoded_path == "api/prompts":
            try:
                content_len = int(self.headers.get("Content-Length", 0))
                if content_len <= 0 or content_len > 1_000_000:
                    self._send_json_response(400, {"status": "error", "message": "Invalid request body size"})
                    return

                raw_body = self.rfile.read(content_len)
                data = json.loads(raw_body.decode("utf-8"))

                action = data.get("action")
                pack_name = data.get("pack")
                name = (data.get("name") or "").strip()
                content = data.get("content", "")

                pack = self.loaded_packs.get(pack_name)
                if not pack:
                    if len(self.loaded_packs) == 1:
                        pack = next(iter(self.loaded_packs.values()))
                    else:
                        self._send_json_response(404, {"status": "error", "message": f"Pack '{pack_name}' not found"})
                        return

                if action == "save":
                    if not name:
                        self._send_json_response(400, {"status": "error", "message": "Prompt name is required"})
                        return
                    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
                    if not safe_name:
                        self._send_json_response(400, {"status": "error", "message": "Invalid prompt name"})
                        return
                    set_active = data.get("set_active", True)
                    pack.save_prompt(safe_name, content, set_active=set_active)
                    self._send_json_response(200, {
                        "status": "ok",
                        "active": pack.active_prompt,
                        "prompts": pack.prompts,
                        "message": f"Saved prompt '{safe_name}'",
                    })
                    return

                elif action == "delete":
                    if not name:
                        self._send_json_response(400, {"status": "error", "message": "Prompt name is required"})
                        return
                    try:
                        deleted = pack.delete_prompt(name)
                        if not deleted:
                            self._send_json_response(404, {"status": "error", "message": f"Prompt '{name}' not found"})
                            return
                        self._send_json_response(200, {
                            "status": "ok",
                            "active": pack.active_prompt,
                            "prompts": pack.prompts,
                            "message": f"Deleted prompt '{name}'",
                        })
                    except ValueError as ve:
                        self._send_json_response(400, {"status": "error", "message": str(ve)})
                    return

                elif action == "select":
                    if not name:
                        self._send_json_response(400, {"status": "error", "message": "Prompt name is required"})
                        return
                    if pack.set_active_prompt(name):
                        self._send_json_response(200, {
                            "status": "ok",
                            "active": pack.active_prompt,
                            "prompts": pack.prompts,
                        })
                    else:
                        self._send_json_response(404, {"status": "error", "message": f"Prompt '{name}' not found"})
                    return

                else:
                    self._send_json_response(400, {"status": "error", "message": f"Unknown action: {action}"})
                    return

            except Exception as e:
                logger.error("Error processing POST /api/prompts: %s", e, exc_info=True)
                self._send_json_response(500, {"status": "error", "message": str(e)})
            return

        self._send_error_response(404, "Endpoint not found")

    def _serve_file(self, send_body: bool = True) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        decoded_path = urllib.parse.unquote(parsed_url.path).strip("/")

        # Health check
        if decoded_path == "healthz":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            body = b'{"status":"ok"}\n'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)
            return

        # System prompt plain text endpoint
        if decoded_path == "prompt":
            query = urllib.parse.parse_qs(parsed_url.query)
            pack_filter = query.get("pack", [None])[0]
            name_filter = query.get("name", [None])[0]

            prompts = []
            for p_name, pack in self.loaded_packs.items():
                if pack_filter and p_name != pack_filter:
                    continue
                if name_filter and hasattr(pack, "prompts") and name_filter in pack.prompts:
                    prompts.append(pack.prompts[name_filter])
                elif getattr(pack, "system_prompt", None):
                    prompts.append(pack.system_prompt)

            prompt_content = "\n\n".join(prompts) if prompts else ""
            if prompt_content and not prompt_content.endswith("\n"):
                prompt_content += "\n"
            body = prompt_content.encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)
            return

        # Action Pack Dashboard at root or index.html
        if not decoded_path or decoded_path in ("index.html", "dashboard"):
            dashboard_html = render_dashboard_html(self.loaded_packs)
            body = dashboard_html.encode("utf-8")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)
            return

        parts = decoded_path.split("/", 1)
        if len(parts) < 2:
            self._send_error_response(404, "Pack name or filename missing")
            return

        pack_name, rel_path = parts[0], parts[1]

        media_dir = self.pack_mounts.get(pack_name)
        if not media_dir:
            self._send_error_response(404, f"Pack '{pack_name}' not mounted")
            return

        # Security check: Prevent path traversal
        if not is_safe_path(media_dir, rel_path):
            logger.warning("Blocked directory traversal attempt: pack=%s, path=%s", pack_name, rel_path)
            self._send_error_response(403, "Forbidden path")
            return

        file_path = (media_dir / rel_path).resolve()
        if not file_path.is_file():
            self._send_error_response(404, f"File not found: {rel_path}")
            return

        # Determine MIME type
        ext = file_path.suffix.lower()
        mime_type = MIME_MAP.get(ext) or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

        try:
            file_size = file_path.stat().st_size
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(file_size))
            self.end_headers()

            if send_body:
                with open(file_path, "rb") as f:
                    while chunk := f.read(64 * 1024):
                        self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # Client browser dropped connection or closed tab while downloading
            pass
        except Exception as e:
            logger.error("Error serving %s: %s", file_path, e)

    def _send_error_response(self, code: int, message: str) -> None:
        try:
            self.send_response(code)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            body = message.encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Threaded TCP server configured with socket reuse and daemon threads."""

    allow_reuse_address = True
    daemon_threads = True


def start_background_server(
    pack_mounts: dict[str, Path],
    host: str = "0.0.0.0",
    port: int = 8088,
    loaded_packs: dict[str, Any] | None = None,
) -> tuple[ThreadedTCPServer, int]:
    """Start the media server in a background daemon thread.

    Returns:
        tuple[ThreadedTCPServer, int]: The running server instance and the bound port.
    """
    handler_class = functools.partial(MediaRequestHandler)
    # Configure mounted packs and pack objects on the handler class
    MediaRequestHandler.pack_mounts = pack_mounts
    MediaRequestHandler.loaded_packs = loaded_packs or {}

    server = ThreadedTCPServer((host, port), handler_class)
    bound_port = server.server_address[1]

    thread = threading.Thread(
        target=server.serve_forever,
        name="MediaHTTPServerThread",
        daemon=True,
    )
    thread.start()

    logger.info("Media server running on http://%s:%d with %d mounted packs", host, bound_port, len(pack_mounts))
    return server, bound_port
