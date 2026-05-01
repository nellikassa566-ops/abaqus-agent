# Abaqus MCP Plugin v4.0

A plugin that enables communication between **Abaqus/CAE** and external AI assistants (or any MCP client) via file-based IPC.

## What's New in v4.0

- **Job management** — list and submit Abaqus jobs remotely
- **ODB inspection** — open ODB files read-only and query steps, frames, instances
- **Viewport capture** — screenshot any Abaqus viewport as base64 image
- **Richer model info** — now includes loads, BCs, interactions, and assembly instances
- **MCP resource** — `abaqus://status` resource for real-time plugin status
- **Stale command cleanup** — automatically removes commands older than 2 minutes
- **Logging** — all operations logged to `~/.abaqus-mcp/mcp.log`
- **Version tracking** — status.json and ping responses include plugin version
- **GUI Status button** — check plugin state from the Plug-ins menu

## Architecture

```text
┌─────────────┐  MCP protocol   ┌───────────────┐  file IPC   ┌──────────────┐
│  MCP Client  │ ──────────────> │  mcp_server.py │ ─────────> │ Abaqus/CAE   │
│  (Cursor AI) │ <────────────── │  (FastMCP)     │ <───────── │ (plugin.py)  │
└─────────────┘                  └───────────────┘             └──────────────┘
                                        │
                                   commands/*.json ──>  (plugin reads & deletes)
                                   results/*.json  <──  (plugin writes)
                                   status.json     <──  (heartbeat every 2s)
```

## Features

- Execute Python scripts in Abaqus remotely
- Query model information (parts, materials, steps, loads, BCs, interactions)
- List and submit analysis jobs
- Inspect ODB result files
- Capture viewport screenshots
- Simple file-based communication (no sockets required)
- Non-blocking background mode (GUI stays responsive)
- GUI menu entries for start / stop / status control
- Works with any MCP-compatible client (Cursor, Claude Desktop, etc.)

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/Cai-aa/abaqus-mcp.git ~/.abaqus-mcp
```

### 2. Install Python dependencies (for the MCP server)

```bash
pip install mcp
```

### 3. (Optional) Auto-load plugin on Abaqus startup

```powershell
# Windows
copy "$env:USERPROFILE\.abaqus-mcp\abaqus_v6.env.example" "$env:USERPROFILE\abaqus_v6.env"
```

```bash
# Linux/Mac
cp ~/.abaqus-mcp/abaqus_v6.env.example ~/abaqus_v6.env
```

### 4. (Optional) Install GUI plugin menu

```powershell
# Windows
Copy-Item -Recurse "$env:USERPROFILE\.abaqus-mcp\abaqus_plugins\mcp_control" "$env:USERPROFILE\abaqus_plugins\mcp_control"
```

```bash
# Linux/Mac
cp -r ~/.abaqus-mcp/abaqus_plugins/mcp_control ~/abaqus_plugins/mcp_control
```

### 5. Configure your MCP client

Add to your Cursor / Claude Desktop MCP settings (`.mcp.json`):

```json
{
  "mcpServers": {
    "abaqus-mcp-server": {
      "command": "python",
      "args": ["C:/Users/YourUsername/.abaqus-mcp/mcp_server.py"]
    }
  }
}
```

## Usage

### Start MCP (in Abaqus)

**Experimental** — non-blocking background thread (may be unstable on some Abaqus builds):

```python
mcp_start()  # GUI remains responsive if background worker is supported
```

Menu: `Plug-ins` → `MCP` → `Start MCP (Background)`

**Alternative** — cooperative loop (mostly responsive):

```python
mcp_coop_loop()
```

Menu: `Plug-ins` → `MCP` → `Start MCP (Cooperative)`

**Alternative** — blocking mode:

```python
mcp_loop()  # blocks console
```

Menu: `Plug-ins` → `MCP` → `Start MCP (Blocking)`


| Mode                            | GUI Responsive     | Stop Method                 |
| ------------------------------- | ------------------ | --------------------------- |
| Background (`mcp_start()`)      | Yes (if supported) | `mcp_stop()` or menu        |
| Cooperative (`mcp_coop_loop()`) | Mostly yes         | `mcp_stop()` or `stop.flag` |
| Blocking (`mcp_loop()`)         | No                 | `stop.flag` / interrupt     |


For maximum reliability, use `mcp_loop()` in production sessions.

### Check Status

```python
mcp_status()  # prints status to console
```

Menu: `Plug-ins` → `MCP` → `MCP Status`

### Stop MCP

Option 1 — GUI menu: `Plug-ins` → `MCP` → `Stop MCP`

Option 2 — Abaqus console:

```python
mcp_stop()
```

Option 3 — PowerShell:

```powershell
echo $null > "$env:USERPROFILE\.abaqus-mcp\stop.flag"
```

Option 4 — Run `stop_mcp.py` from any Python environment.

## MCP Tools

These tools are exposed to MCP clients via `mcp_server.py`:


| Tool                      | Description                                                   |
| ------------------------- | ------------------------------------------------------------- |
| `check_abaqus_connection` | Verify Abaqus is running and plugin is responding             |
| `execute_script`          | Execute a Python script inside Abaqus/CAE                     |
| `get_model_info`          | Get model details (parts, materials, steps, loads, BCs, etc.) |
| `list_templates`          | List installed simulation templates                           |
| `instantiate_template`    | Create a validated model spec from a template                 |
| `validate_model_spec`     | Validate a model spec before sending it to Abaqus             |
| `create_or_update_model_from_spec` | Dry-run or build an Abaqus model from structured JSON |
| `validate_model`          | Run Abaqus-side model checks and return fix hints             |
| `mesh_model`              | Seed and mesh model parts                                     |
| `list_jobs`               | List all analysis jobs in the session                         |
| `submit_job`              | Submit a job by name and wait for completion                  |
| `write_input`             | Write an input file without submitting                        |
| `submit_job_async`        | Submit a job and return immediately                           |
| `get_job_status`          | Read job status plus lightweight diagnostics                  |
| `cancel_job`              | Cancel a running job                                          |
| `parse_job_diagnostics`   | Parse `.sta`, `.msg`, and `.dat` solver diagnostics           |
| `get_odb_info`            | Open an ODB file read-only and return metadata                |
| `query_odb_field`         | Query min/max/avg for an ODB field output                     |
| `extract_xy_history`      | Extract ODB history output as XY pairs                        |
| `export_contour_image`    | Export a contour image from an ODB                            |
| `export_report`           | Write a concise Markdown ODB/job report                       |
| `get_viewport_image`      | Capture a viewport screenshot as base64                       |
| `ping`                    | Test connection (returns version info)                        |


## Structured Simulation Workflow

The recommended agent flow is:

1. Use `list_templates` and `instantiate_template` to start from a known simulation family.
2. Edit the returned JSON spec to match the user request.
3. Call `validate_model_spec`.
4. Call `create_or_update_model_from_spec` with `dry_run=true`.
5. Call `create_or_update_model_from_spec` with `dry_run=false`.
6. Call `validate_model`, then `write_input` or `submit_job_async`.
7. Poll with `get_job_status`; if it fails, call `parse_job_diagnostics`.
8. Use `query_odb_field`, `extract_xy_history`, `export_contour_image`, and `export_report` for post-processing.

Installed templates:

- `tsv_thermal_cycle`
- `bga_thermal_cycle`
- `uniaxial_tension`
- `contact_indentation`
- `heat_transfer`

`execute_script` remains available as an expert escape hatch for operations not covered by structured tools.


## MCP Resources


| URI               | Description                                                |
| ----------------- | ---------------------------------------------------------- |
| `abaqus://status` | Real-time plugin status (running/stopped, version, uptime) |


## File-Based IPC Protocol

Write a JSON command file into `~/.abaqus-mcp/commands/`:

```python
import json, os, time

command = {
    'id': 'my_command',
    'type': 'execute_script',
    'script': 'print("Hello from Abaqus!")',
    'timestamp': time.time(),
}

cmd_path = os.path.expanduser('~/.abaqus-mcp/commands/cmd_my_command.json')
with open(cmd_path, 'w') as f:
    json.dump(command, f)
```

Result will appear at `~/.abaqus-mcp/results/my_command.json`.

### Command Types


| Type                 | Parameters                | Description                     |
| -------------------- | ------------------------- | ------------------------------- |
| `execute_script`     | `script` (str)            | Execute Python script in Abaqus |
| `build_model_from_spec` | `spec`, `dry_run`       | Build or dry-run a structured model spec |
| `validate_model`     | `model_name`              | Validate current/named model    |
| `mesh_model`         | `model_name`, `global_size` | Mesh current/named model      |
| `get_model_info`     | —                         | Get current model information   |
| `list_jobs`          | —                         | List all defined jobs           |
| `write_input`        | `job_name`                | Write input file                |
| `submit_job`         | `job_name` (str)          | Submit and wait for a job       |
| `submit_job_async`   | `job_name`                | Submit and return immediately   |
| `get_job_status`     | `job_name`, `workdir`     | Read status and diagnostics     |
| `cancel_job`         | `job_name`                | Cancel a running job            |
| `parse_job_diagnostics` | `job_name`, `workdir`  | Parse solver log diagnostics    |
| `get_odb_info`       | `odb_path` (str)          | Read ODB metadata               |
| `query_odb_field`    | `odb_path`, `variable`, `step_name`, `frame`, `invariant` | Query field stats |
| `extract_xy_history` | `odb_path`, `variable`    | Extract history output          |
| `export_result_image` | `odb_path`, `variable`, `output_path` | Export contour image      |
| `export_report`      | `odb_path`, `report_path` | Export Markdown report          |
| `get_viewport_image` | `viewport_name`, `format` | Capture viewport screenshot     |
| `ping`               | —                         | Test connection                 |
| `stop`               | —                         | Request loop stop               |


## Directory Structure

```text
~/.abaqus-mcp/
├── abaqus_mcp_plugin.py      # Abaqus-side plugin (runs inside CAE)
├── mcp_server.py              # MCP server (runs externally)
├── stop_mcp.py                # Helper to send stop signal
├── abaqus_v6.env.example      # Auto-load config template
├── .mcp.json                  # MCP client config example
├── abaqus_plugins/
│   └── mcp_control/
│       ├── __init__.py
│       └── mcp_control_plugin.py
├── commands/                  # Incoming command files
├── results/                   # Outgoing result files
├── templates/                 # Parameterized simulation templates
├── transactions/              # Spec logs and optional CAE checkpoints
├── scripts/                   # Temporary script files
├── screenshots/               # Temporary viewport captures
├── status.json                # Heartbeat status (updated every 2s)
├── mcp.log                    # Operation log
└── stop.flag                  # Stop signal file
```

## Troubleshooting

- **Plugin says "running" but no commands are consumed:**
  1. Run `mcp_stop()` then `mcp_start()` again
  2. Check `~/.abaqus-mcp/status.json` — timestamp should update every ~2s
  3. Check `~/.abaqus-mcp/mcp.log` for errors
- **Abaqus uses a different home directory:**
  ```python
  import os; os.environ['ABAQUS_MCP_HOME'] = r'C:\Users\YourName\.abaqus-mcp'
  ```
  Set this before loading the plugin.
- **Commands timing out:**
  - Stale commands (>2 min) are auto-cleaned
  - Ensure the plugin is in `running` state via `mcp_status()`
- **GUI plugin not showing:**
  - Verify `~/abaqus_plugins/mcp_control/` exists and contains `mcp_control_plugin.py`
  - Restart Abaqus/CAE

## License

MIT License
