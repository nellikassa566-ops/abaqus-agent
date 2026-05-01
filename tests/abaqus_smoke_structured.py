# -*- coding: utf-8 -*-
"""Abaqus noGUI smoke test for structured MCP model building."""

import json
import os

try:
    here = os.path.dirname(os.path.dirname(__file__))
except Exception:
    here = os.getcwd()
plugin_path = os.path.join(here, 'abaqus_mcp_plugin.py')
execfile(plugin_path, globals())

spec = {
    'model_name': 'MCP_Smoke_Model',
    'parts': [
        {'name': 'Block', 'type': 'block', 'dimensions': [1.0, 1.0, 1.0], 'origin': [0.0, 0.0, 0.0]}
    ],
    'materials': [
        {'name': 'Steel', 'elastic': {'youngs_modulus': 210000.0, 'poisson_ratio': 0.3}}
    ],
    'sections': [
        {'name': 'Section-Block', 'material': 'Steel', 'parts': ['Block']}
    ],
    'steps': [
        {'name': 'Load', 'type': 'static', 'time_period': 1.0}
    ],
    'boundary_conditions': [
        {'name': 'BC-Fixed', 'type': 'encastre', 'region': {'part': 'Block', 'face': 'xMin'}}
    ],
    'mesh': {'global_size': 0.5, 'element_type': 'C3D8R'},
    'jobs': [
        {'name': 'Job-MCP-Smoke', 'model': 'MCP_Smoke_Model'}
    ],
}

dry = build_model_from_spec(spec, dry_run=True)
print('DRY_RUN_SUCCESS', dry.get('success'))
if not dry.get('success'):
    raise RuntimeError(json.dumps(dry))

built = build_model_from_spec(spec, dry_run=False)
print('BUILD_SUCCESS', built.get('success'))
if not built.get('success'):
    raise RuntimeError(json.dumps(built))

validation = validate_model('MCP_Smoke_Model')
print('VALIDATION_ERRORS', len(validation.get('errors', [])))
if validation.get('errors'):
    raise RuntimeError(json.dumps(validation))

written = write_input('Job-MCP-Smoke')
print('WRITE_INPUT_SUCCESS', written.get('success'))
if not written.get('success'):
    raise RuntimeError(json.dumps(written))
