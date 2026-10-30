"""Owning tests for helper policy; vendor and core compatibility need native probes."""

import copy
import asyncio
from contextlib import nullcontext
import json
import io
from pathlib import Path
import subprocess
import shutil
import signal
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import agent_recovery as helper


class DocumentCore:
    def __init__(self):
        self.version = 0
        self.value = None
        self.fail = False
        self.before_set = None

    def call(self, method, params):
        if method == "doc.get":
            return dict(version=self.version, value=copy.deepcopy(self.value))
        if self.before_set:
            callback, self.before_set = self.before_set, None
            callback()
        if self.fail:
            raise helper.RpcError(dict(message="disk write failed", data=dict(kind="io")))
        if params["version"] != self.version:
            raise helper.RpcError(dict(message="changed", data=dict(kind="conflict")))
        self.value, self.version = copy.deepcopy(params["value"]), self.version + 1
        return dict(version=self.version)


class LaunchCore(DocumentCore):
    capable = True
    native_capable = True
    fail_keep = False
    native_id = None
    kept = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def call(self, method, params):
        if method == "rpc.discover":
            names = ["terminal", "keep", "recovery", "expected_scope"] if self.capable else ["terminal", "keep"]
            properties = dict(resume_session={}) if self.native_capable else {}
            return dict(methods=[dict(name="terminal.keep", params=[dict(name=n) for n in names])],
                        components=dict(schemas=dict(Terminal=dict(properties=properties))))
        if method == "terminal.get":
            return dict(address="t1", state="live", resume_session=self.native_id)
        if method == "terminal.keep":
            if self.fail_keep:
                raise RuntimeError("keep was not saved")
            self.kept = params["keep"]
            if not self.kept:
                self.native_id = None
            return {}
        return super().call(method, params)


class RecoveryTests(unittest.TestCase):
    def test_direct_rpc_reads_large_replies_times_out_and_closes(self):
        transports = []
        class ReplyTransport(asyncio.Transport):
            def __init__(self, protocol):
                self.protocol, self.closed = protocol, False
                transports.append(self)
            def get_extra_info(self, name, default=None):
                return default
            def is_closing(self):
                return self.closed
            def write(self, data):
                request = json.loads(data)
                if request["method"] != "unresponsive":
                    self.protocol.data_received((json.dumps(dict(jsonrpc="2.0", id=request["id"], result="x" * 70000)) + "\n").encode())
            def close(self):
                if not self.closed:
                    self.closed = True
                    self.protocol.connection_lost(None)
        async def pipe(loop, factory, address):
            protocol = factory()
            transport = ReplyTransport(protocol)
            protocol.connection_made(transport)
            return transport, protocol
        async def unix(address, *, limit):
            reader = asyncio.StreamReader(limit=limit)
            protocol = asyncio.StreamReaderProtocol(reader)
            transport = ReplyTransport(protocol)
            protocol.connection_made(transport)
            return reader, asyncio.StreamWriter(transport, protocol, reader, asyncio.get_running_loop())
        connector = patch.object(asyncio.ProactorEventLoop, "create_pipe_connection", new=pipe) if helper.os.name == "nt" else patch.object(asyncio, "open_unix_connection", new=unix)
        with connector, patch.dict(helper.os.environ, HC_TERMINAL_SOCKET="fixture"), patch.object(helper.subprocess, "Popen", side_effect=AssertionError("RPC must not spawn or start a core")):
            with helper.Core() as core:
                self.assertEqual(core.call("large", {}), "x" * 70000)
                core.deadline = time.monotonic() + .02
                with self.assertRaisesRegex(RuntimeError, "did not answer"):
                    core.call("unresponsive", {})
            self.assertTrue(all(t.closed for t in transports))

    def test_missing_endpoint_closes_runner_without_starting_core(self):
        async def absent(*args, **kwargs):
            raise FileNotFoundError("stopped endpoint")
        connector = patch.object(asyncio.ProactorEventLoop, "create_pipe_connection", new=absent) if helper.os.name == "nt" else patch.object(asyncio, "open_unix_connection", new=absent)
        with connector, patch.dict(helper.os.environ, HC_TERMINAL_SOCKET="absent"), patch.object(helper.subprocess, "Popen", side_effect=AssertionError("must not restart stopped core")):
            core = helper.Core()
            with self.assertRaises(FileNotFoundError):
                core.__enter__()
            with self.assertRaisesRegex(RuntimeError, "closed"):
                core.runner.get_loop()


    def test_menu_opt_in_checks_current_local_windows_space(self):
        module = Path(helper.__file__).parents[2] / "defaults/mods/new-menu/main.js"
        code = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
let active = { id: 'local', active: true }, platform = 'windows', configured = true, calls = [], reads = 0;
const agent = { key: 'claude', name: 'Claude', run: ['native-claude'], installed: true };
const cx = { focus_handle: () => ({focus(){}}), spawn: f => tasks.push(f(cx)), notify(){} };
let tasks = [];
let keyHandler, rows = [];
const widget = () => new Proxy({}, {get: (_, key) => (...args) => { if (key === 'on_key_down') keyHandler = args[0]; return widget(); }});
const context = { View: class {}, Set, Promise, spaces: () => [active], window: () => ({platform}),
  theme: () => ({}), div: widget, svg: widget, image: widget, h_flex: widget, v_flex: widget,
  props: () => ({overlay:'menu'}), closeOverlay() {}, panes: () => [], keys: () => [],
  appHome: () => 'C:/Herdr Space', knownAgents: async () => [agent],
  readTextFile: async () => { reads++; return configured ? '{}' : null; }, run: (method, params) => calls.push({method,params}) };
let source = fs.readFileSync(process.argv[1], 'utf8').replace(/^import .*;\r?\n/gm, '').replace('export default class', 'class');
vm.runInNewContext(source + '; globalThis.Menu = NewMenu;', context);
(async () => {
  const menu = new context.Menu(); menu.init({},cx); await Promise.all(tasks);
  menu.startAgent(agent); assert.equal(calls.at(-1).params.argv[0], 'python');
  menu.row = (id, icon, name, action, hint, enabled = true) => { rows.push({id,action,hint,enabled}); return widget(); };
  agent.installed = false; menu.render();
  assert.equal(rows.find(r => r.id === 'claude').enabled, true);
  keyHandler({key:'1',modifiers:{}}); assert.equal(calls.at(-1).params.argv[0], 'python');
  active = { id: 'remote-now', active: true, machine: 'remote' };
  rows = []; menu.render(); assert.equal(rows.find(r => r.id === 'claude').enabled, false);
  const beforeKey = calls.length; keyHandler({key:'1',modifiers:{}}); assert.equal(calls.length,beforeKey);
  agent.installed = true;
  menu.startAgent(agent); assert.equal(calls.at(-1).params.argv[0], 'native-claude');
  assert.equal(calls.at(-1).params.space, 'remote-now');
  active = { id: 'local', active: true }; platform = 'macos';
  menu.startAgent(agent); assert.equal(calls.at(-1).params.argv[0], 'native-claude');
  platform = 'linux'; tasks = []; const beforeReads = reads;
  const linux = new context.Menu(); linux.init({},cx); await Promise.all(tasks);
  assert.equal(reads,beforeReads); linux.startAgent(agent); assert.equal(calls.at(-1).params.argv[0], 'native-claude');
  platform = 'windows'; configured = false; tasks = [];
  const absent = new context.Menu(); absent.init({},cx); await Promise.all(tasks);
  absent.startAgent(agent); assert.equal(calls.at(-1).params.argv[0], 'native-claude');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run(["node", "-e", code, str(module)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_durable_registration_precedes_spawn_and_resume_never_falls_back(self):
        with tempfile.TemporaryDirectory(prefix="recovery launch ") as folder:
            output = Path(folder) / "launched.json"
            vendor = Path(folder) / "vendor.py"
            vendor.write_text("import json,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))")
            config = Path(folder) / "codex.json"
            config.write_text(json.dumps(dict(agent="codex", argv=[sys.executable, str(vendor), str(output), ""])))
            args = SimpleNamespace(action="new", config=str(config), cwd=folder, session_id=None)
            core = LaunchCore()
            core.capable, core.native_capable, core.fail_keep = False, False, True
            with patch.object(helper, "Core", return_value=core), patch.dict(helper.os.environ, HC_TERMINAL_ID="t1", HC_SCOPE="scope"):
                with patch.object(sys, "argv", ["helper", "new", "--config", str(config), "--session-id", str(uuid.uuid4())]), patch.object(sys, "stderr", io.StringIO()):
                    with self.assertRaises(SystemExit) as invalid:
                        helper.main()
                    self.assertEqual(invalid.exception.code, 2)
                self.assertIsNone(core.value)
                with self.assertRaisesRegex(RuntimeError, "does not support"):
                    helper.run(args)
                self.assertIsNone(core.value)
                self.assertFalse(output.exists())
                core.kept = True
                with patch.object(helper, "terminal_lock", side_effect=nullcontext), \
                     patch.dict(helper.os.environ, HC_TERMINAL_SOCKET="fixture"), \
                     patch.object(sys, "argv", ["helper", "new", "--config", str(config)]), \
                     patch.object(sys, "stderr", io.StringIO()), \
                     patch.object(helper, "shell", side_effect=AssertionError("unsupported core must not open a fallback shell")):
                    self.assertEqual(helper.main(), 1)
                self.assertTrue(core.kept, "unsupported core must not receive a cleanup mutation")
                core.capable = True
                with self.assertRaisesRegex(RuntimeError, "native Codex"):
                    helper.run(args)
                self.assertIsNone(core.value)
                core.native_capable = True
                with self.assertRaisesRegex(RuntimeError, "keep was not saved"):
                    helper.run(args)
                self.assertFalse(output.exists())
                self.assertIsNone(core.value)
                core.fail_keep = False
                helper.claim(core, "t1", "codex", folder)
                sid = str(uuid.uuid4())
                core.native_id = sid
                args.action = "recover"
                self.assertEqual(helper.run(args), 0)
                self.assertEqual(json.loads(output.read_text())[-2:], ["resume", sid])
                self.assertIsNone(core.value["session_id"])
                self.assertNotIn("explicit_resume", core.value)
                self.assertEqual(core.native_id, sid)
                # Re-registering recovery never clears the durable native identity.
                self.assertEqual(helper.run(args), 0)
                self.assertEqual(json.loads(output.read_text())[-2:], ["resume", sid])
                vendor.write_text(vendor.read_text() + "\nraise SystemExit(7)\n")
                self.assertEqual(helper.run(args), 7)
                output.unlink()
                core.native_id = None
                core.value["session_id"] = sid
                with self.assertRaisesRegex(RuntimeError, "no main conversation"):
                    helper.run(args)
                self.assertFalse(output.exists())

    def test_explicit_selection_clears_old_binding_and_native_id_supersedes_fallback(self):
        with tempfile.TemporaryDirectory(prefix="native recovery ") as folder:
            output = Path(folder) / "argv.json"
            vendor = Path(folder) / "vendor.py"
            vendor.write_text("import json,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))")
            config = Path(folder) / "codex.json"
            config.write_text(json.dumps(dict(agent="codex", argv=[sys.executable, str(vendor), str(output)])))
            args = SimpleNamespace(action="new", config=str(config), cwd=folder, session_id=None)
            core = LaunchCore()
            core.native_id = str(uuid.uuid4())
            core.kept = True
            core.before_set = lambda: self.assertFalse(core.kept, "old recipe must be cleared before publishing a new claim")
            original_child = helper.child
            observed = []
            published = str(uuid.uuid4())
            def spawn(command, cwd, env):
                observed.append(core.native_id)
                self.assertTrue(core.kept)
                core.native_id = published
                return original_child(command, cwd, env)
            with patch.object(helper, "Core", return_value=core), patch.dict(helper.os.environ, HC_TERMINAL_ID="t1", HC_SCOPE="scope"):
                with patch.object(helper, "child", side_effect=spawn):
                    self.assertEqual(helper.run(args), 0)
                self.assertEqual(observed, [None])
                self.assertEqual(json.loads(output.read_text()), [])
                self.assertEqual(core.native_id, published)
                explicit = str(uuid.uuid4())
                args.action, args.session_id = "resume", explicit
                self.assertEqual(helper.run(args), 0)
                self.assertIsNone(core.native_id)
                self.assertTrue(core.value["explicit_resume"])
                args.action, args.session_id = "recover", None
                self.assertEqual(helper.run(args), 0)
                self.assertEqual(json.loads(output.read_text()), ["resume", explicit])
                # A subsequent root native report is authoritative, never the fallback.
                core.native_id = published
                self.assertEqual(helper.run(args), 0)
                self.assertEqual(json.loads(output.read_text()), ["resume", published])
                self.assertIsNone(core.value["session_id"])
                self.assertNotIn("explicit_resume", core.value)
                core.native_id = "invalid-native-id"
                output.unlink()
                with self.assertRaises(ValueError):
                    helper.run(args)
                self.assertFalse(output.exists())

    def test_claude_chosen_id_is_durable_before_spawn_and_fixed_on_recovery(self):
        with tempfile.TemporaryDirectory(prefix="claude chosen identity ") as folder:
            output = Path(folder) / "argv.json"
            vendor = Path(folder) / "vendor.py"
            vendor.write_text("import json,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))")
            config = Path(folder) / "claude.json"
            baseline = [sys.executable, str(vendor), str(output), "--settings", "private-settings.json", "--plugin-dir", "existing-plugin"]
            config.write_text(json.dumps(dict(agent="claude", argv=baseline)))
            args = SimpleNamespace(action="new", config=str(config), cwd=folder, session_id=None)
            core = LaunchCore()
            core.native_capable = False
            original_child = helper.child
            before = []
            def spawn(command, cwd, env):
                before.append(core.value["session_id"])
                self.assertTrue(core.kept)
                return original_child(command, cwd, env)
            with patch.object(helper, "Core", return_value=core), patch.dict(helper.os.environ, HC_TERMINAL_ID="t1", HC_SCOPE="scope"), patch.object(helper, "child", side_effect=spawn):
                core.fail = True
                with self.assertRaisesRegex(RuntimeError, "disk write failed"):
                    helper.run(args)
                self.assertFalse(output.exists())
                core.fail = False
                self.assertEqual(helper.run(args), 0)
                argv = json.loads(output.read_text())
                chosen = argv[-1]
                uuid.UUID(chosen)
                self.assertEqual(before, [chosen])
                self.assertEqual(argv, baseline[3:] + ["--session-id", chosen])
                args.action = "recover"
                core.native_id = str(uuid.uuid4())
                self.assertEqual(helper.run(args), 0)
                self.assertEqual(json.loads(output.read_text()), baseline[3:] + ["--resume", chosen])
                self.assertEqual(before, [chosen, chosen])
                output.unlink()
                core.value["cwd"] = str(Path(folder) / "missing")
                with self.assertRaisesRegex(RuntimeError, "working directory"):
                    helper.run(args)
                self.assertFalse(output.exists())

    def test_ordinary_exit_finishes_and_failed_launch_disarms_before_shell(self):
        events = []
        with patch.object(sys, "argv", ["helper", "new", "--config", "unused"]), \
             patch.object(helper, "terminal_lock", side_effect=nullcontext), \
             patch.object(helper, "run", return_value=0), \
             patch.object(helper, "disarm", side_effect=lambda: events.append("disarm")), \
             patch.object(helper, "shell", side_effect=lambda: events.append("shell") or 0):
            self.assertEqual(helper.main(), 0)
            self.assertEqual(events, ["disarm"])
            events.clear()
            with patch.object(helper, "run", return_value=7):
                self.assertEqual(helper.main(), 7)
            self.assertEqual(events, ["disarm"])
            events.clear()
            with patch.object(helper, "run", side_effect=KeyboardInterrupt):
                self.assertEqual(helper.main(), 130)
            self.assertEqual(events, ["disarm"])
            events.clear()
            with patch.object(helper, "run", side_effect=RuntimeError("missing session")):
                self.assertEqual(helper.main(), 0)
            self.assertEqual(events, ["disarm", "shell"])
            events.clear()
            conflict = helper.RpcError(dict(message="claim changed", data=dict(kind="conflict")))
            with patch.object(helper, "run", side_effect=conflict):
                self.assertEqual(helper.main(), 1)
            self.assertEqual(events, [])
            errors = io.StringIO()
            with patch.object(helper, "run", side_effect=RuntimeError("missing session")), \
                 patch.object(helper, "disarm", side_effect=RuntimeError("core unavailable")), \
                 patch.object(sys, "stderr", errors):
                self.assertEqual(helper.main(), 1)
            self.assertIn("missing session", errors.getvalue())
            self.assertIn("core unavailable", errors.getvalue())
            self.assertEqual(events, [])
            errors = io.StringIO()
            with patch.object(helper, "run", return_value=7), \
                 patch.object(helper, "disarm", side_effect=RuntimeError("core unavailable")), \
                 patch.object(sys, "stderr", errors):
                self.assertEqual(helper.main(), 7)
            self.assertIn("core unavailable", errors.getvalue())
            self.assertEqual(events, [])

    def test_busy_launcher_cannot_disarm_or_open_shell_and_lock_releases(self):
        code = ("import sys; sys.path.insert(0," + repr(str(Path(helper.__file__).parent)) + "); "
                "import agent_recovery as h; "
                "h.disarm=lambda:print('WRONG DISARM'); h.shell=lambda:print('WRONG SHELL'); "
                "sys.argv=['helper','new','--config','unused']; sys.exit(h.main())")
        with patch.dict(helper.os.environ, HC_TERMINAL_SOCKET="scratch-" + str(uuid.uuid4()), HC_TERMINAL_ID="t1"):
            with helper.terminal_lock():
                result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn("already owns", result.stderr)
                self.assertEqual(result.stdout, "")
            code = code[:code.index("h.disarm=")] + "exec('with h.terminal_lock():\\n print(\"released\")')"
            result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "released")

    def test_cleanup_defers_real_interrupt_until_bounded_clear_finishes(self):
        class InterruptedCore(LaunchCore):
            def __enter__(self):
                signal.raise_signal(signal.SIGINT)
                return self
        core = InterruptedCore()
        core.kept = True
        previous = signal.getsignal(signal.SIGINT)
        with patch.object(helper, "Core", return_value=core), \
             patch.object(helper, "terminal_lock", side_effect=nullcontext), \
             patch.object(helper, "run", return_value=7), \
             patch.dict(helper.os.environ, HC_TERMINAL_SOCKET="fixture", HC_TERMINAL_ID="t1", HC_SCOPE="scope"), \
             patch.object(sys, "argv", ["helper", "new", "--config", "unused"]):
            self.assertEqual(helper.main(), 7)
        self.assertFalse(core.kept)
        self.assertEqual(signal.getsignal(signal.SIGINT), previous)



    def test_recovery_cannot_claim_a_pin_from_a_stale_document_read(self):
        core = DocumentCore()
        sid = str(uuid.uuid4())
        helper.claim(core, "t1", "codex", "/repo", sid)
        saved = core.call("doc.get", {})
        newer = helper.claim(core, "t1", "codex", "/new")
        with self.assertRaises(helper.RpcError):
            helper.claim(core, "t1", "codex", "/repo", sid, current=saved)
        self.assertEqual(core.value, newer)
        self.assertEqual(core.value["cwd"], "/new")


    def test_direct_argv_spaces_and_vendor_arguments(self):
        with tempfile.TemporaryDirectory(prefix="recovery space ") as folder:
            script = Path(folder) / "argument probe.py"
            output = Path(folder) / "captured args.json"
            script.write_text("import json,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))")
            values = ["", "model name", "path with spaces", 'quoted " value', "unicode café"]
            self.assertEqual(helper.child([sys.executable, str(script), str(output), *values], folder, None), 0)
            self.assertEqual(json.loads(output.read_text()), values)
            original_spawn = subprocess.Popen
            def interrupt_at_spawn(*args, **kwargs):
                process = original_spawn(*args, **kwargs)
                signal.raise_signal(signal.SIGINT)
                return process
            with patch.object(helper.subprocess, "Popen", side_effect=interrupt_at_spawn):
                self.assertEqual(helper.child([sys.executable, str(script), str(output), *values], folder, None), 0)
            with patch.object(helper.subprocess, "Popen") as spawn, patch.object(helper.os, "name", "posix"):
                spawn.return_value.wait.return_value = -2
                self.assertEqual(helper.child(["vendor"], folder, None), 130)
        command = helper.vendor_command(dict(agent="claude", argv=["claude", "--model", "model name"]))
        self.assertEqual(command[2], "model name")
        self.assertNotIn("--dangerously-skip-permissions", command)


class ClaudeHookTests(unittest.TestCase):
    def test_native_identity_tracks_main_changes_and_only_its_owner_can_finish(self):
        with tempfile.TemporaryDirectory(prefix="Claude hook ") as folder:
            config = Path(folder) / "claude.json"
            config.write_text(json.dumps(dict(agent="claude", argv=["claude"])))
            first, next_id = str(uuid.uuid4()), str(uuid.uuid4())
            core = LaunchCore()
            env = dict(HC_TERMINAL_ID="t1", HC_TERMINAL_SOCKET="hook-" + str(uuid.uuid4()), HC_SCOPE="scope",
                       CLAUDE_CODE_SESSION_ATTENDED="1", CLAUDE_PID="101")
            start = dict(hook_event_name="SessionStart", session_id=first, cwd=folder, source="startup", extra="ignored")
            with patch.object(helper, "Core", return_value=core), patch.dict(helper.os.environ, env), \
                 patch.object(helper, "child", side_effect=AssertionError("hooks must not launch a program")):
                # A recovered/menu launcher can hold its lifetime lock while Claude's hooks run.
                with helper.terminal_lock():
                    helper.claude_hook(config, start)
                self.assertEqual(core.value, dict(agent="claude", cwd=str(Path(folder).resolve()), session_id=first, owner_pid=101))
                self.assertTrue(core.kept)
                for source in ("clear", "resume", "fork"):
                    helper.claude_hook(config, dict(start, session_id=next_id, source=source))
                    self.assertEqual(core.value["session_id"], next_id)
                end = dict(hook_event_name="SessionEnd", session_id=next_id, reason="prompt_input_exit")
                for reason in ("clear", "resume", "other"):
                    helper.claude_hook(config, dict(end, reason=reason))
                    self.assertTrue(core.kept)
                helper.claude_hook(config, dict(end, session_id=first))
                with patch.dict(helper.os.environ, CLAUDE_PID="102"):
                    helper.claude_hook(config, end)
                self.assertTrue(core.kept)
                helper.claude_hook(config, end)
                self.assertFalse(core.kept)

    def test_background_headless_subagents_and_non_herdr_calls_do_not_enroll(self):
        event = dict(hook_event_name="SessionStart", session_id=str(uuid.uuid4()), cwd="unused")
        base = dict(HC_TERMINAL_ID="t1", HC_TERMINAL_SOCKET="fixture", HC_SCOPE="scope", CLAUDE_PID="101",
                    CLAUDE_CODE_SESSION_ATTENDED="1")
        with patch.object(helper, "Core", side_effect=AssertionError("unattended calls must not touch core")):
            for changes, payload in [
                (dict(HC_SCOPE=""), event),
                (dict(CLAUDE_CODE_SESSION_ATTENDED="0"), event),
                (dict(CLAUDE_CODE_SESSION_ATTENDED=""), event),
                (dict(CLAUDE_JOB_DIR="background-job"), event),
                ({}, dict(event, agent_id="child")),
            ]:
                with patch.dict(helper.os.environ, dict(base, **changes)):
                    helper.claude_hook("unused", payload)

    def test_shutdown_and_missing_core_preserve_recipe_without_opening_a_shell(self):
        class StoppingCore(LaunchCore):
            def call(self, method, params):
                if method == "terminal.keep":
                    raise helper.RpcError(dict(message="terminal is stopping", data=dict(kind="unavailable")))
                return super().call(method, params)
        core = StoppingCore()
        core.kept = True
        sid = str(uuid.uuid4())
        core.value = dict(agent="claude", session_id=sid, owner_pid=101)
        original = copy.deepcopy(core.value)
        event = json.dumps(dict(hook_event_name="SessionEnd", session_id=sid, reason="prompt_input_exit"))
        env = dict(HC_TERMINAL_ID="t1", HC_TERMINAL_SOCKET="hook-" + str(uuid.uuid4()), HC_SCOPE="scope",
                   CLAUDE_CODE_SESSION_ATTENDED="1", CLAUDE_PID="101")
        with patch.dict(helper.os.environ, env), \
             patch.object(sys, "argv", ["helper", "claude-hook", "--config", "unused"]), \
             patch.object(helper, "shell", side_effect=AssertionError("a hook must never open a shell")):
            with patch.object(helper, "Core", return_value=core), patch.object(sys, "stdin", io.StringIO(event)):
                self.assertEqual(helper.main(), 0)
            self.assertEqual(core.value, original)
            self.assertTrue(core.kept)
            with patch.object(helper, "Core", side_effect=FileNotFoundError("stopped core")), patch.object(sys, "stdin", io.StringIO(event)):
                self.assertEqual(helper.main(), 0)


if __name__ == "__main__":
    unittest.main()
