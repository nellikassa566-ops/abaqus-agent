# -*- coding: utf-8 -*-
"""Abaqus noGUI smoke test for structured ODB queries."""

import json
import os

try:
    here = os.path.dirname(os.path.dirname(__file__))
except Exception:
    here = os.getcwd()
plugin_path = os.path.join(here, 'abaqus_mcp_plugin.py')
execfile(plugin_path, globals())

odb_path = r'E:\prelearn\bga_cycle_book\Job-BGA-refined.odb'
if not os.path.exists(odb_path):
    print('ODB_SMOKE_SKIPPED missing ' + odb_path)
else:
    mises = query_odb_field(odb_path, 'S', step_name='THERMO', frame=1, invariant='MISES')
    print('MISES_QUERY_SUCCESS', mises.get('success'))
    if not mises.get('success'):
        raise RuntimeError(json.dumps(mises))

    allcd = extract_xy_history(odb_path, 'ALLCD', step_name='THERMO')
    print('ALLCD_QUERY_SUCCESS', allcd.get('success'), 'SERIES', len(allcd.get('series', [])))
    if not allcd.get('success'):
        raise RuntimeError(json.dumps(allcd))

    image_path = os.path.join(here, 'screenshots', 'odb_smoke_mises.png')
    image = export_result_image(odb_path, 'S', output_path=image_path, step_name='THERMO', frame=1, invariant='MISES')
    print('IMAGE_EXPORT_SUCCESS', image.get('success'))
    if not image.get('success'):
        raise RuntimeError(json.dumps(image))
