#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Abaqus MCP Server v4.0 - bridges MCP protocol to file-based IPC with Abaqus.

Provides tools for script execution, model/job/ODB queries, and viewport capture.
Also exposes the Abaqus connection status as an MCP resource.
"""

import json
import os
import time
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from abaqus_mcp_tools import (
    build_command_payload,
    instantiate_template as instantiate_template_spec,
    list_template_metadata,
    parse_job_diagnostics_files,
    validate_model_spec as _validate_model_spec,
)

def _resolve_mcp_home() -> Path:
    for env_name in ('ABAQUS_AGENT_HOME', 'ABAQUS_MCP_HOME'):
        env_home = os.environ.get(env_name)
        if env_home:
            return Path(env_home).expanduser().resolve()

    script_dir = Path(__file__).resolve().parent
    if (script_dir / 'stop_mcp.py').exists():
        return script_dir

    for folder_name in ('.abaqus-agent', '.abaqus-mcp'):
        candidate = Path.home() / folder_name
        if candidate.exists():
            return candidate

    return Path.home() / '.abaqus-agent'


MCP_HOME = _resolve_mcp_home()
COMMANDS_DIR = MCP_HOME / 'commands'
RESULTS_DIR = MCP_HOME / 'results'
STATUS_FILE = MCP_HOME / 'status.json'
TIMEOUT = 30.0

mcp = FastMCP("abaqus-agent")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send_command(cmd_type: str, timeout: float = TIMEOUT, **kwargs) -> dict:
    """Write a command file and wait for the result file."""
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    cmd_id = uuid.uuid4().hex[:8]
    command = {'id': cmd_id, 'type': cmd_type, 'timestamp': time.time(), **kwargs}

    cmd_path = COMMANDS_DIR / f'cmd_{cmd_id}.json'
    result_path = RESULTS_DIR / f'{cmd_id}.json'

    with open(cmd_path, 'w', encoding='utf-8') as f:
        json.dump(command, f)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if result_path.exists():
            try:
                with open(result_path, 'r', encoding='utf-8') as f:
                    result = json.load(f)
                result_path.unlink(missing_ok=True)
                return result
            except Exception:
                pass
        time.sleep(0.05)

    try:
        cmd_path.unlink(missing_ok=True)
    except Exception:
        pass
    return {'success': False, 'error': f'Timeout: no response from Abaqus in {timeout}s'}


def _read_status() -> dict:
    try:
        with open(STATUS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# MCP Resource
# ---------------------------------------------------------------------------

@mcp.resource("abaqus://status")
def abaqus_status() -> str:
    """Current Abaqus MCP plugin status (running / stopped / ready)."""
    status = _read_status()
    if not status:
        return json.dumps({"connected": False, "detail": "status.json not found"})
    return json.dumps(status, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def check_abaqus_connection() -> str:
    """Check if Abaqus is running and the MCP plugin is loaded and responding."""
    status = _read_status()
    if not status:
        return 'Abaqus MCP plugin not found. Is Abaqus running with the plugin loaded?'

    s = status.get('status', 'unknown')
    msg = status.get('message', '')
    dt = status.get('datetime', '')
    ver = status.get('version', '?')

    if s != 'running':
        return (f'Abaqus plugin loaded but not running (status={s}). '
                f'Run mcp_start() in Abaqus console.')

    result = _send_command('ping', timeout=10.0)
    if result.get('success'):
        ping_data = result.get('data', {})
        pong_ver = ping_data.get('version', ver) if isinstance(ping_data, dict) else ver
        return (f'Connected to Abaqus MCP v{pong_ver}.\n'
                f'Status: {s} — {msg}\nLast update: {dt}')
    else:
        return (f'Abaqus plugin loaded but not responding to commands.\n'
                f'Status: {s} — {msg}\nPing result: {result}\n'
                f'Try running mcp_start() again in Abaqus.')


@mcp.tool()
def execute_script(script: str) -> str:
    """Expert escape hatch: execute a Python script inside Abaqus/CAE.

    The script runs in the Abaqus kernel environment with access to mdb and session.
    Use print() to return output.
    """
    result = _send_command('execute_script', script=script)
    if result.get('success'):
        output = result.get('output', '')
        return output if output else '(Script executed successfully, no output)'
    else:
        error = result.get('error', 'Unknown error')
        tb = result.get('traceback', '')
        return f'Error: {error}\n{tb}'.strip()


@mcp.tool()
def get_model_info() -> str:
    """Get detailed information about all models in the current Abaqus session.

    Returns parts, materials, steps, loads, BCs, interactions, assembly instances,
    and viewport info.
    """
    result = _send_command('get_model_info')
    if result.get('success'):
        data = result.get('data', {})
        return json.dumps(data, indent=2, ensure_ascii=False)
    else:
        return f'Error: {result.get("error", "Unknown error")}'


def _coerce_json_object(value, field_name: str) -> dict:
    """Accept dicts from MCP clients or JSON strings from text-only clients."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as exc:
            raise ValueError(f'{field_name} must be a JSON object: {exc}')
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f'{field_name} must be a JSON object')


@mcp.tool()
def list_templates() -> str:
    """List installed parameterized Abaqus model templates."""
    return json.dumps({'templates': list_template_metadata()}, indent=2, ensure_ascii=False)


@mcp.tool()
def instantiate_template(template_id: str, parameters: dict = None) -> str:
    """Instantiate a parameterized template and return a validated model spec."""
    try:
        params = _coerce_json_object(parameters, 'parameters')
        spec = instantiate_template_spec(template_id, params)
        return json.dumps({'success': True, 'spec': spec}, indent=2, ensure_ascii=False)
    except Exception as exc:
        return f'Error: {exc}'


@mcp.tool()
def validate_model_spec(spec: dict) -> str:
    """Validate a structured model spec without contacting Abaqus."""
    try:
        spec_obj = _coerce_json_object(spec, 'spec')
        return json.dumps(_validate_model_spec(spec_obj), indent=2, ensure_ascii=False)
    except Exception as exc:
        return f'Error: {exc}'


@mcp.tool()
def create_or_update_model_from_spec(spec: dict, dry_run: bool = True) -> str:
    """Create or update an Abaqus model from a structured JSON spec.

    Default dry_run=True returns validation and planned actions without mutating
    the Abaqus model. Set dry_run=False to build the model in Abaqus/CAE.
    """
    try:
        spec_obj = _coerce_json_object(spec, 'spec')
        payload = build_command_payload('build_model_from_spec', spec=spec_obj, dry_run=dry_run)
        validation = payload['validation']
        if not validation.get('valid'):
            return json.dumps({'success': False, 'validation': validation}, indent=2, ensure_ascii=False)
        result = _send_command(
            'build_model_from_spec',
            timeout=300.0,
            spec=payload['spec'],
            dry_run=dry_run,
            validation=validation,
        )
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as exc:
        return f'Error: {exc}'


@mcp.tool()
def validate_model(model_name: str = "") -> str:
    """Run Abaqus-side model validation and return machine-readable diagnostics."""
    result = _send_command('validate_model', timeout=60.0, model_name=model_name)
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def mesh_model(model_name: str = "", global_size: float = 0.0) -> str:
    """Seed and mesh the selected model's parts. global_size=0 keeps existing seeds."""
    result = _send_command(
        'mesh_model',
        timeout=300.0,
        model_name=model_name,
        global_size=global_size,
    )
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def list_jobs() -> str:
    """List all jobs defined in the current Abaqus session with their status."""
    result = _send_command('list_jobs')
    if result.get('success'):
        data = result.get('data', {})
        return json.dumps(data, indent=2, ensure_ascii=False)
    else:
        return f'Error: {result.get("error", "Unknown error")}'


@mcp.tool()
def submit_job(job_name: str) -> str:
    """Submit an Abaqus analysis job by name and wait for completion.

    The job must already be defined in the current Abaqus session (mdb.jobs).
    Returns the final job status.
    """
    result = _send_command('submit_job', timeout=600.0, job_name=job_name)
    if result.get('success'):
        data = result.get('data', {})
        return json.dumps(data, indent=2, ensure_ascii=False)
    else:
        error = result.get('error', 'Unknown error')
        data = result.get('data', {})
        detail = data.get('error', '') if isinstance(data, dict) else ''
        return f'Error: {error}\n{detail}'.strip()


@mcp.tool()
def write_input(job_name: str) -> str:
    """Write the input file for an existing Abaqus job without submitting it."""
    result = _send_command('write_input', timeout=120.0, job_name=job_name)
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def submit_job_async(job_name: str) -> str:
    """Submit an Abaqus job and return immediately with the initial status."""
    result = _send_command('submit_job_async', timeout=30.0, job_name=job_name)
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def get_job_status(job_name: str, workdir: str = "") -> str:
    """Return Abaqus job status plus lightweight .sta/.msg/.dat diagnostics."""
    result = _send_command(
        'get_job_status',
        timeout=30.0,
        job_name=job_name,
        workdir=workdir,
    )
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def cancel_job(job_name: str) -> str:
    """Cancel a running Abaqus job by name."""
    result = _send_command('cancel_job', timeout=30.0, job_name=job_name)
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def parse_job_diagnostics(job_name: str, workdir: str = "") -> str:
    """Parse Abaqus .sta/.msg/.dat diagnostics for a job."""
    result = _send_command(
        'parse_job_diagnostics',
        timeout=30.0,
        job_name=job_name,
        workdir=workdir,
    )
    if result.get('success'):
        return json.dumps(result, indent=2, ensure_ascii=False)

    if workdir:
        paths = [str(Path(workdir) / f'{job_name}.{ext}') for ext in ('sta', 'msg', 'dat')]
        fallback = parse_job_diagnostics_files(paths)
        return json.dumps({'success': True, 'data': fallback, 'fallback': 'local_files'}, indent=2, ensure_ascii=False)
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def get_odb_info(odb_path: str) -> str:
    """Open an ODB file (read-only) and return its metadata.

    Returns steps (with frame count and total time), parts, instances, etc.
    Provide the full path to the .odb file.
    """
    result = _send_command('get_odb_info', timeout=60.0, odb_path=odb_path)
    if result.get('success'):
        data = result.get('data', {})
        return json.dumps(data, indent=2, ensure_ascii=False)
    else:
        error = result.get('error', 'Unknown error')
        data = result.get('data', {})
        detail = data.get('error', '') if isinstance(data, dict) else ''
        return f'Error: {error}\n{detail}'.strip()


@mcp.tool()
def query_odb_field(
    odb_path: str,
    variable: str,
    step_name: str = "",
    frame: int = -1,
    time_value: float = None,
    invariant: str = "",
    instance: str = "",
    element_set: str = "",
) -> str:
    """Query min/max/avg for an ODB field output variable."""
    result = _send_command(
        'query_odb_field',
        timeout=120.0,
        odb_path=odb_path,
        variable=variable,
        step_name=step_name,
        frame=frame,
        time_value=time_value,
        invariant=invariant,
        instance=instance,
        element_set=element_set,
    )
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def extract_xy_history(
    odb_path: str,
    variable: str,
    step_name: str = "",
    region: str = "",
) -> str:
    """Extract ODB history output as XY pairs."""
    result = _send_command(
        'extract_xy_history',
        timeout=120.0,
        odb_path=odb_path,
        variable=variable,
        step_name=step_name,
        region=region,
    )
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def export_contour_image(
    odb_path: str,
    variable: str,
    output_path: str = "",
    step_name: str = "",
    frame: int = -1,
    invariant: str = "",
    instance: str = "",
    element_set: str = "",
) -> str:
    """Export a fixed-view contour image from an ODB."""
    result = _send_command(
        'export_result_image',
        timeout=120.0,
        odb_path=odb_path,
        variable=variable,
        output_path=output_path,
        step_name=step_name,
        frame=frame,
        invariant=invariant,
        instance=instance,
        element_set=element_set,
    )
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def export_report(odb_path: str, report_path: str = "", job_name: str = "") -> str:
    """Create a concise Markdown report for an ODB/job."""
    result = _send_command(
        'export_report',
        timeout=120.0,
        odb_path=odb_path,
        report_path=report_path,
        job_name=job_name,
    )
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def get_viewport_image(viewport_name: str = "", image_format: str = "PNG") -> str:
    """Capture a screenshot of an Abaqus viewport.

    Returns the image as a base64-encoded string.
    Leave viewport_name empty to use the current viewport.
    Supported formats: PNG, SVG, TIFF.
    """
    kwargs = {'format': image_format.upper()}
    if viewport_name:
        kwargs['viewport_name'] = viewport_name
    result = _send_command('get_viewport_image', timeout=30.0, **kwargs)
    if result.get('success'):
        data = result.get('data', {})
        if isinstance(data, dict) and data.get('success'):
            b64 = data.get('image_base64', '')
            fmt = data.get('format', 'png')
            return f'data:image/{fmt};base64,{b64}'
        return json.dumps(data, indent=2)
    else:
        return f'Error: {result.get("error", "Unknown error")}'


@mcp.tool()
def ping() -> str:
    """Send a ping to the Abaqus MCP plugin and return pong if alive."""
    result = _send_command('ping', timeout=10.0)
    if result.get('success'):
        data = result.get('data', {})
        if isinstance(data, dict):
            return f'pong (v{data.get("version", "?")})'
        return 'pong'
    return f'No response: {result.get("error", "unknown error")}'


if __name__ == '__main__':
    mcp.run()
