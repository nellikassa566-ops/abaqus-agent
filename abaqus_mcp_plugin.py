# -*- coding: utf-8 -*-
"""
Abaqus MCP Plugin v3.3 - file IPC bridge.

Usage:
1. File -> Run Script... -> choose this file
2. Run mcp_start() for non-blocking background mode (recommended)
3. Run mcp_loop() for blocking mode
4. Run mcp_stop() to stop
"""

import io
import json
import os
import threading
import time
import traceback
from datetime import datetime

try:
    from abaqus import mdb, session
    ABAQUS_AVAILABLE = True
except ImportError:
    ABAQUS_AVAILABLE = False

def _resolve_mcp_home():
    """Resolve MCP home with explicit override support."""
    env_home = os.environ.get('ABAQUS_MCP_HOME', '').strip()
    if env_home:
        return os.path.abspath(os.path.expanduser(env_home))

    # When possible, prefer folder of current plugin file.
    try:
        this_file = os.path.abspath(__file__)
        script_dir = os.path.dirname(this_file)
        if os.path.exists(os.path.join(script_dir, 'stop_mcp.py')):
            return script_dir
    except Exception:
        pass

    return os.path.join(os.path.expanduser('~'), '.abaqus-mcp')


MCP_HOME = _resolve_mcp_home()
COMMANDS_DIR = os.path.join(MCP_HOME, 'commands')
RESULTS_DIR = os.path.join(MCP_HOME, 'results')
SCRIPTS_DIR = os.path.join(MCP_HOME, 'scripts')
STATUS_FILE = os.path.join(MCP_HOME, 'status.json')
STOP_FILE = os.path.join(MCP_HOME, 'stop.flag')


def ensure_dirs():
    for d in [COMMANDS_DIR, RESULTS_DIR, SCRIPTS_DIR]:
        if not os.path.exists(d):
            os.makedirs(d)


def write_status(status, message=""):
    """Write status atomically so external readers never see partial JSON."""
    payload = {
        "status": status,
        "message": message,
        "timestamp": time.time(),
        "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pid": os.getpid(),
    }

    tmp_file = STATUS_FILE + '.tmp'
    try:
        with io.open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)

        for _ in range(5):
            try:
                os.replace(tmp_file, STATUS_FILE)
                return
            except Exception:
                time.sleep(0.02)

        with io.open(STATUS_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
        try:
            os.remove(tmp_file)
        except Exception:
            pass
    except Exception:
        pass


def _write_json(path, data):
    with io.open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


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
        with io.open(script_path, 'w', encoding='utf-8') as f:
            f.write(script_content)
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
            info['models'].append({
                'name': name,
                'parts': list(model_obj.parts.keys()) if hasattr(model_obj, 'parts') else [],
                'materials': list(model_obj.materials.keys()) if hasattr(model_obj, 'materials') else [],
                'steps': list(model_obj.steps.keys()) if hasattr(model_obj, 'steps') else [],
            })
        if hasattr(session, 'viewports'):
            info['current_viewport'] = session.currentViewportName
    except Exception as e:
        info['error'] = str(e)
    return info


def process_command(command):
    cmd_id = command.get('id', 'unknown')
    cmd_type = command.get('type', 'unknown')
    result = {'id': cmd_id, 'success': False, 'timestamp': time.time()}

    try:
        if cmd_type == 'execute_script':
            result = execute_script(command.get('script', ''), cmd_id)
        elif cmd_type == 'get_model_info':
            result['success'] = True
            result['data'] = get_model_info()
        elif cmd_type == 'get_message_log':
            result['success'] = True
            result['data'] = 'Log not available'
        elif cmd_type == 'ping':
            result['success'] = True
            result['data'] = 'pong'
        elif cmd_type == 'stop':
            result['success'] = True
            result['data'] = 'stopping'
            with io.open(STOP_FILE, 'w', encoding='utf-8') as f:
                f.write('stop')
        else:
            result['error'] = 'Unknown command: ' + cmd_type
    except Exception as e:
        result['error'] = str(e)

    return result


def _load_command_file(cmd_path, retries=3, delay=0.03):
    """Retry reads briefly to tolerate partially-written command files."""
    for _ in range(retries):
        try:
            # utf-8-sig also handles UTF-8 files with BOM (EF BB BF).
            with io.open(cmd_path, 'r', encoding='utf-8-sig') as f:
                return json.load(f)
        except Exception:
            time.sleep(delay)
    return None


def poll_once():
    """Process a single command. Returns True if one command was processed."""
    global _mcp_last_status_time
    if not _mcp_running:
        return False
    now = time.time()
    if now - _mcp_last_status_time >= 2.0:
        write_status('running', 'Polling active (GUI after-timer)')
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

        result_path = os.path.join(RESULTS_DIR, cmd_id + '.json')
        _write_json(result_path, result)

        if cmd_type != 'ping':
            status = 'OK' if result.get('success') else 'FAIL'
            print('MCP: ' + cmd_type + ' [' + status + ']')

        return True
    except Exception as e:
        print('MCP: Error: ' + str(e))
        return False


_mcp_running = False
_mcp_thread = None
_mcp_generation = 0
_mcp_poll_interval = 0.1
_mcp_last_status_time = 0.0


def _set_thread_daemon(thread_obj):
    """Set daemon flag with Python 2/3 compatibility."""
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

    try:
        while _mcp_running and _mcp_generation == generation:
            now = time.time()
            if now - last_status_time >= 2.0:
                write_status('running', 'Polling active (background)')
                last_status_time = now

            if os.path.exists(STOP_FILE):
                try:
                    os.remove(STOP_FILE)
                except Exception:
                    pass
                _mcp_running = False
                print('MCP: Stopped by stop.flag')
                break

            poll_once()
            time.sleep(poll_interval)
    except Exception as e:
        err_path = os.path.join(MCP_HOME, 'thread_error.log')
        try:
            with io.open(err_path, 'w', encoding='utf-8') as f:
                f.write(str(e) + '\n\n')
                f.write(traceback.format_exc())
        except Exception:
            pass
        print('MCP: Background worker error: ' + str(e))
    finally:
        if _mcp_generation == generation:
            _mcp_running = False
            _mcp_thread = None
            write_status('stopped', 'Polling stopped')
            print('MCP: Background loop ended')


def _start_worker(interval=0.1, mode_name='background'):
    global _mcp_running, _mcp_thread, _mcp_generation, _mcp_poll_interval

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
        return False

    # Self-check to avoid stale "running" state when worker dies immediately.
    time.sleep(0.05)
    if not _thread_is_alive(_mcp_thread):
        _mcp_running = False
        _mcp_thread = None
        write_status('error', 'Background worker exited during startup')
        print('MCP: Background worker exited during startup')
        return False

    write_status('running', 'Polling active (' + mode_name + ')')
    print('MCP: Background worker started (interval=' + str(_mcp_poll_interval) + 's)')
    return True


def mcp_start(interval=0.1):
    """Start background thread polling + enables GUI after-timer if available."""
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

    # Start background thread (handles ping and simple commands)
    # GUI plugin's after()-timer will handle execute_script on main thread
    _start_worker(interval=interval, mode_name='background')


def mcp_start_timer(interval=0.1):
    """Compatibility alias for previous timer mode."""
    _start_worker(interval=interval, mode_name='timer-compatible')


def mcp_stop():
    """Stop mcp_loop() or mcp_start()."""
    global _mcp_running, _mcp_thread, _mcp_generation

    _mcp_running = False
    _mcp_generation += 1

    try:
        with io.open(STOP_FILE, 'w', encoding='utf-8') as f:
            f.write('stop')
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


def mcp_loop(sleep_interval=0.1):
    """Blocking loop that continuously processes MCP commands."""
    global _mcp_running
    if os.path.exists(STOP_FILE):
        try:
            os.remove(STOP_FILE)
        except Exception:
            pass

    _mcp_running = True
    print('MCP: Listening for commands...')
    print('MCP: To stop, run in PowerShell:')
    print('     echo $null > "$env:USERPROFILE\\.abaqus-mcp\\stop.flag"')
    print('')

    write_status('running', 'Polling active (blocking)')
    last_status_time = 0.0

    try:
        while True:
            now = time.time()
            if now - last_status_time >= 2.0:
                write_status('running', 'Polling active (blocking)')
                last_status_time = now

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

    write_status('stopped', 'Polling stopped')
    print('MCP: Loop ended')


def mcp_coop_loop(sleep_interval=0.1):
    """
    Cooperative loop.

    This still runs a loop in the current thread, but yields GUI updates,
    so Abaqus remains responsive compared to mcp_loop().
    """
    global _mcp_running
    if os.path.exists(STOP_FILE):
        try:
            os.remove(STOP_FILE)
        except Exception:
            pass

    _mcp_running = True
    print('MCP: Listening for commands... (cooperative mode)')
    print('MCP: To stop, run mcp_stop() or create stop.flag')
    write_status('running', 'Polling active (cooperative)')
    last_status_time = 0.0

    try:
        while True:
            now = time.time()
            if now - last_status_time >= 2.0:
                write_status('running', 'Polling active (cooperative)')
                last_status_time = now

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

    write_status('stopped', 'Polling stopped')
    print('MCP: Cooperative loop ended')


def mcp_status():
    print('')
    print('=' * 50)
    print('MCP Status')
    print('=' * 50)
    print('Mode: File IPC')
    print('Commands dir: ' + COMMANDS_DIR)
    print('Results dir: ' + RESULTS_DIR)
    print('Stop file: ' + STOP_FILE)
    print('')
    print('Usage:')
    print('  mcp_start()        - Non-blocking background mode')
    print('  mcp_start_timer()  - Alias of mcp_start()')
    print('  mcp_coop_loop()    - Cooperative loop (GUI-friendly)')
    print('  mcp_loop()         - Blocking mode')
    print('  poll_once()        - Process one command')
    print('')
    print('To stop, run mcp_stop() or:')
    print('  echo $null > "$env:USERPROFILE\\.abaqus-mcp\\stop.flag"')
    print('=' * 50)


ensure_dirs()
write_status('ready', 'Plugin loaded')

print('')
print('=' * 50)
print('Abaqus MCP Plugin v3.3 (File IPC)')
print('=' * 50)
print('Home: ' + MCP_HOME)
print('Abaqus: ' + str(ABAQUS_AVAILABLE))
print('')
print('To process MCP commands, run:')
print('  mcp_start()  - Non-blocking background mode')
print('  mcp_loop()   - Blocking mode')
print('  poll_once()  - Process one command')
print('')
print('To stop, run:')
print('  mcp_stop()')
print('or')
print('  echo $null > "$env:USERPROFILE\\.abaqus-mcp\\stop.flag"')
print('=' * 50)
