"""Owning tests for helper policy; vendor and core compatibility need native probes."""

import copy
from contextlib import nullcontext
import json
import io
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
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
    token_capable = True
    session_token = None
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
                        components=dict(schemas=dict(Terminal=dict(properties=properties),
                                                     Recovery=dict(properties=dict(session_token={}) if self.token_capable else {}))))
        if method == "terminal.get":
            return dict(address="t1", state="live", resume_session=self.native_id)
        if method == "terminal.keep":
            if self.fail_keep:
                raise RuntimeError("keep was not saved")
            self.kept = params["keep"]
            if not self.kept:
                self.native_id, self.session_token = None, None
            else:
                self.session_token = params["recovery"].get("session_token")
            return {}
        return super().call(method, params)


class RecoveryTests(unittest.TestCase):

    def test_menu_opt_in_checks_current_local_windows_space(self):
        module = Path(helper.__file__).parents[2] / "defaults/mods/new-menu/main.js"
        code = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
let active = { id: 'local', active: true }, platform = 'windows', configured = true, calls = [];
const agent = { key: 'claude', name: 'Claude', run: ['native-claude'], installed: true };
const cx = { focus_handle: () => ({focus(){}}), spawn: f => tasks.push(f(cx)), notify(){} };
let tasks = [];
const context = { View: class {}, Set, Promise, spaces: () => [active], window: () => ({platform}),
  appHome: () => 'C:/Herdr Space', knownAgents: async () => [agent],
  readTextFile: async () => configured ? '{}' : null, run: (method, params) => calls.push({method,params}) };
let source = fs.readFileSync(process.argv[1], 'utf8').replace(/^import .*;\r?\n/gm, '').replace('export default class', 'class');
vm.runInNewContext(source + '; globalThis.Menu = NewMenu;', context);
(async () => {
  const menu = new context.Menu(); menu.init({},cx); await Promise.all(tasks);
  menu.startAgent(agent); assert.equal(calls.at(-1).params.argv[0], 'python');
  active = { id: 'remote-now', active: true, machine: 'remote' };
  menu.startAgent(agent); assert.equal(calls.at(-1).params.argv[0], 'native-claude');
  assert.equal(calls.at(-1).params.space, 'remote-now');
  active = { id: 'local', active: true }; platform = 'macos';
  menu.startAgent(agent); assert.equal(calls.at(-1).params.argv[0], 'native-claude');
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
            vendor.write_text("import json,os,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:])); pathlib.Path(sys.argv[1]+'.token').write_text(os.environ.get('HERDR_MAIN_INVOCATION',''))")
            config = Path(folder) / "codex.json"
            config.write_text(json.dumps(dict(agent="codex", argv=[sys.executable, str(vendor), str(output), ""])))
            args = SimpleNamespace(action="new", config=str(config), cwd=folder, session_id=None)
            core = LaunchCore()
            core.capable, core.native_capable, core.token_capable, core.fail_keep = False, False, False, True
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
                core.capable = True
                with self.assertRaisesRegex(RuntimeError, "native Codex"):
                    helper.run(args)
                self.assertIsNone(core.value)
                core.native_capable = True
                with self.assertRaisesRegex(RuntimeError, "session correlation"):
                    helper.run(args)
                self.assertIsNone(core.value)
                core.token_capable = True
                with self.assertRaisesRegex(RuntimeError, "keep was not saved"):
                    helper.run(args)
                self.assertFalse(output.exists())
                core.fail_keep = False
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
            vendor.write_text("import json,os,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:])); pathlib.Path(sys.argv[1]+'.token').write_text(os.environ.get('HERDR_MAIN_INVOCATION',''))")
            config = Path(folder) / "codex.json"
            config.write_text(json.dumps(dict(agent="codex", argv=[sys.executable, str(vendor), str(output)])))
            args = SimpleNamespace(action="new", config=str(config), cwd=folder, session_id=None)
            core = LaunchCore()
            core.native_id = str(uuid.uuid4())
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
                first_token = core.session_token
                self.assertEqual(Path(str(output) + ".token").read_text(), first_token)
                self.assertEqual(first_token, core.value["invocation"])
                explicit = str(uuid.uuid4())
                args.action, args.session_id = "resume", explicit
                self.assertEqual(helper.run(args), 0)
                self.assertIsNone(core.native_id)
                self.assertTrue(core.value["explicit_resume"])
                self.assertNotEqual(core.session_token, first_token)
                self.assertEqual(Path(str(output) + ".token").read_text(), core.session_token)
                args.action, args.session_id = "recover", None
                self.assertEqual(helper.run(args), 0)
                self.assertEqual(json.loads(output.read_text()), ["resume", explicit])
                # A subsequent root native report is authoritative, never the fallback.
                core.native_id = published
                self.assertEqual(helper.run(args), 0)
                self.assertEqual(json.loads(output.read_text()), ["resume", published])
                self.assertEqual(Path(str(output) + ".token").read_text(), core.session_token)
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
            core.native_capable, core.token_capable = False, False
            original_child = helper.child
            before = []
            def spawn(command, cwd, env):
                before.append(core.value["session_id"])
                self.assertTrue(core.kept)
                self.assertIsNone(core.session_token)
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




    def test_recovery_cannot_claim_a_pin_from_a_stale_document_read(self):
        core = DocumentCore()
        sid = str(uuid.uuid4())
        helper.claim(core, "t1", "codex", "/repo", sid)
        saved = core.call("doc.get", {})
        newer = helper.claim(core, "t1", "codex", "/new")
        with self.assertRaises(helper.RpcError):
            helper.claim(core, "t1", "codex", "/repo", sid, current=saved)
        self.assertEqual(core.value["invocation"], newer["invocation"])
        self.assertEqual(core.value["cwd"], "/new")


    def test_direct_argv_spaces_and_vendor_arguments(self):
        with tempfile.TemporaryDirectory(prefix="recovery space ") as folder:
            script = Path(folder) / "argument probe.py"
            output = Path(folder) / "captured args.json"
            script.write_text("import json,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))")
            values = ["", "model name", "path with spaces", 'quoted " value', "unicode café"]
            self.assertEqual(helper.child([sys.executable, str(script), str(output), *values], folder, None), 0)
            self.assertEqual(json.loads(output.read_text()), values)
        command = helper.vendor_command(dict(agent="claude", argv=["claude", "--model", "model name"]))
        self.assertEqual(command[2], "model name")
        self.assertNotIn("--dangerously-skip-permissions", command)


if __name__ == "__main__":
    unittest.main()
