# media-mcp

An MCP runtime that enables local LLMs (like `llama-server`) to trigger and render inline WebP animations, emotes, and reaction media directly in chat.

Includes an embedded **Action Pack Dashboard & System Prompt Hub** on port `8088` for live media previews, one-click system prompt copying, and quick-launch links to the `llama-server` WebUI.

---

## 1. Action Pack Structure

An Action Pack is a self-contained directory containing:
* `pack.json`: Manifest defining action tools, parameters, descriptions, and media file references.
* `system_prompt.txt`: Optional system prompt / persona instructing the model when and how to call the actions.
* `media/`: Folder containing animation and image assets (`.webp`, `.gif`, `.png`, etc.).

```text
my-action-pack/
├── pack.json             # Manifest and action definitions
├── system_prompt.txt     # Custom system prompt for chat sessions
└── media/
    ├── README.txt
    └── cat.webp          # Animation assets
```

### Scaffold a New Pack
```bash
.venv/bin/python cli.py create ~/.media-packs/reactions --name reactions
```
This automatically scaffolds `pack.json`, a starter `system_prompt.txt`, and the `media/` directory.

### Validate Your Pack
Drop your `.webp` images into `media/`, customize your actions and prompt, and validate:
```bash
.venv/bin/python cli.py validate ~/.media-packs/reactions
```

### Example `pack.json`
```json
{
  "$schema": "https://media-mcp.org/schema/v1/pack.json",
  "name": "reactions",
  "version": "1.0.0",
  "description": "Cute reaction animations and emotes",
  "actions": [
    {
      "name": "happy_cat",
      "description": "Displays a cute happy cat animation. Call this when the user asks for a cat, expresses joy, or wants cute animal media.",
      "media": "cat.webp",
      "alt": "Cute Cat Animation",
      "template": "A cute cat appears!\n\n{media}"
    }
  ]
}
```

---

## 2. Configure & Start with `llama-server`

Point your `mcp.json` to `tools.py` with `--pack` or `--packs-dir`:

```json
{
  "mcpServers": {
    "media-tools": {
      "command": "/absolute/path/to/media-mcp/.venv/bin/python",
      "args": [
        "/absolute/path/to/media-mcp/tools.py",
        "--pack", "/absolute/path/to/my-action-pack"
      ]
    }
  }
}
```

Launch `llama-server`:
```bash
llama-server -m /path/to/model.gguf --mcp-servers-config /absolute/path/to/mcp.json
```

When `tools.py` starts, it outputs a startup banner to stderr:
```text
==============================================================
🚀 Media MCP Server Ready
   • Media Dashboard & Prompt: http://<LAN_IP>:8088/
   • Raw System Prompt:       http://<LAN_IP>:8088/prompt
   • llama-server WebUI:      http://<LAN_IP>:8080/
==============================================================
```

> [!NOTE]
> The HTTP media server starts automatically on a background daemon thread (default port `8088`, with auto-fallback to `8089..8099`) and terminates cleanly when `llama-server` exits. No separate server terminal is needed.

---

## 3. Media Dashboard & Endpoints (Port 8088)

The background server provides built-in web endpoints accessible across your local network:

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/` or `/index.html` | `GET` | **Action Dashboard:** Responsive dark-mode UI with live WebP previews, one-click system prompt copying, and quick-launch to `llama-server` WebUI. |
| `/prompt` | `GET` | **Raw System Prompt:** Plain-text endpoint returning the active system prompt (ideal for scripting). |
| `/healthz` | `GET` | **Health Check:** Returns `{"status":"ok"}`. |
| `/<pack>/<filename>` | `GET` | **Media Streaming:** Serves media files with CORS, CORP (`require-corp`), and Private Network Access headers. |

### Terminal Quick Copy
Copy the active system prompt straight to your clipboard from any terminal:
```bash
# macOS
curl -s http://localhost:8088/prompt | pbcopy

# Linux (xclip / wl-copy)
curl -s http://localhost:8088/prompt | xclip -selection clipboard
```

### WebUI Workflow
1. Open the dashboard at `http://<LAN_IP>:8088/`.
2. Click **📋 Copy Prompt** on the System Prompt card.
3. Click **Open llama-server WebUI (:8080) ↗**.
4. Paste the prompt into the **System Message** input in the WebUI.
5. Chat with the model—it will trigger media tools and render animations inline!
