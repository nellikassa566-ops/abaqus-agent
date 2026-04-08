# Abaqus MCP Plugin

A plugin that enables communication between Abaqus/CAE and external tools via file-based IPC (Inter-Process Communication).

## Features

- Execute Python scripts in Abaqus remotely
- Get model information (models, parts, materials, steps)
- Simple file-based communication (no sockets)
- Easy integration with AI assistants and automation tools
- Non-blocking background mode (GUI stays responsive)
- GUI menu entries for start/stop control

## Installation

1. Clone or copy this repository to `~/.abaqus-mcp`:

```bash
git clone https://github.com/Cai-aa/.abaqus-mcp.git ~/.abaqus-mcp
```

2. Optional: auto-load plugin on Abaqus startup:

```powershell
# Windows
copy "$env:USERPROFILE\.abaqus-mcp\abaqus_v6.env.example" "$env:USERPROFILE\abaqus_v6.env"
```

```bash
# Linux/Mac
cp ~/.abaqus-mcp/abaqus_v6.env.example ~/abaqus_v6.env
```

3. Optional: install GUI plugin menu:

```powershell
# Windows
Copy-Item -Recurse "$env:USERPROFILE\.abaqus-mcp\abaqus_plugins\mcp_control" "$env:USERPROFILE\abaqus_plugins\mcp_control"
```

```bash
# Linux/Mac
cp -r ~/.abaqus-mcp/abaqus_plugins/mcp_control ~/abaqus_plugins/mcp_control
```

## Usage

### Start MCP

Recommended (non-blocking background):

```python
mcp_start()  # GUI remains responsive
```

Menu: `Plug-ins` -> `MCP` -> `Start MCP (Background)`

Compatibility alias (same backend as `mcp_start`):

```python
mcp_start_timer()
```

Menu: `Plug-ins` -> `MCP` -> `Start MCP (Timer)`

Alternative (cooperative loop in current console thread):

```python
mcp_coop_loop()
```

Alternative (blocking mode with explicit stop behavior):

```python
mcp_loop()  # blocks console loop
```

Menu: `Plug-ins` -> `MCP` -> `Start MCP (Blocking)`

| Mode | GUI Responsive | Stop Method |
|------|----------------|-------------|
| Background (`mcp_start()` / `mcp_start_timer()`) | Yes | `mcp_stop()` or menu |
| Cooperative (`mcp_coop_loop()`) | Mostly yes | `mcp_stop()` or `stop.flag` |
| Blocking (`mcp_loop()`) | No | `stop.flag` / interrupt |

### Stop MCP

Option 1: GUI menu
- `Plug-ins` -> `MCP` -> `Stop MCP`

Option 2: Command line

```python
mcp_stop()
```

Option 3: PowerShell

```powershell
echo $null > "$env:USERPROFILE\.abaqus-mcp\stop.flag"
```

Option 4: Run `stop_mcp.py`

## Send Commands

Write a JSON command file into `~/.abaqus-mcp/commands/`:

```python
import os
import json
import time

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

Result will be written to `~/.abaqus-mcp/results/my_command.json`.

## Command Types

| Type | Description |
|------|-------------|
| `execute_script` | Execute Python script in Abaqus |
| `get_model_info` | Get current model information |
| `ping` | Test connection |
| `stop` | Request loop stop |

## Directory Structure

```text
~/.abaqus-mcp/
├── abaqus_mcp_plugin.py
├── stop_mcp.py
├── abaqus_v6.env.example
├── abaqus_plugins/
│   └── mcp_control/
│       ├── __init__.py
│       └── mcp_control_plugin.py
├── commands/
├── results/
├── scripts/
├── status.json
└── stop.flag
```

## Troubleshooting

- If background mode says "running" but no commands are consumed:
  - Run `mcp_stop()`
  - Run `mcp_start()` again
  - Check `~/.abaqus-mcp/status.json` timestamp keeps updating every ~2s
- If Abaqus uses a different home directory, force MCP path before loading plugin:
  - `import os; os.environ['ABAQUS_MCP_HOME'] = r'C:\Users\Cai\.abaqus-mcp'`
- If still not responding, use `mcp_coop_loop()` temporarily to verify command path.

## License

MIT License
