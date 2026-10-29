"""Owning tests for helper policy; vendor and core compatibility need native probes."""

import copy
from contextlib import nullcontext
import json
import io
from pathlib import Path
import subprocess
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


def event(sid=None, source="startup", **extra):
    return dict(hook_event_name="SessionStart", session_id=sid or str(uuid.uuid4()),
                source=source, cwd="/repo", **extra)


class RecoveryTests(unittest.TestCase):
    def test_durable_registration_precedes_spawn_and_resume_never_falls_back(self):
        class LaunchCore(DocumentCore):
            fail_keep = True
            capable = False
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def call(self, method, params):
                if method == "rpc.discover":
                    names = ["terminal", "keep", "recovery", "expected_scope"] if self.capable else ["terminal", "keep"]
                    return dict(methods=[dict(name="terminal.keep", params=[dict(name=n) for n in names])])
                if method == "terminal.get":
                    return dict(address="t1", state="live")
                if method == "terminal.keep":
                    if self.fail_keep:
                        raise RuntimeError("keep was not saved")
                    return {}
                return super().call(method, params)
        with tempfile.TemporaryDirectory(prefix="recovery launch ") as folder:
            output = Path(folder) / "launched.json"
            vendor = Path(folder) / "vendor.py"
            vendor.write_text("import json,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))")
            config = Path(folder) / "codex.json"
            config.write_text(json.dumps(dict(agent="codex", argv=[sys.executable, str(vendor), str(output), ""])))
            args = SimpleNamespace(action="new", config=str(config), cwd=folder, session_id=None)
            core = LaunchCore()
            with patch.object(helper, "Core", return_value=core), patch.dict(helper.os.environ, HC_TERMINAL_ID="t1", HC_SCOPE="scope"):
                with self.assertRaisesRegex(RuntimeError, "does not support"):
                    helper.run(args)
                self.assertIsNone(core.value)
                self.assertFalse(output.exists())
                core.capable = True
                with self.assertRaisesRegex(RuntimeError, "keep was not saved"):
                    helper.run(args)
                self.assertFalse(output.exists())
                core.fail_keep = False
                sid = str(uuid.uuid4())
                core.value["session_id"] = sid
                args.action = "recover"
                self.assertEqual(helper.run(args), 0)
                self.assertEqual(json.loads(output.read_text())[-2:], ["resume", sid])
                self.assertEqual(core.value["session_id"], sid)
                # Even before a resume hook, another recovery keeps the same UUID.
                self.assertEqual(helper.run(args), 0)
                self.assertEqual(json.loads(output.read_text())[-2:], ["resume", sid])
                vendor.write_text(vendor.read_text() + "\nraise SystemExit(7)\n")
                self.assertEqual(helper.run(args), 7)
                output.unlink()
                core.value["session_id"] = None
                with self.assertRaisesRegex(RuntimeError, "no captured main"):
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

    def test_late_owner_and_side_events_cannot_replace_pin(self):
        core = DocumentCore()
        old = helper.claim(core, "t1", "codex", "/repo")
        new = helper.claim(core, "t1", "codex", "/repo")
        helper.capture(core, "t1", old["invocation"], "codex", event())
        self.assertIsNone(core.value["session_id"])
        helper.capture(core, "t1", new["invocation"], "codex", event(source="fork"))
        helper.capture(core, "t1", new["invocation"], "codex", event(agent_id="child"))
        self.assertIsNone(core.value["session_id"])
        main = event()
        helper.capture(core, "t1", new["invocation"], "codex", main)
        for source in ("startup", "resume", "fork", "clear", "compact"):
            helper.capture(core, "t1", new["invocation"], "codex", event(source=source))
        self.assertEqual(core.value["session_id"], main["session_id"])

    def test_explicit_identity_and_source_are_required(self):
        for source in ("startup", "resume"):
            with self.subTest(source=source):
                core = DocumentCore()
                sid = str(uuid.uuid4())
                owner = helper.claim(core, "t1", "claude", "/repo", sid, source)
                helper.capture(core, "t1", owner["invocation"], "claude", event(source=source))
                helper.capture(core, "t1", owner["invocation"], "claude", event(sid, "fork"))
                self.assertEqual(core.value["session_id"], sid if source == "resume" else None)
                helper.capture(core, "t1", owner["invocation"], "claude", event(sid, source))
                self.assertEqual(core.value["session_id"], sid)

    def test_save_failure_is_visible_and_new_owner_wins_cas_race(self):
        core = DocumentCore()
        owner = helper.claim(core, "t1", "codex", "/repo")
        core.fail = True
        with self.assertRaisesRegex(RuntimeError, "disk write failed"):
            helper.capture(core, "t1", owner["invocation"], "codex", event())
        self.assertIsNone(core.value["session_id"])
        core.fail = False
        def replace_owner():
            helper.claim(core, "t1", "codex", "/other")
        core.before_set = replace_owner
        helper.capture(core, "t1", owner["invocation"], "codex", event())
        self.assertEqual(core.value["cwd"], "/other")
        self.assertIsNone(core.value["session_id"])

    def test_recovery_cannot_claim_a_pin_from_a_stale_document_read(self):
        core = DocumentCore()
        sid = str(uuid.uuid4())
        helper.claim(core, "t1", "codex", "/repo", sid, "resume")
        saved = core.call("doc.get", {})
        newer = helper.claim(core, "t1", "codex", "/new")
        with self.assertRaises(helper.RpcError):
            helper.claim(core, "t1", "codex", "/repo", sid, "resume", current=saved)
        self.assertEqual(core.value["invocation"], newer["invocation"])
        self.assertEqual(core.value["cwd"], "/new")

    def test_invalid_startup_does_not_make_recovery_available(self):
        core = DocumentCore()
        owner = helper.claim(core, "t1", "codex", "/repo")
        for bad in (dict(event(), cwd="/other"), dict(event(), session_id="not-an-id")):
            with self.assertRaises(ValueError):
                helper.capture(core, "t1", owner["invocation"], "codex", bad)
        self.assertIsNone(core.value["session_id"])

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
