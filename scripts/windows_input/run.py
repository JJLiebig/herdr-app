"""Isolated herdr-app -> core -> console qualification. MatrixOnly is inert."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import struct
import subprocess as S
import threading
import time
import uuid

from report import CASES, MODES, assess, negotiations

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
NO_WINDOW = 0x08000000


def write(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    # Windows readers/antivirus can briefly deny replacement of an open file.
    for attempt in range(100):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(.01)


def read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return None


def command(argv, env=None, timeout=15):
    p = S.run([str(x) for x in argv], env=env, capture_output=True, timeout=timeout,
              creationflags=NO_WINDOW)
    if p.returncode:
        raise RuntimeError(f'{argv}: {p.stderr.decode(errors="replace")} {p.stdout.decode(errors="replace")}')
    return p.stdout.decode('utf-8-sig').strip()


def identity(path):
    path = Path(path).resolve(strict=True)
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


class Viewer:
    """Existing hc framed protocol; attach before opening the observer gate."""
    def __init__(self, core, env, terminal, observe=True):
        self.p = S.Popen([str(core), 'connect'], env=env, stdin=S.PIPE, stdout=S.PIPE,
                         stderr=S.DEVNULL, creationflags=NO_WINDOW)
        self.lock = threading.Lock()
        self.data = bytearray()
        self.q = queue.Queue()
        self.error = None
        self.p.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"hello","params":{"framed":true}}\n')
        self.p.stdin.flush()
        threading.Thread(target=self.reader, daemon=True).start()
        try:
            self.hello = self.q.get(timeout=10)
            self.send(0, 0, json.dumps(dict(jsonrpc='2.0', id=2, method='terminal.attach',
                      params=dict(terminal=terminal, observe=observe, cols=100, rows=30))).encode())
            reply = self.q.get(timeout=10)
            if 'result' not in reply:
                raise RuntimeError(reply)
            self.channel = reply['result']['channel']
        except BaseException:
            self.close()
            raise

    def exact(self, size):
        data = bytearray()
        while len(data) < size:
            part = self.p.stdout.read(size - len(data))
            if not part:
                raise EOFError('viewer closed')
            data.extend(part)
        return bytes(data)

    def reader(self):
        try:
            self.q.put(json.loads(self.p.stdout.readline()))
            while True:
                channel, kind, size = struct.unpack('<IBI', self.exact(9))
                if size > 4 * 1024 * 1024:
                    raise RuntimeError('oversized viewer frame')
                data = self.exact(size)
                if channel and kind == 0:
                    if len(self.data) + len(data) > 8 * 1024 * 1024:
                        raise RuntimeError('viewer capture limit')
                    self.data.extend(data)
                    self.send(channel, 1, struct.pack('<I', len(data)))
                elif channel == 0:
                    reply = json.loads(data)
                    if reply.get('id') == 2:
                        self.q.put(reply)
        except Exception as exc:
            self.error = str(exc)

    def send(self, channel, kind, data):
        with self.lock:
            self.p.stdin.write(struct.pack('<IBI', channel, kind, len(data)) + data)
            self.p.stdin.flush()

    def close(self):
        if self.p.poll() is None:
            self.p.kill()
        self.p.wait(timeout=5)


class Desktop:
    """No desktop calls until explicit injection authorization has been checked."""
    def __init__(self):
        self.u = C.WinDLL('user32', use_last_error=True)
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.a = C.WinDLL('advapi32', use_last_error=True)
        self.u.GetForegroundWindow.restype = W.HWND
        self.u.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
        self.u.GetKeyboardLayout.argtypes = [W.DWORD]
        self.u.GetKeyboardLayout.restype = W.HANDLE
        self.u.MapVirtualKeyExW.argtypes = [W.UINT, W.UINT, W.HANDLE]
        self.u.SetForegroundWindow.argtypes = [W.HWND]
        self.u.GetAsyncKeyState.argtypes = [C.c_int]
        self.u.GetAsyncKeyState.restype = C.c_short
        self.u.IsWindowVisible.argtypes = [W.HWND]
        self.k.GetCurrentProcess.restype = W.HANDLE
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.a.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
        self.a.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
        token, elevated, size = W.HANDLE(), W.DWORD(), W.DWORD()
        if not self.a.OpenProcessToken(self.k.GetCurrentProcess(), 8, C.byref(token)):
            raise C.WinError(C.get_last_error())
        try:
            if not self.a.GetTokenInformation(token, 20, C.byref(elevated), 4, C.byref(size)):
                raise C.WinError(C.get_last_error())
            if elevated.value:
                raise RuntimeError('Refusing elevated controller')
        finally:
            self.k.CloseHandle(token)
        class Key(C.Structure):
            _fields_ = [('vk', W.WORD), ('scan', W.WORD), ('flags', W.DWORD),
                        ('time', W.DWORD), ('extra', C.c_size_t)]
        class Payload(C.Union):
            _fields_ = [('key', Key), ('padding', C.c_byte * (32 if C.sizeof(C.c_void_p) == 8 else 24))]
        class Input(C.Structure):
            _fields_ = [('type', W.DWORD), ('payload', Payload)]
        self.Key, self.Input = Key, Input
        assert C.sizeof(Input) == (40 if C.sizeof(C.c_void_p) == 8 else 28), 'Wrong Windows INPUT ABI'
        self.u.SendInput.argtypes = [W.UINT, C.POINTER(Input), C.c_int]
        self.abort = threading.Event()
        self.done = threading.Event()
        def watch():
            while not self.done.wait(.01):
                if self.u.GetAsyncKeyState(0x7b) & 0x8001:
                    self.abort.set()
        self.watcher = threading.Thread(target=watch, daemon=True)
        self.watcher.start()

    def pid(self, hwnd):
        pid = W.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
        return pid.value

    def window(self, pid):
        matches = []
        callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        def visit(hwnd, _):
            if self.pid(hwnd) == pid and self.u.IsWindowVisible(hwnd):
                matches.append(hwnd)
            return True
        callback = callback_type(visit)
        self.u.EnumWindows.argtypes = [callback_type, W.LPARAM]
        self.u.EnumWindows(callback, 0)
        if len(matches) > 1:
            raise RuntimeError('Ambiguous test window')
        return matches[0] if matches else None

    def guard(self, hwnd, pid):
        if self.abort.is_set():
            raise RuntimeError('F12 emergency stop')
        if not hwnd or self.pid(hwnd) != pid or self.u.GetForegroundWindow() != hwnd:
            raise RuntimeError('Test window lost foreground focus; no further input sent')

    def chord(self, hwnd, pid, keys):
        self.guard(hwnd, pid)
        for key in (16, 17, 18, 91, 92, 1, 2, 4):
            if self.u.GetAsyncKeyState(key) & 0x8000:
                raise RuntimeError('Physical modifier or mouse button held')
        layout = self.u.GetKeyboardLayout(self.u.GetWindowThreadProcessId(hwnd, None))
        def event(key, up):
            scan = self.u.MapVirtualKeyExW(key, 4, layout)
            if not scan:
                raise RuntimeError(f'No scan code for {key}')
            item = self.Input()
            item.type = 1
            item.payload.key = self.Key(0, scan & 255, 8 | (2 if up else 0) | (1 if scan & 0xff00 else 0), 0, 0)
            return item
        events = [event(k, False) for k in keys] + [event(k, True) for k in reversed(keys)]
        self.guard(hwnd, pid)
        sent = self.u.SendInput(len(events), (self.Input * len(events))(*events), C.sizeof(self.Input))
        if sent != len(events):
            held = [event(k, True) for i, k in enumerate(keys) if i < sent <= 2 * len(keys) - 1 - i]
            if held:
                released = self.u.SendInput(len(held), (self.Input * len(held))(*held), C.sizeof(self.Input))
                if released != len(held):
                    raise RuntimeError('Partial SendInput; key release failed, release modifiers manually')
            raise RuntimeError(f'Partial SendInput {sent}/{len(events)}; run stopped')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app')
    parser.add_argument('--core', required=True)
    parser.add_argument('--core-source', required=True)
    parser.add_argument('--matrix-only', action='store_true')
    parser.add_argument('--probe-only', action='store_true')
    parser.add_argument('--allow-input-injection', action='store_true')
    args = parser.parse_args()
    root = REPO / '.local/windows-input' / (time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
    root.mkdir(parents=True, exist_ok=False)
    report = dict(schema=1, cases=dict(CASES), modes=MODES, observations=[], errors=[], cleanup_errors=[])
    write(root / 'matrix.json', dict(cases=dict(CASES), modes=MODES))
    if args.matrix_only:
        print(f'Matrix: {root / "matrix.json"}')
        return 0
    if os.name != 'nt' or not (args.probe_only or args.allow_input_injection):
        raise RuntimeError('Windows and explicit --allow-input-injection required')
    desktop = None
    core, env = None, None
    started = False
    deadline = time.monotonic() + 240
    def wait_for(fn, seconds=15):
        until = min(deadline, time.monotonic() + seconds)
        while time.monotonic() < until:
            (root / 'lease').touch()
            if desktop and desktop.abort.is_set():
                raise RuntimeError('F12 emergency stop')
            value = fn()
            if value:
                return value
            time.sleep(.05)
        raise TimeoutError('Handshake or total runtime deadline exceeded')
    try:
        if not args.probe_only:
            desktop = Desktop()
        source = Path(args.core_source).resolve(strict=True)
        report['app_source_head'] = command(['git', '-C', REPO, 'rev-parse', 'HEAD'])
        report['app_source_dirty'] = bool(command(['git', '-C', REPO, 'status', '--porcelain']))
        report['core_source_head'] = command(['git', '-C', source, 'rev-parse', 'HEAD'])
        report['core_source_dirty'] = bool(command(['git', '-C', source, 'status', '--porcelain']))
        report['source_note'] = 'Source heads are checkout identities; binary hashes/versions identify the tested builds.'
        core_in = Path(args.core).resolve(strict=True)
        # Stage only the explicit binary and its matching hash-pinned bundle.
        stage = root / 'package'
        (stage / 'conpty/x64').mkdir(parents=True)
        core = stage / 'hc.exe'
        shutil.copy2(core_in, core)
        pin = (source / 'scripts/fetch-conpty.ps1').read_text(encoding='utf-8-sig')
        for name, member in [('conpty/conpty.dll', 'runtimes/win-x64/native/conpty.dll'),
                             ('conpty/x64/OpenConsole.exe', 'build/native/runtimes/x64/OpenConsole.exe')]:
            expected = re.search(r'"' + re.escape(member) + r'"\s*=\s*"([0-9a-f]{64})"', pin)
            if not expected:
                raise RuntimeError('Cannot read source ConPTY pin')
            shutil.copy2(core_in.parent / name, stage / name)
            entry = identity(stage / name)
            if entry['sha256'] != expected[1]:
                raise RuntimeError(f'Core bundle does not match source pin: {name}')
            report[name] = entry
        report['core'] = identity(core)
        report['core_original'] = identity(core_in)
        report['core_version'] = command([core, '--version'])
        report['os'] = command(['cmd.exe', '/d', '/c', 'ver'])
        report['powershell'] = command(['pwsh', '-NoProfile', '-Command', '$PSVersionTable.PSVersion.ToString()'])
        report['conpty_version'] = re.search(r'\$version\s*=\s*"([^"]+)"', pin)[1]
        if not args.probe_only:
            app = Path(args.app).resolve(strict=True)
            report['app'] = identity(app)
            report['app_version'] = command([app, '--version'])
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('HC_', 'HERDR_', 'XDG_'))}
        env.update(HC_SESSION='gauntlet-' + uuid.uuid4().hex[:12],
                   HERDR_APP_HOME=str(root / 'app'), XDG_CONFIG_HOME=str(root / 'config'),
                   XDG_STATE_HOME=str(root / 'state'), XDG_DATA_HOME=str(root / 'data'),
                   XDG_CACHE_HOME=str(root / 'cache'), TERM='xterm-256color', TERM_PROGRAM='herdr',
                   PATH=str(stage) + os.pathsep + env['PATH'])
        report['session'] = env['HC_SESSION']
        app_home = root / 'app'
        app_home.mkdir()
        write(app_home / 'settings.json', dict(core=str(core), telemetry=False, clipboard_write=False))
        # Fresh homes otherwise show onboarding/what's-new over the terminal.
        write(app_home / 'shared.json', {'frame.welcomed': 'skipped'})
        versions = re.findall(r'^## \[([^]]+)\]', (REPO / 'CHANGELOG.md').read_text(encoding='utf-8'), re.M)
        write(app_home / 'update-notes.json', dict(last_run=versions[0] if versions else '0.0.0', presented=versions))
        started = True
        command([core, 'server', 'start'], env)
        for mode in MODES:
            work = root / mode
            work.mkdir()
            nonce = uuid.uuid4().hex
            plan = dict(work=str(work), root=str(root), mode=mode, nonce=nonce)
            write(work / 'plan.json', plan)
            # Delay observer output until both viewers are attached (no missing negotiation).
            gate = work / 'go'
            def quote(text):
                return "'" + str(text).replace("'", "''") + "'"
            bootstrap = work / 'bootstrap.ps1'
            bootstrap.write_text(f"$limit = [DateTime]::UtcNow.AddSeconds(25)\nwhile (-not (Test-Path -LiteralPath {quote(gate)})) {{ if ([DateTime]::UtcNow -gt $limit) {{ exit 2 }}; Start-Sleep -Milliseconds 50 }}\n& {quote(HERE / 'Probe.ps1')} -PlanPath {quote(work / 'plan.json')}\n", encoding='utf-8')
            argv = ['pwsh', '-NoLogo', '-NoProfile', '-File', str(bootstrap)]
            proc, viewer, terminal = None, None, None
            try:
                (root / 'lease').touch()
                if args.probe_only:
                    result = json.loads(command([core, 'run', '--bg', '--no-parent', '--no-notify', *argv], env))
                    terminal = result['address']
                else:
                    proc = S.Popen([str(app)], env=env, creationflags=NO_WINDOW)
                    wait_for(lambda: (app_home / 'AGENTS.md').exists())
                    # status also confirms the app command socket is accepting calls.
                    def ready_app():
                        try:
                            return command([app, 'status'], env, timeout=2)
                        except (RuntimeError, S.TimeoutExpired):
                            return None
                    wait_for(ready_app)
                    result = json.loads(command([app, 'do', 'space.new', json.dumps(dict(name='input-gauntlet', folder=str(work), argv=argv))], env))
                    if 'error' in result:
                        raise RuntimeError(result)
                    terminal = result['terminal']
                    command([app, 'do', 'pane.focus', json.dumps(dict(terminal=terminal))], env)
                viewer = Viewer(core, env, terminal, observe=not args.probe_only)
                hwnd = None
                if proc:
                    hwnd = wait_for(lambda: desktop.window(proc.pid))
                    # Focus once during setup. Later focus loss aborts; never refocus.
                    desktop.u.SetForegroundWindow(hwnd)
                    wait_for(lambda: desktop.u.GetForegroundWindow() == hwnd, seconds=3)
                    desktop.guard(hwnd, proc.pid)
                    # The app attaches asynchronously after the command reply.
                    time.sleep(.5)
                gate.touch()
                ready = wait_for(lambda: read(work / 'ready.json'))
                if ready['nonce'] != nonce or ready['mode'] != mode or min(ready['geometry'][:2]) <= 0:
                    raise RuntimeError('Observer handshake mismatch')
                if mode.startswith('kitty') and not ready['kitty_acknowledged']:
                    raise RuntimeError('Kitty negotiation not acknowledged')
                wait_for(lambda: b'\x1b[?9001h' in viewer.data)
                if viewer.error:
                    raise RuntimeError(viewer.error)
                run = dict(mode=mode, handshake=ready, terminal=terminal, hwnd=hwnd,
                           app_pid=proc.pid if proc else None, cases=[])
                report['observations'].append(run)
                runtime_path = str(stage / 'conpty/x64/OpenConsole.exe').replace("'", "''")
                run['openconsole_processes'] = json.loads(command(['pwsh', '-NoProfile', '-Command',
                    f"ConvertTo-Json -InputObject @(Get-CimInstance Win32_Process -Filter \"Name='OpenConsole.exe'\" | Where-Object {{ $_.ExecutablePath -eq '{runtime_path}' }} | Select-Object ProcessId,ExecutablePath)"], env))
                if not run['openconsole_processes']:
                    raise RuntimeError('No running OpenConsole from the staged bundle')
                def request(action):
                    rid = uuid.uuid4().hex
                    write(work / 'request.json', dict(id=rid, nonce=nonce, action=action))
                    def fresh():
                        error = read(work / 'probe-error.json')
                        if error:
                            raise RuntimeError(error)
                        reply = read(work / 'reply.json')
                        return reply if reply and reply['id'] == rid and reply['nonce'] == nonce else None
                    reply = wait_for(fresh, seconds=5)
                    if reply['error']:
                        raise RuntimeError(reply['error'])
                    return reply
                for name, gestures in CASES:
                    if args.probe_only:
                        break
                    request('begin')
                    for chord in gestures:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Total runtime exceeded')
                        desktop.chord(hwnd, proc.pid, chord)
                        # Rapid sequential physical gestures, not one coalesced OS batch.
                        time.sleep(.015)
                    capture = request('end')
                    problem = assess(mode, gestures, capture)
                    run['cases'].append(dict(case=name, status='fail' if problem else 'pass', reason=problem, capture=capture))
                if args.probe_only:
                    controls = []
                    if mode == 'native':
                        controls = [('coalesced-old', [b'\x1b\x03'], False),
                                    ('delayed-old', [b'\x1b', b'\x03'], True),
                                    ('win32-fixed', [b'\x1b[27;1;27;1;0;1_\x1b[27;1;27;0;0;1_\x03'], True)]
                    elif mode.startswith('kitty'):
                        controls = [('kitty-bytes', [b'\x1b[27u\x1b[99;5u'], True)]
                    run['controls'] = []
                    for name, chunks, expected_pass in controls:
                        request('begin')
                        for chunk in chunks:
                            viewer.send(viewer.channel, 0, chunk)
                            if len(chunks) > 1:
                                time.sleep(.2)
                        capture = request('end')
                        problem = assess(mode, [[27], [17, 67]], capture)
                        passed = (problem is None) == expected_pass
                        if name == 'coalesced-old':
                            passed = passed and any(r[0] == 1 and r[1] and r[3] == 3 and r[6] & 3 for r in capture['records'])
                        run['controls'].append(dict(case=name, passed=passed, expected_semantic_pass=expected_pass,
                                                    reason=problem, capture=capture))
                        if not passed:
                            raise RuntimeError(f'Core byte control failed: {name}: {problem}')
                    if mode == 'legacy':
                        for name, gestures, data in [
                            ('shift-enter', [[16, 13]], b'\x1b[13;28;13;1;16;1_\x1b[13;28;13;0;16;1_'),
                            ('ctrl-enter', [[17, 13]], b'\x1b[13;28;10;1;8;1_\x1b[13;28;10;0;8;1_'),
                            ('alt-enter', [[18, 13]], b'\x1b[13;28;13;1;2;1_\x1b[13;28;13;0;2;1_')]:
                            request('begin')
                            viewer.send(viewer.channel, 0, data)
                            capture = request('end')
                            problem = assess(mode, gestures, capture)
                            run['controls'].append(dict(case=name, passed=problem is None, reason=problem, capture=capture))
                            if problem:
                                raise RuntimeError(f'Core byte control failed: {name}: {problem}')
                (work / 'probe-stop').touch()
                exited = wait_for(lambda: read(work / 'probe-exit.json'), seconds=5)
                if exited['cleanup_errors']:
                    raise RuntimeError(exited['cleanup_errors'])
                run['negotiated_modes'] = negotiations(viewer.data)
                (work / 'viewer.bin').write_bytes(viewer.data)
            finally:
                if viewer:
                    try:
                        viewer.close()
                    except Exception as exc:
                        report['cleanup_errors'].append(f'viewer cleanup: {exc}')
                if proc:
                    try:
                        proc.terminate()
                        proc.wait(timeout=5)
                    except Exception as exc:
                        report['cleanup_errors'].append(f'app cleanup: {exc}')
                if terminal:
                    try:
                        command([core, 'kill', terminal], env)
                    except Exception as exc:
                        report['cleanup_errors'].append(f'terminal cleanup: {exc}')
                if viewer:
                    (work / 'viewer.bin').write_bytes(bytes(viewer.data))
    except Exception as exc:
        report['errors'].append(str(exc))
    finally:
        if desktop:
            desktop.done.set()
            desktop.watcher.join(timeout=1)
        if started:
            try:
                command([core, 'server', 'stop'], env)
            except Exception as exc:
                report['cleanup_errors'].append(str(exc))
        report['kind'] = 'handshakes and core byte controls; no desktop input' if args.probe_only else 'desktop'
        lines = [f"{r['mode']}/{c['case']}: {c['status']}" + (f" ({c['reason']})" if c['reason'] else '')
                 for r in report['observations'] for c in r['cases']]
        expected_count = 0 if args.probe_only else len(MODES) * len(CASES)
        report_failed = bool(report['errors'] or report['cleanup_errors'] or len(lines) != expected_count or
                             len(report['observations']) != len(MODES) or any(': fail' in line for line in lines))
        report['status'] = 'fail' if report_failed else 'pass'
        write(root / 'report.json', report)
        if not args.probe_only:
            completed = {(r['mode'], c['case']) for r in report['observations'] for c in r['cases']}
            lines.extend(f'{mode}/{name}: not_run' for mode in MODES for name, _ in CASES if (mode, name) not in completed)
        summary = '\n'.join(['FAIL' if report_failed else ('HANDSHAKES AND BYTE CONTROLS PASSED (desktop input not run)' if args.probe_only else 'PASS'),
                            *lines, *report['errors'], *report['cleanup_errors']]) + '\n'
        (root / 'summary.txt').write_text(summary, encoding='utf-8')
        print(summary, end='')
        print(f'Report: {root / "report.json"}')
    return int(report_failed)


if __name__ == '__main__':
    raise SystemExit(main())
