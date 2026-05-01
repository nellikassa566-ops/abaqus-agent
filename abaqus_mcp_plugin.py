# -*- coding: utf-8 -*-
"""
Abaqus MCP Plugin v4.0 - file IPC bridge.

Bridges Abaqus/CAE kernel to external MCP clients via file-based IPC.
Supports script execution, model/job/ODB queries, and viewport capture.

Usage:
1. File -> Run Script... -> choose this file
2. Run mcp_start() for non-blocking background mode (recommended)
3. Run mcp_loop() for blocking mode
4. Run mcp_stop() to stop
"""

import base64
import io
import json
import os
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime

__version__ = '4.0.0'

try:
    from abaqus import mdb, session
    ABAQUS_AVAILABLE = True
except ImportError:
    ABAQUS_AVAILABLE = False


def _resolve_mcp_home():
    """Resolve MCP home with explicit override support."""
    for env_name in ('ABAQUS_AGENT_HOME', 'ABAQUS_MCP_HOME'):
        env_home = os.environ.get(env_name, '').strip()
        if env_home:
            return os.path.abspath(os.path.expanduser(env_home))
    try:
        this_file = os.path.abspath(__file__)
        script_dir = os.path.dirname(this_file)
        if os.path.exists(os.path.join(script_dir, 'stop_mcp.py')):
            return script_dir
    except Exception:
        pass
    home = os.path.expanduser('~')
    for folder_name in ('.abaqus-agent', '.abaqus-mcp'):
        candidate = os.path.join(home, folder_name)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(home, '.abaqus-agent')


MCP_HOME = _resolve_mcp_home()
COMMANDS_DIR = os.path.join(MCP_HOME, 'commands')
RESULTS_DIR = os.path.join(MCP_HOME, 'results')
SCRIPTS_DIR = os.path.join(MCP_HOME, 'scripts')
SCREENSHOTS_DIR = os.path.join(MCP_HOME, 'screenshots')
STATUS_FILE = os.path.join(MCP_HOME, 'status.json')
STOP_FILE = os.path.join(MCP_HOME, 'stop.flag')
LOG_FILE = os.path.join(MCP_HOME, 'mcp.log')

STALE_COMMAND_AGE = 120.0

try:
    text_type = unicode
except NameError:
    text_type = str


def _as_text(value):
    if isinstance(value, text_type):
        return value
    try:
        return value.decode('utf-8')
    except Exception:
        return text_type(value)


def _write_text(path, text, mode='w'):
    with io.open(path, mode, encoding='utf-8') as f:
        f.write(_as_text(text))


def _dump_json(path, data, **kwargs):
    kwargs.setdefault('indent', 2)
    kwargs.setdefault('ensure_ascii', False)
    _write_text(path, json.dumps(data, **kwargs))


def ensure_dirs():
    for d in [COMMANDS_DIR, RESULTS_DIR, SCRIPTS_DIR, SCREENSHOTS_DIR]:
        if not os.path.exists(d):
            os.makedirs(d)


def _log(level, message):
    """Append a log entry to mcp.log (best-effort, never raises)."""
    try:
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = '[%s] %s: %s\n' % (ts, level, message)
        _write_text(LOG_FILE, line, mode='a')
    except Exception:
        pass


def write_status(status, message=""):
    """Write status atomically so external readers never see partial JSON."""
    payload = {
        "status": status,
        "message": message,
        "version": __version__,
        "timestamp": time.time(),
        "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pid": os.getpid(),
        "mcp_home": MCP_HOME,
    }
    tmp_file = STATUS_FILE + '.tmp'
    try:
        _dump_json(tmp_file, payload)
        for _ in range(5):
            try:
                os.replace(tmp_file, STATUS_FILE)
                return
            except Exception:
                time.sleep(0.02)
        _dump_json(STATUS_FILE, payload)
        try:
            os.remove(tmp_file)
        except Exception:
            pass
    except Exception:
        pass


def _write_json(path, data):
    _dump_json(path, data)


def _background_self_test(timeout=1.5):
    """
    Verify background worker can consume command files and write result files.
    Returns True if ping loopback succeeds.
    """
    test_id = 'bgtest_' + uuid.uuid4().hex[:8]
    cmd_path = os.path.join(COMMANDS_DIR, 'cmd_' + test_id + '.json')
    result_path = os.path.join(RESULTS_DIR, test_id + '.json')
    command = {
        'id': test_id,
        'type': 'ping',
        'timestamp': time.time(),
    }
    try:
        _write_json(cmd_path, command)
    except Exception:
        return False

    deadline = time.time() + max(0.5, float(timeout))
    while time.time() < deadline:
        if os.path.exists(result_path):
            try:
                with io.open(result_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return bool(data.get('success'))
            except Exception:
                return False
            finally:
                try:
                    os.remove(result_path)
                except Exception:
                    pass
        time.sleep(0.05)

    # cleanup stale test command/result
    try:
        if os.path.exists(cmd_path):
            os.remove(cmd_path)
    except Exception:
        pass
    try:
        if os.path.exists(result_path):
            os.remove(result_path)
    except Exception:
        pass
    return False


def _cleanup_stale_commands():
    """Remove command files older than STALE_COMMAND_AGE seconds."""
    now = time.time()
    try:
        for name in os.listdir(COMMANDS_DIR):
            if not name.endswith('.json'):
                continue
            fpath = os.path.join(COMMANDS_DIR, name)
            try:
                age = now - os.path.getmtime(fpath)
                if age > STALE_COMMAND_AGE:
                    os.remove(fpath)
                    _log('WARN', 'Removed stale command: ' + name)
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def execute_script(script_content, script_id):
    result = {
        "id": script_id,
        "success": False,
        "output": "",
        "error": None,
        "timestamp": time.time(),
    }
    script_path = os.path.join(SCRIPTS_DIR, 'script_' + script_id + '.py')
    try:
        _write_text(script_path, script_content)
    except Exception as e:
        result['error'] = str(e)
        return result

    exec_globals = {'__name__': '__main__', '__file__': script_path}
    try:
        from abaqus import mdb, session
        exec_globals['mdb'] = mdb
        exec_globals['session'] = session
    except Exception:
        pass

    output_lines = []
    exec_globals['print'] = lambda *a, **k: output_lines.append(' '.join(str(x) for x in a))

    try:
        with io.open(script_path, 'r', encoding='utf-8') as f:
            exec(compile(f.read(), script_path, 'exec'), exec_globals)
        result['success'] = True
        result['output'] = '\n'.join(output_lines)
    except Exception as e:
        result['error'] = str(e)
        result['traceback'] = traceback.format_exc()
    try:
        os.remove(script_path)
    except Exception:
        pass
    return result


def get_model_info():
    info = {'models': [], 'working_directory': os.getcwd()}
    try:
        from abaqus import mdb, session
        for name in mdb.models.keys():
            model_obj = mdb.models[name]
            model_data = {
                'name': name,
                'parts': {},
                'materials': list(model_obj.materials.keys()) if hasattr(model_obj, 'materials') else [],
                'sections': list(model_obj.sections.keys()) if hasattr(model_obj, 'sections') else [],
                'steps': {},
                'assemblies': {},
                'loads': {},
                'bcs': {},
                'interactions': {},
                'jobs': [],
            }
            if hasattr(model_obj, 'parts'):
                for part_name, part in model_obj.parts.items():
                    bbox = None
                    try:
                        pts = [v.pointOn[0] for v in part.vertices]
                        if pts:
                            bbox = {
                                'low': [min([p[i] for p in pts]) for i in range(3)],
                                'high': [max([p[i] for p in pts]) for i in range(3)],
                            }
                    except Exception:
                        pass
                    model_data['parts'][part_name] = {
                        'cells': len(part.cells) if hasattr(part, 'cells') else 0,
                        'faces': len(part.faces) if hasattr(part, 'faces') else 0,
                        'edges': len(part.edges) if hasattr(part, 'edges') else 0,
                        'nodes': len(part.nodes) if hasattr(part, 'nodes') else 0,
                        'elements': len(part.elements) if hasattr(part, 'elements') else 0,
                        'sets': list(part.sets.keys()) if hasattr(part, 'sets') else [],
                        'surfaces': list(part.surfaces.keys()) if hasattr(part, 'surfaces') else [],
                        'section_assignments': [
                            str(getattr(sa, 'sectionName', '')) for sa in getattr(part, 'sectionAssignments', [])
                        ],
                        'bounding_box': bbox,
                    }
            if hasattr(model_obj, 'steps'):
                for step_name, step in model_obj.steps.items():
                    step_data = {'type': step.__class__.__name__}
                    for attr in ('timePeriod', 'initialInc', 'maxInc', 'nlgeom'):
                        try:
                            step_data[attr] = str(getattr(step, attr))
                        except Exception:
                            pass
                    model_data['steps'][step_name] = step_data
            if hasattr(model_obj, 'loads'):
                for load_name, load in model_obj.loads.items():
                    model_data['loads'][load_name] = {'type': load.__class__.__name__}
            if hasattr(model_obj, 'boundaryConditions'):
                for bc_name, bc in model_obj.boundaryConditions.items():
                    model_data['bcs'][bc_name] = {'type': bc.__class__.__name__}
            if hasattr(model_obj, 'interactions'):
                for int_name, interaction in model_obj.interactions.items():
                    model_data['interactions'][int_name] = {'type': interaction.__class__.__name__}
            if hasattr(model_obj, 'rootAssembly') and model_obj.rootAssembly:
                ra = model_obj.rootAssembly
                if hasattr(ra, 'instances'):
                    for inst_name, inst in ra.instances.items():
                        model_data['assemblies'][inst_name] = {
                            'partName': getattr(inst, 'partName', ''),
                            'cells': len(inst.cells) if hasattr(inst, 'cells') else 0,
                            'faces': len(inst.faces) if hasattr(inst, 'faces') else 0,
                            'nodes': len(inst.nodes) if hasattr(inst, 'nodes') else 0,
                            'elements': len(inst.elements) if hasattr(inst, 'elements') else 0,
                        }
            for job_name, job in mdb.jobs.items():
                if str(getattr(job, 'model', '')) == name:
                    model_data['jobs'].append({
                        'name': job_name,
                        'status': str(getattr(job, 'status', 'UNKNOWN')),
                        'type': str(getattr(job, 'type', '')),
                    })
            info['models'].append(model_data)
        if hasattr(session, 'viewports'):
            info['current_viewport'] = session.currentViewportName
            info['viewports'] = list(session.viewports.keys())
    except Exception as e:
        info['error'] = str(e)
    return info


def list_jobs():
    """List all jobs in the current Abaqus session with their status."""
    jobs_info = []
    try:
        from abaqus import mdb
        for name in mdb.jobs.keys():
            job = mdb.jobs[name]
            job_data = {'name': name}
            for attr in ('status', 'type', 'model', 'description',
                         'numCpus', 'numDomains', 'memory'):
                try:
                    val = getattr(job, attr, None)
                    if val is not None:
                        job_data[attr] = str(val)
                except Exception:
                    pass
            jobs_info.append(job_data)
    except Exception as e:
        return {'error': str(e), 'jobs': []}
    return {'jobs': jobs_info}


def submit_job(job_name):
    """Submit a job by name."""
    try:
        from abaqus import mdb
        if job_name not in mdb.jobs:
            return {'success': False, 'error': 'Job not found: ' + job_name}
        job = mdb.jobs[job_name]
        job.submit(consistencyChecking=False)
        job.waitForCompletion()
        status = str(getattr(job, 'status', 'UNKNOWN'))
        return {'success': True, 'job': job_name, 'status': status}
    except Exception as e:
        return {'success': False, 'error': str(e), 'traceback': traceback.format_exc()}


def get_odb_info(odb_path):
    """Open an ODB (read-only) and return its metadata."""
    info = {}
    try:
        from odbAccess import openOdb
        odb = openOdb(path=str(odb_path), readOnly=True)
        try:
            info['steps'] = {}
            for step_name in odb.steps.keys():
                step = odb.steps[step_name]
                info['steps'][step_name] = {
                    'number': step.number,
                    'totalTime': step.totalTime,
                    'frames': len(step.frames),
                }
            info['parts'] = list(odb.parts.keys()) if hasattr(odb, 'parts') else []
            info['instances'] = list(odb.rootAssembly.instances.keys()) if hasattr(odb, 'rootAssembly') else []
            if hasattr(odb, 'sectionCategories'):
                info['sectionCategories'] = list(odb.sectionCategories.keys())
        finally:
            odb.close()
        info['success'] = True
    except Exception as e:
        info['success'] = False
        info['error'] = str(e)
    return info


def get_viewport_image(viewport_name=None, width=800, height=600, fmt='PNG'):
    """Capture a viewport image and return it as base64."""
    try:
        from abaqus import session
        vp_name = viewport_name or session.currentViewportName
        if vp_name not in session.viewports:
            return {'success': False, 'error': 'Viewport not found: ' + str(vp_name)}

        img_file = os.path.join(SCREENSHOTS_DIR, 'viewport_' + str(int(time.time())) + '.' + fmt.lower())
        session.printToFile(
            fileName=img_file,
            format=getattr(session, fmt.upper(), session.PNG),
            canvasObjects=(session.viewports[vp_name],)
        )
        if os.path.exists(img_file):
            with open(img_file, 'rb') as f:
                data = base64.b64encode(f.read()).decode('ascii')
            try:
                os.remove(img_file)
            except Exception:
                pass
            return {'success': True, 'image_base64': data, 'format': fmt.lower()}
        return {'success': False, 'error': 'Image file not created'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ---------------------------------------------------------------------------
# Structured finite-element workflow helpers
# ---------------------------------------------------------------------------

def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _repo_get(repo, key, default=None):
    try:
        if key in repo.keys():
            return repo[key]
    except Exception:
        try:
            return repo[key]
        except Exception:
            pass
    return default


def _abaqus_unset(value):
    from abaqusConstants import UNSET
    if value is None:
        return UNSET
    return value


def _save_transaction(label, command_type, payload):
    """Record a JSON transaction and copy the current CAE if it exists."""
    tx_dir = os.path.join(MCP_HOME, 'transactions')
    try:
        if not os.path.exists(tx_dir):
            os.makedirs(tx_dir)
    except Exception:
        pass
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    tx_id = stamp + '_' + uuid.uuid4().hex[:8]
    tx_path = os.path.join(tx_dir, tx_id + '.json')
    data = {
        'id': tx_id,
        'label': label,
        'command_type': command_type,
        'timestamp': time.time(),
        'payload': payload,
    }
    try:
        from abaqus import mdb
        cae_path = getattr(mdb, 'pathName', '')
        if cae_path and os.path.exists(cae_path):
            checkpoint = os.path.join(tx_dir, tx_id + '.cae')
            shutil.copy2(cae_path, checkpoint)
            data['checkpoint'] = checkpoint
    except Exception as e:
        data['checkpoint_error'] = str(e)
    try:
        _dump_json(tx_path, data)
    except Exception:
        pass
    return data


def _region_from_selector(model_obj, selector, meta):
    """Resolve a small v1 region selector into an Abaqus Region."""
    from regionToolset import Region
    ra = model_obj.rootAssembly
    selector = selector or {}
    part_name = selector.get('part') or selector.get('instance')
    if part_name:
        inst_name = part_name if part_name in ra.instances.keys() else part_name + '-1'
        inst = _repo_get(ra.instances, inst_name)
    else:
        inst = None
    if inst is None and ra.instances.keys():
        inst = ra.instances[ra.instances.keys()[0]]
        inst_name = inst.name
    if inst is None:
        raise RuntimeError('No assembly instance available for region selector')

    geom = meta.get(inst_name.replace('-1', ''), {})
    origin = geom.get('origin', [0.0, 0.0, 0.0])
    dims = geom.get('dimensions', [0.0, 0.0, 0.0])
    radius = geom.get('radius', 0.0)
    height = geom.get('height', dims[2] if len(dims) > 2 else 0.0)
    tol = 1.0e-6
    face = selector.get('face')
    if face:
        x0, y0, z0 = origin[0], origin[1], origin[2]
        if geom.get('type') == 'cylinder':
            x_min, x_max = x0 - radius, x0 + radius
            y_min, y_max = y0 - radius, y0 + radius
            z_min, z_max = z0, z0 + height
        else:
            x_min, x_max = x0, x0 + dims[0]
            y_min, y_max = y0, y0 + dims[1]
            z_min, z_max = z0, z0 + dims[2]
        boxes = {
            'xMin': (x_min - tol, x_min + tol, y_min - tol, y_max + tol, z_min - tol, z_max + tol),
            'xMax': (x_max - tol, x_max + tol, y_min - tol, y_max + tol, z_min - tol, z_max + tol),
            'yMin': (x_min - tol, x_max + tol, y_min - tol, y_min + tol, z_min - tol, z_max + tol),
            'yMax': (x_min - tol, x_max + tol, y_max - tol, y_max + tol, z_min - tol, z_max + tol),
            'zMin': (x_min - tol, x_max + tol, y_min - tol, y_max + tol, z_min - tol, z_min + tol),
            'zMax': (x_min - tol, x_max + tol, y_min - tol, y_max + tol, z_max - tol, z_max + tol),
        }
        if face not in boxes:
            raise RuntimeError('Unsupported face selector: ' + str(face))
        b = boxes[face]
        faces = inst.faces.getByBoundingBox(
            xMin=b[0], xMax=b[1], yMin=b[2], yMax=b[3], zMin=b[4], zMax=b[5])
        if len(faces) == 0:
            raise RuntimeError('Region selector produced empty face set: ' + str(selector))
        return Region(faces=faces)

    if selector.get('point') is not None:
        point = selector.get('point')
        verts = inst.vertices.getByBoundingBox(
            xMin=point[0] - tol, xMax=point[0] + tol,
            yMin=point[1] - tol, yMax=point[1] + tol,
            zMin=point[2] - tol, zMax=point[2] + tol)
        if len(verts) == 0:
            raise RuntimeError('Region selector produced empty vertex set: ' + str(selector))
        return Region(vertices=verts)

    if hasattr(inst, 'cells') and len(inst.cells):
        return Region(cells=inst.cells[:])
    return Region(faces=inst.faces[:])


def _create_material(model_obj, material_spec):
    mat = model_obj.Material(name=material_spec['name'])
    elastic = material_spec.get('elastic') or {}
    if elastic.get('table'):
        rows = [tuple(row) for row in elastic.get('table')]
        mat.Elastic(temperatureDependency=True, table=tuple(rows))
    elif elastic:
        mat.Elastic(table=((
            _safe_float(elastic.get('youngs_modulus')),
            _safe_float(elastic.get('poisson_ratio')),
        ),))
    expansion = material_spec.get('expansion') or {}
    if expansion.get('table'):
        rows = [tuple(row) for row in expansion.get('table')]
        mat.Expansion(temperatureDependency=True, table=tuple(rows))
    elif expansion.get('coefficient') is not None:
        mat.Expansion(table=((_safe_float(expansion.get('coefficient')),),))
    return mat


def _create_part(model_obj, part_spec):
    from abaqusConstants import DEFORMABLE_BODY, THREE_D
    name = part_spec['name']
    part_type = part_spec.get('type', 'block')
    if part_type == 'cylinder':
        radius = _safe_float(part_spec.get('radius'), 1.0)
        height = _safe_float(part_spec.get('height'), 1.0)
        sk = model_obj.ConstrainedSketch(name=name + '_sketch', sheetSize=max(radius * 6.0, 10.0))
        sk.CircleByCenterPerimeter(center=(0.0, 0.0), point1=(radius, 0.0))
        part = model_obj.Part(name=name, dimensionality=THREE_D, type=DEFORMABLE_BODY)
        part.BaseSolidExtrude(sketch=sk, depth=height)
    else:
        dims = part_spec.get('dimensions', [1.0, 1.0, 1.0])
        dx, dy, dz = _safe_float(dims[0], 1.0), _safe_float(dims[1], 1.0), _safe_float(dims[2], 1.0)
        sk = model_obj.ConstrainedSketch(name=name + '_sketch', sheetSize=max(dx, dy, dz, 10.0) * 2.0)
        sk.rectangle(point1=(0.0, 0.0), point2=(dx, dy))
        part = model_obj.Part(name=name, dimensionality=THREE_D, type=DEFORMABLE_BODY)
        part.BaseSolidExtrude(sketch=sk, depth=dz)
    try:
        del model_obj.sketches[sk.name]
    except Exception:
        pass
    return part


def build_model_from_spec(spec, dry_run=True, validation=None):
    """Build a v1 Abaqus model from a structured spec."""
    planned = {
        'model_name': spec.get('model_name', 'Model-1'),
        'parts': [p.get('name') for p in spec.get('parts', [])],
        'materials': [m.get('name') for m in spec.get('materials', [])],
        'sections': [s.get('name') for s in spec.get('sections', [])],
        'steps': [s.get('name') for s in spec.get('steps', [])],
        'jobs': [j.get('name') for j in spec.get('jobs', [])],
    }
    if dry_run:
        return {'success': True, 'dry_run': True, 'planned_actions': planned, 'validation': validation or {}}

    try:
        from abaqus import mdb
        from abaqusConstants import CARTESIAN, ON
        from regionToolset import Region
        import mesh

        _save_transaction('before_build_model_from_spec', 'build_model_from_spec', spec)

        model_name = spec.get('model_name', 'Model-1')
        if model_name in mdb.models:
            del mdb.models[model_name]
        model_obj = mdb.Model(name=model_name)
        meta = {}

        for part_spec in spec.get('parts', []):
            part = _create_part(model_obj, part_spec)
            origin = part_spec.get('origin', [0.0, 0.0, 0.0])
            meta[part_spec['name']] = {
                'type': part_spec.get('type', 'block'),
                'origin': origin,
                'dimensions': part_spec.get('dimensions', [0.0, 0.0, part_spec.get('height', 0.0)]),
                'radius': part_spec.get('radius', 0.0),
                'height': part_spec.get('height', 0.0),
            }

        for material_spec in spec.get('materials', []):
            _create_material(model_obj, material_spec)

        for section in spec.get('sections', []):
            sec_name = section['name']
            model_obj.HomogeneousSolidSection(name=sec_name, material=section['material'])
            for part_name in section.get('parts', []):
                if part_name in model_obj.parts:
                    part = model_obj.parts[part_name]
                    if len(part.cells):
                        part.SectionAssignment(region=Region(cells=part.cells[:]), sectionName=sec_name)

        ra = model_obj.rootAssembly
        ra.DatumCsysByDefault(CARTESIAN)
        for part_spec in spec.get('parts', []):
            part_name = part_spec['name']
            inst_name = part_name + '-1'
            ra.Instance(name=inst_name, part=model_obj.parts[part_name], dependent=ON)
            origin = part_spec.get('origin', [0.0, 0.0, 0.0])
            ra.translate(instanceList=(inst_name,), vector=tuple(origin))

        for amp in spec.get('amplitudes', []):
            if amp.get('type', 'tabular') == 'tabular':
                data = tuple([tuple(row) for row in amp.get('data', [])])
                model_obj.TabularAmplitude(name=amp['name'], data=data)

        for step in spec.get('steps', []):
            name = step['name']
            previous = step.get('previous', 'Initial')
            step_type = step.get('type', 'static')
            time_period = _safe_float(step.get('time_period'), 1.0)
            if step_type == 'visco':
                model_obj.ViscoStep(
                    name=name, previous=previous, timePeriod=time_period,
                    initialInc=_safe_float(step.get('initial_inc'), time_period),
                    maxInc=_safe_float(step.get('max_inc'), time_period),
                    cetol=_safe_float(step.get('cetol'), 0.01))
            elif step_type == 'heat_transfer':
                model_obj.HeatTransferStep(name=name, previous=previous, timePeriod=time_period)
            elif step_type == 'coupled_temp_displacement':
                model_obj.CoupledTempDisplacementStep(name=name, previous=previous, timePeriod=time_period)
            else:
                model_obj.StaticStep(name=name, previous=previous, timePeriod=time_period)

        for bc in spec.get('boundary_conditions', []):
            region = _region_from_selector(model_obj, bc.get('region'), meta)
            step_name = bc.get('step', 'Initial')
            bc_type = bc.get('type', 'displacement')
            if bc_type == 'encastre':
                model_obj.EncastreBC(name=bc['name'], createStepName=step_name, region=region)
            elif bc_type == 'xsymm':
                model_obj.XsymmBC(name=bc['name'], createStepName=step_name, region=region)
            elif bc_type == 'ysymm':
                model_obj.YsymmBC(name=bc['name'], createStepName=step_name, region=region)
            elif bc_type == 'zsymm':
                model_obj.ZsymmBC(name=bc['name'], createStepName=step_name, region=region)
            elif bc_type == 'temperature':
                model_obj.TemperatureBC(
                    name=bc['name'], createStepName=step_name, region=region,
                    magnitude=_safe_float(bc.get('magnitude'), 0.0))
            else:
                model_obj.DisplacementBC(
                    name=bc['name'], createStepName=step_name, region=region,
                    u1=_abaqus_unset(bc.get('u1')), u2=_abaqus_unset(bc.get('u2')), u3=_abaqus_unset(bc.get('u3')),
                    ur1=_abaqus_unset(bc.get('ur1')), ur2=_abaqus_unset(bc.get('ur2')), ur3=_abaqus_unset(bc.get('ur3')))

        all_cells = []
        for inst in ra.instances.values():
            if hasattr(inst, 'cells') and len(inst.cells):
                all_cells.extend(inst.cells[:])
        for field in spec.get('predefined_fields', []):
            if field.get('type') == 'temperature' and all_cells:
                model_obj.Temperature(
                    name=field['name'],
                    createStepName=field.get('step', spec.get('steps', [{'name': 'Initial'}])[0].get('name', 'Initial')),
                    region=Region(cells=tuple(all_cells)),
                    magnitudes=(_safe_float(field.get('magnitude'), 0.0),),
                    amplitude=field.get('amplitude', None))

        mesh_spec = spec.get('mesh') or {}
        global_size = _safe_float(mesh_spec.get('global_size'), 0.0)
        if global_size > 0.0:
            for part_key in model_obj.parts.keys():
                part = model_obj.parts[part_key]
                part.seedPart(size=global_size, deviationFactor=0.1, minSizeFactor=0.1)
                try:
                    elem = mesh.ElemType(elemCode=getattr(__import__('abaqusConstants'), mesh_spec.get('element_type', 'C3D8R')), elemLibrary=getattr(__import__('abaqusConstants'), 'STANDARD'))
                    part.setElementType(regions=(part.cells[:],), elemTypes=(elem,))
                except Exception:
                    pass
                try:
                    part.generateMesh()
                except Exception:
                    pass

        for job in spec.get('jobs', []):
            if job['name'] in mdb.jobs:
                del mdb.jobs[job['name']]
            mdb.Job(name=job['name'], model=job.get('model', model_name), description=job.get('description', 'Created from MCP spec'))

        return {
            'success': True,
            'dry_run': False,
            'model_name': model_name,
            'created': planned,
            'validation': validate_model(model_name),
        }
    except Exception as e:
        return {'success': False, 'error': str(e), 'traceback': traceback.format_exc(), 'planned_actions': planned}


def validate_model(model_name=''):
    """Return diagnostics for the active or named Abaqus model."""
    diagnostics = {'errors': [], 'warnings': [], 'info': {}, 'fix_hints': []}
    try:
        from abaqus import mdb
        if model_name:
            model_obj = _repo_get(mdb.models, model_name)
        else:
            keys = list(mdb.models.keys())
            model_obj = mdb.models[keys[0]] if keys else None
        if model_obj is None:
            diagnostics['errors'].append('model not found: ' + str(model_name))
            diagnostics['fix_hints'].append('Create a model first or pass a valid model_name.')
            return diagnostics

        diagnostics['info']['model_name'] = model_obj.name
        diagnostics['info']['parts'] = {}
        for part_name, part in model_obj.parts.items():
            elem_count = len(part.elements) if hasattr(part, 'elements') else 0
            cell_count = len(part.cells) if hasattr(part, 'cells') else 0
            section_count = len(part.sectionAssignments) if hasattr(part, 'sectionAssignments') else 0
            diagnostics['info']['parts'][part_name] = {
                'cells': cell_count,
                'elements': elem_count,
                'section_assignments': section_count,
                'sets': list(part.sets.keys()) if hasattr(part, 'sets') else [],
                'surfaces': list(part.surfaces.keys()) if hasattr(part, 'surfaces') else [],
            }
            if cell_count and section_count == 0:
                diagnostics['errors'].append('part has cells but no section assignment: ' + part_name)
            if cell_count and elem_count == 0:
                diagnostics['warnings'].append('part is not meshed: ' + part_name)

        diagnostics['info']['materials'] = list(model_obj.materials.keys())
        diagnostics['info']['steps'] = list(model_obj.steps.keys())
        diagnostics['info']['loads'] = list(model_obj.loads.keys()) if hasattr(model_obj, 'loads') else []
        diagnostics['info']['boundary_conditions'] = list(model_obj.boundaryConditions.keys()) if hasattr(model_obj, 'boundaryConditions') else []
        diagnostics['info']['interactions'] = list(model_obj.interactions.keys()) if hasattr(model_obj, 'interactions') else []

        if not model_obj.parts:
            diagnostics['errors'].append('model has no parts')
        if not model_obj.materials:
            diagnostics['warnings'].append('model has no materials')
        if hasattr(model_obj, 'rootAssembly') and not model_obj.rootAssembly.instances:
            diagnostics['warnings'].append('assembly has no instances')
        if not diagnostics['info']['boundary_conditions']:
            diagnostics['warnings'].append('model has no boundary conditions')

        if diagnostics['errors']:
            diagnostics['fix_hints'].append('Fix errors before writing input or submitting jobs.')
        if diagnostics['warnings']:
            diagnostics['fix_hints'].append('Review warnings before trusting solver results.')
    except Exception as e:
        diagnostics['errors'].append(str(e))
        diagnostics['traceback'] = traceback.format_exc()
    return diagnostics


def mesh_model(model_name='', global_size=0.0):
    try:
        from abaqus import mdb
        if model_name:
            model_obj = _repo_get(mdb.models, model_name)
        else:
            keys = list(mdb.models.keys())
            model_obj = mdb.models[keys[0]] if keys else None
        if model_obj is None:
            return {'success': False, 'error': 'model not found: ' + str(model_name)}
        gsize = _safe_float(global_size, 0.0)
        summary = {}
        for part_name, part in model_obj.parts.items():
            if gsize > 0.0:
                part.seedPart(size=gsize, deviationFactor=0.1, minSizeFactor=0.1)
            try:
                part.generateMesh()
            except Exception as e:
                summary[part_name] = {'success': False, 'error': str(e)}
                continue
            summary[part_name] = {'success': True, 'nodes': len(part.nodes), 'elements': len(part.elements)}
        return {'success': True, 'model': model_obj.name, 'parts': summary}
    except Exception as e:
        return {'success': False, 'error': str(e), 'traceback': traceback.format_exc()}


def write_input(job_name):
    try:
        from abaqus import mdb
        if job_name not in mdb.jobs:
            return {'success': False, 'error': 'Job not found: ' + job_name}
        mdb.jobs[job_name].writeInput(consistencyChecking=False)
        return {'success': True, 'job': job_name, 'input': os.path.abspath(job_name + '.inp')}
    except Exception as e:
        return {'success': False, 'error': str(e), 'traceback': traceback.format_exc()}


def submit_job_async(job_name):
    try:
        from abaqus import mdb
        if job_name not in mdb.jobs:
            return {'success': False, 'error': 'Job not found: ' + job_name}
        job = mdb.jobs[job_name]
        job.submit(consistencyChecking=False)
        return {'success': True, 'job': job_name, 'status': str(getattr(job, 'status', 'SUBMITTED'))}
    except Exception as e:
        return {'success': False, 'error': str(e), 'traceback': traceback.format_exc()}


def _job_log_paths(job_name, workdir=''):
    base = workdir or os.getcwd()
    return [os.path.join(base, job_name + ext) for ext in ('.sta', '.msg', '.dat')]


def _parse_diagnostics_text(text):
    upper = (text or '').upper()
    issues = []
    rules = [
        ('increment_cutback_failure', ['TOO MANY ATTEMPTS', 'TIME INCREMENT REQUIRED IS LESS THAN'], 'Reduce increment size, add stabilization, or improve mesh/contact.', 'error'),
        ('negative_eigenvalues', ['NEGATIVE EIGENVALUE'], 'Check constraints, rigid body motion, contact, and stiffness.', 'warning'),
        ('distorted_elements', ['DISTORTED', 'EXCESSIVE DISTORTION'], 'Refine or repair mesh around reported elements.', 'warning'),
        ('contact_penetration', ['CONTACT', 'PENETRATION'], 'Review contact pair orientation and contact controls.', 'warning'),
        ('creep_subroutine', ['USER SUBROUTINE CREEP', 'CREEP WILL CAUSE CODE EXECUTION ERRORS'], 'Check creep constants/material names/subroutine availability.', 'warning'),
    ]
    for code, patterns, hint, severity in rules:
        for pattern in patterns:
            if pattern in upper:
                issues.append({'code': code, 'severity': severity, 'fix_hint': hint})
                break
    completed = 'THE ANALYSIS HAS BEEN COMPLETED' in upper or 'COMPLETED' in upper
    explicit_error = '***ERROR' in upper
    ok = completed and not explicit_error and not any([i.get('severity') == 'error' for i in issues])
    return {'ok': ok, 'completed': completed, 'issues': issues}


def parse_job_diagnostics(job_name, workdir=''):
    combined = []
    files = []
    for path in _job_log_paths(job_name, workdir):
        if os.path.exists(path):
            files.append(path)
            try:
                with io.open(path, 'r', encoding='utf-8', errors='replace') as f:
                    combined.append(f.read())
            except TypeError:
                with io.open(path, 'r', encoding='utf-8') as f:
                    combined.append(f.read())
            except Exception:
                pass
    data = _parse_diagnostics_text('\n'.join(combined))
    data['files'] = files
    return data


def get_job_status(job_name, workdir=''):
    try:
        from abaqus import mdb
        status = 'UNKNOWN'
        if job_name in mdb.jobs:
            status = str(getattr(mdb.jobs[job_name], 'status', 'UNKNOWN'))
        diagnostics = parse_job_diagnostics(job_name, workdir)
        return {'success': True, 'job': job_name, 'status': status, 'diagnostics': diagnostics}
    except Exception as e:
        return {'success': False, 'error': str(e), 'traceback': traceback.format_exc()}


def cancel_job(job_name):
    try:
        from abaqus import mdb
        if job_name not in mdb.jobs:
            return {'success': False, 'error': 'Job not found: ' + job_name}
        mdb.jobs[job_name].kill()
        return {'success': True, 'job': job_name, 'status': str(getattr(mdb.jobs[job_name], 'status', 'KILLED'))}
    except Exception as e:
        return {'success': False, 'error': str(e), 'traceback': traceback.format_exc()}


def _choose_odb_frame(step, frame=-1, time_value=None):
    if time_value is not None:
        return min(step.frames, key=lambda f: abs(f.frameValue - time_value))
    if frame is None:
        frame = -1
    return step.frames[int(frame)]


def _invariant_constant(name):
    if not name:
        return None
    import abaqusConstants
    return getattr(abaqusConstants, str(name).upper(), None)


def _invariant_label(name):
    labels = {
        'MISES': 'Mises',
        'MAX_PRINCIPAL': 'Max. Principal',
        'MID_PRINCIPAL': 'Mid. Principal',
        'MIN_PRINCIPAL': 'Min. Principal',
        'TRESCA': 'Tresca',
        'PRESS': 'Pressure',
    }
    return labels.get(str(name).upper(), str(name))


def query_odb_field(odb_path, variable, step_name='', frame=-1, time_value=None, invariant='', instance='', element_set=''):
    try:
        from odbAccess import openOdb
        odb = openOdb(path=str(odb_path), readOnly=True)
        try:
            if not step_name:
                step_name = list(odb.steps.keys())[-1]
            step = odb.steps[step_name]
            odb_frame = _choose_odb_frame(step, frame, time_value)
            field = odb_frame.fieldOutputs[variable]
            inv = _invariant_constant(invariant)
            if inv is not None:
                field = field.getScalarField(invariant=inv)
            if element_set:
                region = None
                if instance and instance in odb.rootAssembly.instances:
                    inst = odb.rootAssembly.instances[instance]
                    region = _repo_get(inst.elementSets, element_set)
                if region is None:
                    region = _repo_get(odb.rootAssembly.elementSets, element_set)
                if region is not None:
                    field = field.getSubset(region=region)
            values = [v.data for v in field.values]
            scalar_values = []
            for value in values:
                try:
                    scalar_values.append(float(value))
                except Exception:
                    try:
                        scalar_values.append(float(value[0]))
                    except Exception:
                        pass
            if not scalar_values:
                return {'success': False, 'error': 'No scalar values found for field ' + variable}
            return {
                'success': True,
                'odb_path': odb_path,
                'step': step_name,
                'frame': odb_frame.incrementNumber,
                'frame_value': odb_frame.frameValue,
                'variable': variable,
                'count': len(scalar_values),
                'min': min(scalar_values),
                'max': max(scalar_values),
                'avg': sum(scalar_values) / float(len(scalar_values)),
            }
        finally:
            odb.close()
    except Exception as e:
        return {'success': False, 'error': str(e), 'traceback': traceback.format_exc()}


def extract_xy_history(odb_path, variable, step_name='', region=''):
    try:
        from odbAccess import openOdb
        odb = openOdb(path=str(odb_path), readOnly=True)
        try:
            if not step_name:
                step_name = list(odb.steps.keys())[-1]
            step = odb.steps[step_name]
            series = []
            for reg_name, hist_region in step.historyRegions.items():
                if region and region not in reg_name:
                    continue
                if variable in hist_region.historyOutputs:
                    series.append({
                        'region': reg_name,
                        'variable': variable,
                        'data': list(hist_region.historyOutputs[variable].data),
                    })
            return {'success': True, 'odb_path': odb_path, 'step': step_name, 'series': series}
        finally:
            odb.close()
    except Exception as e:
        return {'success': False, 'error': str(e), 'traceback': traceback.format_exc()}


def export_result_image(odb_path, variable, output_path='', step_name='', frame=-1, invariant='', instance='', element_set=''):
    try:
        try:
            import visualization
        except Exception:
            pass
        from abaqus import session
        from abaqusConstants import CONTOURS_ON_DEF, INTEGRATION_POINT, INVARIANT, NODAL, PNG
        odb = session.openOdb(name=str(odb_path))
        vp_name = 'MCP Result'
        if vp_name in session.viewports:
            vp = session.viewports[vp_name]
        else:
            vp = session.Viewport(name=vp_name, origin=(0, 0), width=160, height=100)
        vp.setValues(displayedObject=odb)
        if step_name:
            step_index = list(odb.steps.keys()).index(step_name)
        else:
            step_index = len(odb.steps.keys()) - 1
        vp.odbDisplay.setFrame(step=step_index, frame=int(frame))
        refinement = None
        inv = _invariant_constant(invariant)
        if inv is not None:
            refinement = (INVARIANT, _invariant_label(invariant))
        try:
            if refinement:
                vp.odbDisplay.setPrimaryVariable(variableLabel=variable, outputPosition=INTEGRATION_POINT, refinement=refinement)
            else:
                vp.odbDisplay.setPrimaryVariable(variableLabel=variable, outputPosition=INTEGRATION_POINT)
        except Exception:
            vp.odbDisplay.setPrimaryVariable(variableLabel=variable, outputPosition=NODAL)
        if element_set:
            import displayGroupOdbToolset as dgo
            set_name = element_set
            if instance:
                set_name = instance + '.' + element_set
            leaf = dgo.LeafFromElementSets(elementSets=(set_name,))
            vp.odbDisplay.displayGroup.replace(leaf=leaf)
        vp.odbDisplay.display.setValues(plotState=(CONTOURS_ON_DEF,))
        vp.view.fitView()
        if not output_path:
            output_path = os.path.join(SCREENSHOTS_DIR, 'result_' + str(int(time.time())) + '.png')
        file_base = output_path[:-4] if output_path.lower().endswith('.png') else output_path
        session.printToFile(fileName=file_base, format=PNG, canvasObjects=(vp,))
        final_path = file_base + '.png'
        return {'success': True, 'image_path': final_path, 'odb_path': odb_path, 'variable': variable}
    except Exception as e:
        return {'success': False, 'error': str(e), 'traceback': traceback.format_exc()}


def export_report(odb_path, report_path='', job_name=''):
    try:
        info = get_odb_info(odb_path)
        diagnostics = parse_job_diagnostics(job_name or os.path.splitext(os.path.basename(odb_path))[0], os.path.dirname(odb_path))
        if not report_path:
            report_path = os.path.splitext(odb_path)[0] + '_report.md'
        lines = [
            '# Abaqus MCP Result Report',
            '',
            '- ODB: `' + str(odb_path) + '`',
            '- Diagnostics OK: `' + str(diagnostics.get('ok')) + '`',
            '',
            '## ODB Info',
            '',
            '```json',
            json.dumps(info, indent=2),
            '```',
            '',
            '## Diagnostics',
            '',
            '```json',
            json.dumps(diagnostics, indent=2),
            '```',
        ]
        _write_text(report_path, '\n'.join(lines))
        return {'success': True, 'report_path': report_path, 'odb_info': info, 'diagnostics': diagnostics}
    except Exception as e:
        return {'success': False, 'error': str(e), 'traceback': traceback.format_exc()}


# ---------------------------------------------------------------------------
# Command dispatcher
# ---------------------------------------------------------------------------

def process_command(command):
    cmd_id = command.get('id', 'unknown')
    cmd_type = command.get('type', 'unknown')
    result = {'id': cmd_id, 'success': False, 'timestamp': time.time()}

    try:
        if cmd_type == 'execute_script':
            result = execute_script(command.get('script', ''), cmd_id)
        elif cmd_type == 'build_model_from_spec':
            data = build_model_from_spec(
                command.get('spec', {}),
                dry_run=command.get('dry_run', True),
                validation=command.get('validation', {}),
            )
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'get_model_info':
            result['success'] = True
            result['data'] = get_model_info()
        elif cmd_type == 'validate_model':
            result['success'] = True
            result['data'] = validate_model(command.get('model_name', ''))
        elif cmd_type == 'mesh_model':
            data = mesh_model(command.get('model_name', ''), command.get('global_size', 0.0))
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'list_jobs':
            data = list_jobs()
            result['success'] = 'error' not in data
            result['data'] = data
        elif cmd_type == 'write_input':
            data = write_input(command.get('job_name', ''))
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'submit_job':
            data = submit_job(command.get('job_name', ''))
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'submit_job_async':
            data = submit_job_async(command.get('job_name', ''))
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'get_job_status':
            data = get_job_status(command.get('job_name', ''), command.get('workdir', ''))
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'cancel_job':
            data = cancel_job(command.get('job_name', ''))
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'parse_job_diagnostics':
            result['success'] = True
            result['data'] = parse_job_diagnostics(command.get('job_name', ''), command.get('workdir', ''))
        elif cmd_type == 'get_odb_info':
            data = get_odb_info(command.get('odb_path', ''))
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'query_odb_field':
            data = query_odb_field(
                command.get('odb_path', ''),
                command.get('variable', ''),
                command.get('step_name', ''),
                command.get('frame', -1),
                command.get('time_value'),
                command.get('invariant', ''),
                command.get('instance', ''),
                command.get('element_set', ''),
            )
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'extract_xy_history':
            data = extract_xy_history(
                command.get('odb_path', ''),
                command.get('variable', ''),
                command.get('step_name', ''),
                command.get('region', ''),
            )
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'export_result_image':
            data = export_result_image(
                command.get('odb_path', ''),
                command.get('variable', ''),
                command.get('output_path', ''),
                command.get('step_name', ''),
                command.get('frame', -1),
                command.get('invariant', ''),
                command.get('instance', ''),
                command.get('element_set', ''),
            )
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'export_report':
            data = export_report(
                command.get('odb_path', ''),
                command.get('report_path', ''),
                command.get('job_name', ''),
            )
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'get_viewport_image':
            data = get_viewport_image(
                viewport_name=command.get('viewport_name'),
                width=command.get('width', 800),
                height=command.get('height', 600),
                fmt=command.get('format', 'PNG'),
            )
            result['success'] = data.get('success', False)
            result['data'] = data
        elif cmd_type == 'get_message_log':
            result['success'] = True
            result['data'] = 'Log not available'
        elif cmd_type == 'ping':
            result['success'] = True
            result['data'] = {'response': 'pong', 'version': __version__}
        elif cmd_type == 'stop':
            result['success'] = True
            result['data'] = 'stopping'
            _write_text(STOP_FILE, 'stop')
        else:
            result['error'] = 'Unknown command: ' + cmd_type
    except Exception as e:
        result['error'] = str(e)
        result['traceback'] = traceback.format_exc()
        _log('ERROR', 'process_command(%s): %s' % (cmd_type, str(e)))

    return result


# ---------------------------------------------------------------------------
# Polling engine
# ---------------------------------------------------------------------------

def _load_command_file(cmd_path, retries=3, delay=0.03):
    """Retry reads briefly to tolerate partially-written command files."""
    for _ in range(retries):
        try:
            with io.open(cmd_path, 'r', encoding='utf-8-sig') as f:
                return json.load(f)
        except Exception:
            time.sleep(delay)
    return None


_mcp_running = False
_mcp_thread = None
_mcp_generation = 0
_mcp_poll_interval = 0.1
_mcp_last_status_time = 0.0
_mcp_commands_processed = 0
_mcp_start_time = 0.0


def poll_once():
    """Process a single command. Returns True if one command was processed."""
    global _mcp_last_status_time, _mcp_commands_processed
    if not _mcp_running:
        return False

    now = time.time()
    if now - _mcp_last_status_time >= 2.0:
        uptime = int(now - _mcp_start_time) if _mcp_start_time else 0
        write_status('running', 'Polling active | cmds=%d uptime=%ds' % (_mcp_commands_processed, uptime))
        _mcp_last_status_time = now

    try:
        cmd_files = [name for name in os.listdir(COMMANDS_DIR) if name.endswith('.json')]
        if not cmd_files:
            return False

        cmd_files.sort()
        cmd_file = cmd_files[0]
        cmd_path = os.path.join(COMMANDS_DIR, cmd_file)

        command = _load_command_file(cmd_path)
        if command is None:
            return False

        cmd_id = command.get('id', 'unknown')
        cmd_type = command.get('type', 'unknown')

        try:
            os.remove(cmd_path)
        except Exception:
            pass

        result = process_command(command)
        _mcp_commands_processed += 1

        result_path = os.path.join(RESULTS_DIR, cmd_id + '.json')
        _write_json(result_path, result)

        if cmd_type != 'ping':
            status = 'OK' if result.get('success') else 'FAIL'
            print('MCP: ' + cmd_type + ' [' + status + ']')
            _log('INFO', '%s [%s] id=%s' % (cmd_type, status, cmd_id))

        return True
    except Exception as e:
        print('MCP: Error: ' + str(e))
        _log('ERROR', 'poll_once: ' + str(e))
        return False


# ---------------------------------------------------------------------------
# Start / stop helpers
# ---------------------------------------------------------------------------

def _set_thread_daemon(thread_obj):
    try:
        thread_obj.daemon = True
    except Exception:
        try:
            thread_obj.setDaemon(True)
        except Exception:
            pass
    return thread_obj


def _thread_is_alive(thread_obj):
    if thread_obj is None:
        return False
    try:
        return thread_obj.is_alive()
    except Exception:
        try:
            return thread_obj.isAlive()
        except Exception:
            return False


def _mcp_thread_loop(generation, poll_interval):
    """Background polling loop used by non-blocking start modes."""
    global _mcp_running, _mcp_thread, _mcp_generation
    last_status_time = 0.0
    cleanup_time = 0.0

    try:
        while _mcp_running and _mcp_generation == generation:
            now = time.time()
            if now - last_status_time >= 2.0:
                uptime = int(now - _mcp_start_time) if _mcp_start_time else 0
                write_status('running', 'Polling active (background) | cmds=%d uptime=%ds' % (_mcp_commands_processed, uptime))
                last_status_time = now

            if now - cleanup_time >= 30.0:
                _cleanup_stale_commands()
                cleanup_time = now

            if os.path.exists(STOP_FILE):
                try:
                    os.remove(STOP_FILE)
                except Exception:
                    pass
                _mcp_running = False
                print('MCP: Stopped by stop.flag')
                _log('INFO', 'Stopped by stop.flag')
                break

            poll_once()
            time.sleep(poll_interval)
    except Exception as e:
        err_path = os.path.join(MCP_HOME, 'thread_error.log')
        try:
            _write_text(err_path, str(e) + '\n\n' + traceback.format_exc())
        except Exception:
            pass
        print('MCP: Background worker error: ' + str(e))
        _log('ERROR', 'Background worker: ' + str(e))
    finally:
        if _mcp_generation == generation:
            _mcp_running = False
            _mcp_thread = None
            write_status('stopped', 'Polling stopped')
            print('MCP: Background loop ended')
            _log('INFO', 'Background loop ended')


def _start_worker(interval=0.1, mode_name='background'):
    global _mcp_running, _mcp_thread, _mcp_generation, _mcp_poll_interval
    global _mcp_commands_processed, _mcp_start_time

    if _thread_is_alive(_mcp_thread):
        print('MCP: Already running')
        return True

    if _mcp_running:
        print('MCP: Recovering from stale running state')
        _mcp_running = False

    if os.path.exists(STOP_FILE):
        try:
            os.remove(STOP_FILE)
        except Exception:
            pass

    try:
        _mcp_poll_interval = max(0.02, float(interval))
    except Exception:
        _mcp_poll_interval = 0.1

    _mcp_generation += 1
    generation = _mcp_generation
    _mcp_running = True
    _mcp_commands_processed = 0
    _mcp_start_time = time.time()

    print('MCP: Starting in ' + mode_name + ' worker...')
    print('MCP: Use mcp_stop() or Plug-ins -> MCP -> Stop MCP to stop')
    print('MCP: Abaqus GUI remains responsive!')

    try:
        worker = threading.Thread(target=_mcp_thread_loop, args=(generation, _mcp_poll_interval))
        _set_thread_daemon(worker)
        worker.start()
        _mcp_thread = worker
    except Exception as e:
        _mcp_running = False
        _mcp_thread = None
        write_status('error', 'Background start failed: ' + str(e))
        print('MCP: Failed to start background worker: ' + str(e))
        _log('ERROR', 'Failed to start: ' + str(e))
        return False

    time.sleep(0.05)
    if not _thread_is_alive(_mcp_thread):
        _mcp_running = False
        _mcp_thread = None
        write_status('error', 'Background worker exited during startup')
        print('MCP: Background worker exited during startup')
        return False

    write_status('running', 'Polling active (' + mode_name + ')')
    print('MCP: Background worker started (interval=' + str(_mcp_poll_interval) + 's)')
    _log('INFO', 'Started in ' + mode_name + ' mode')
    return True


def mcp_start(interval=0.1):
    """Start background thread polling (experimental on some Abaqus builds)."""
    global _mcp_running, _mcp_poll_interval

    if _mcp_running:
        print('MCP: Already running')
        return

    if os.path.exists(STOP_FILE):
        try:
            os.remove(STOP_FILE)
        except Exception:
            pass

    _mcp_poll_interval = max(0.02, float(interval))
    ok = _start_worker(interval=interval, mode_name='background')
    if not ok:
        return

    # Validate background mode really processes file IPC.
    if not _background_self_test(timeout=1.5):
        _log('WARN', 'Background mode self-test failed; recommend mcp_loop()')
        print('MCP: Background mode did not pass self-test in this Abaqus session.')
        print('MCP: Recommended stable mode: mcp_loop()')
        print('MCP: You can stop current mode with mcp_stop().')
    else:
        print('MCP: Background mode self-test passed.')


def mcp_start_timer(interval=0.1):
    """Compatibility alias for previous timer mode."""
    _start_worker(interval=interval, mode_name='timer-compatible')


def mcp_stop():
    """Stop mcp_loop() or mcp_start()."""
    global _mcp_running, _mcp_thread, _mcp_generation

    _mcp_running = False
    _mcp_generation += 1

    try:
        _write_text(STOP_FILE, 'stop')
    except Exception:
        pass

    if _thread_is_alive(_mcp_thread):
        try:
            _mcp_thread.join(1.0)
        except Exception:
            pass

    _mcp_thread = None
    write_status('stopped', 'Polling stopped')
    print('MCP: Stop signal sent')
    _log('INFO', 'Stop signal sent')


def mcp_loop(sleep_interval=0.1):
    """Blocking loop that continuously processes MCP commands."""
    global _mcp_running, _mcp_commands_processed, _mcp_start_time
    if os.path.exists(STOP_FILE):
        try:
            os.remove(STOP_FILE)
        except Exception:
            pass

    _mcp_running = True
    _mcp_commands_processed = 0
    _mcp_start_time = time.time()

    print('MCP: Listening for commands...')
    print('MCP: To stop, run in PowerShell:')
    print('     echo $null > "' + STOP_FILE + '"')
    print('')

    write_status('running', 'Polling active (blocking)')
    _log('INFO', 'Started in blocking mode')
    last_status_time = 0.0
    cleanup_time = 0.0

    try:
        while True:
            now = time.time()
            if now - last_status_time >= 2.0:
                write_status('running', 'Polling active (blocking)')
                last_status_time = now

            if now - cleanup_time >= 30.0:
                _cleanup_stale_commands()
                cleanup_time = now

            if os.path.exists(STOP_FILE):
                try:
                    os.remove(STOP_FILE)
                except Exception:
                    pass
                print('MCP: Stopped by stop.flag')
                break

            poll_once()
            time.sleep(max(0.02, float(sleep_interval)))
    except KeyboardInterrupt:
        print('\nMCP: Stopped by Ctrl+C')
    except Exception as e:
        print('MCP: Error: ' + str(e))
        _log('ERROR', 'mcp_loop: ' + str(e))

    write_status('stopped', 'Polling stopped')
    print('MCP: Loop ended')
    _log('INFO', 'Blocking loop ended')


def mcp_coop_loop(sleep_interval=0.1):
    """Cooperative loop: runs in current thread but yields GUI updates."""
    global _mcp_running, _mcp_commands_processed, _mcp_start_time
    if os.path.exists(STOP_FILE):
        try:
            os.remove(STOP_FILE)
        except Exception:
            pass

    _mcp_running = True
    _mcp_commands_processed = 0
    _mcp_start_time = time.time()

    print('MCP: Listening for commands... (cooperative mode)')
    print('MCP: To stop, run mcp_stop() or create stop.flag')
    write_status('running', 'Polling active (cooperative)')
    _log('INFO', 'Started in cooperative mode')
    last_status_time = 0.0
    cleanup_time = 0.0

    try:
        while True:
            now = time.time()
            if now - last_status_time >= 2.0:
                write_status('running', 'Polling active (cooperative)')
                last_status_time = now

            if now - cleanup_time >= 30.0:
                _cleanup_stale_commands()
                cleanup_time = now

            if os.path.exists(STOP_FILE):
                try:
                    os.remove(STOP_FILE)
                except Exception:
                    pass
                print('MCP: Stopped by stop.flag')
                break

            poll_once()

            try:
                if ABAQUS_AVAILABLE:
                    session.processUpdates()
            except Exception:
                pass

            time.sleep(max(0.02, float(sleep_interval)))
    except KeyboardInterrupt:
        print('\nMCP: Stopped by Ctrl+C')
    except Exception as e:
        print('MCP: Error: ' + str(e))
        _log('ERROR', 'mcp_coop_loop: ' + str(e))

    write_status('stopped', 'Polling stopped')
    print('MCP: Cooperative loop ended')
    _log('INFO', 'Cooperative loop ended')


def mcp_status():
    """Print current MCP status."""
    print('')
    print('=' * 55)
    print('Abaqus MCP Plugin v' + __version__)
    print('=' * 55)
    print('Mode:         File IPC')
    print('Home:         ' + MCP_HOME)
    print('Running:      ' + str(_mcp_running))
    print('Commands dir: ' + COMMANDS_DIR)
    print('Results dir:  ' + RESULTS_DIR)
    print('Processed:    ' + str(_mcp_commands_processed))
    if _mcp_start_time:
        print('Uptime:       ' + str(int(time.time() - _mcp_start_time)) + 's')
    print('')
    print('Commands:')
    print('  mcp_start()        - Non-blocking background (experimental)')
    print('  mcp_start_timer()  - Alias of mcp_start()')
    print('  mcp_coop_loop()    - Cooperative loop (GUI-friendly)')
    print('  mcp_loop()         - Blocking mode')
    print('  poll_once()        - Process one command')
    print('  mcp_status()       - Show this status')
    print('  mcp_stop()         - Stop polling')
    print('=' * 55)


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

ensure_dirs()
write_status('ready', 'Plugin loaded v' + __version__)

print('')
print('=' * 55)
print('Abaqus MCP Plugin v' + __version__ + ' (File IPC)')
print('=' * 55)
print('Home:   ' + MCP_HOME)
print('Abaqus: ' + str(ABAQUS_AVAILABLE))
print('')
print('Start:  mcp_start()     (background, recommended)')
print('        mcp_loop()      (blocking)')
print('Stop:   mcp_stop()')
print('Status: mcp_status()')
print('=' * 55)
_log('INFO', 'Plugin loaded v' + __version__)
