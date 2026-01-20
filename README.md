# Abaqus MCP Plugin

A plugin that enables communication between Abaqus CAE and external programs via file-based IPC (Inter-Process Communication).

## Features

- Execute Python scripts in Abaqus remotely
- Get model information (models, parts, materials, steps)
- Simple file-based communication (no socket dependencies)
- Easy to integrate with AI assistants or automation tools

## Installation

1. Clone or download this repository to `~/.abaqus-mcp`:
   ```bash
   git clone https://github.com/Cai-aa/.abaqus-mcp.git ~/.abaqus-mcp
   ```

2. (Optional) Auto-load on startup - copy the env file to your home directory:
   ```powershell
   # Windows
   copy "$env:USERPROFILE\.abaqus-mcp\abaqus_v6.env.example" "$env:USERPROFILE\abaqus_v6.env"
   ```
   ```bash
   # Linux/Mac
   cp ~/.abaqus-mcp/abaqus_v6.env.example ~/abaqus_v6.env
   ```

## Usage

### Start the Plugin in Abaqus

**Option 1: Auto-load (if installed abaqus_v6.env)**
- Just start Abaqus/CAE, the plugin loads automatically

**Option 2: Manual load**
1. Open Abaqus/CAE
2. Go to `File` → `Run Script...`
3. Select `abaqus_mcp_plugin.py`

Then in the Abaqus command line, run:

```python
mcp_loop()  # Start continuous command processing
```

### Stop the Plugin

Option 1: Run in PowerShell:
```powershell
echo $null > "$env:USERPROFILE\.abaqus-mcp\stop.flag"
```

Option 2: Run `stop_mcp.py` in any Python environment

### Send Commands

Write a JSON command file to `~/.abaqus-mcp/commands/`:

```python
import os
import json
import time

command = {
    'id': 'my_command',
    'type': 'execute_script',
    'script': 'print("Hello from Abaqus!")',
    'timestamp': time.time()
}

cmd_path = os.path.expanduser('~/.abaqus-mcp/commands/cmd_my_command.json')
with open(cmd_path, 'w') as f:
    json.dump(command, f)
```

Results will be written to `~/.abaqus-mcp/results/my_command.json`

## Command Types

| Type | Description |
|------|-------------|
| `execute_script` | Execute Python script in Abaqus |
| `get_model_info` | Get current model information |
| `ping` | Test connection |
| `stop` | Stop the MCP loop |

## Directory Structure

```
~/.abaqus-mcp/
├── abaqus_mcp_plugin.py    # Main plugin
├── stop_mcp.py             # Stop utility
├── abaqus_v6.env.example   # Auto-load config template
├── commands/               # Input: command JSON files
├── results/                # Output: result JSON files
├── scripts/                # Temporary script files
├── status.json             # Runtime status (auto-generated)
└── stop.flag               # Stop signal file
```

## License

MIT License
