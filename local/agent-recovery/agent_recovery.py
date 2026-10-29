"""Personal, opt-in pinned-main conversation recovery for an existing Herdr terminal."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import queue
import shlex
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid


class BusyError(RuntimeError):
    pass


@contextmanager
def terminal_lock():
    identity = os.environ["HC_TERMINAL_SOCKET"] + "\0" + os.environ["HC_TERMINAL_ID"]
    directory = Path.home() / ".cache" / "herdr-agent-recovery"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(identity.encode()).hexdigest() + ".lock")
    # Keep the file: unlinking it can split Unix contenders across different inodes.
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BusyError("a managed agent already owns this terminal") from None
        yield


class RpcError(RuntimeError):
    def __init__(self, error):
        super().__init__(error.get("message", "core rejected the request"))
        self.kind = error.get("data", {}).get("kind")


class Core:
    """Same bounded hc connect transport as the retained capture experiment."""

    def __enter__(self):
        env = dict(os.environ, HC_SOCKET=os.environ["HC_TERMINAL_SOCKET"])
        self.bridge = subprocess.Popen(
            ["hc", "connect"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.replies = queue.Queue()
        self.sequence = 0
        self.deadline = time.monotonic() + 5

        def read():
            for line in self.bridge.stdout:
                self.replies.put(line)
            self.replies.put(None)

        threading.Thread(target=read, daemon=True).start()
        return self

    def call(self, method, params):
        self.sequence += 1
        self.bridge.stdin.write(json.dumps(dict(jsonrpc="2.0", id=self.sequence,
                                               method=method, params=params)) + "\n")
        self.bridge.stdin.flush()
        try:
            line = self.replies.get(timeout=max(0, self.deadline - time.monotonic()))
        except queue.Empty:
            raise RuntimeError("core did not answer") from None
        if line is None:
            raise RuntimeError("terminal core is unavailable")
        reply = json.loads(line)
        if reply.get("id") != self.sequence:
            raise RuntimeError("unexpected core reply")
        if "error" in reply:
            raise RpcError(reply["error"])
        return reply["result"]

    def __exit__(self, *exception):
        self.bridge.stdin.close()
        try:
            self.bridge.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.bridge.terminate()
            self.bridge.wait()
        self.bridge.stdout.close()


def document_name(terminal):
    return "herdr-main-" + terminal


def claim(core, terminal, agent, cwd, session_id=None, source="startup", current=None):
    if current is None:
        current = core.call("doc.get", {"name": document_name(terminal)})
    value = dict(invocation=str(uuid.uuid4()), agent=agent, cwd=cwd,
                 expected_id=session_id, source=source,
                 session_id=session_id if source == "resume" else None)
    # A competing managed launch wins or loses explicitly; never overwrite its claim.
    core.call("doc.set", dict(name=document_name(terminal), version=current["version"], value=value))
    return value


def capture(core, terminal, invocation, agent, event):
    if not isinstance(event, dict):
        raise ValueError("session event must be an object")
    if event.get("hook_event_name") != "SessionStart" or event.get("agent_id"):
        return
    current = core.call("doc.get", {"name": document_name(terminal)})
    value = current["value"]
    if not value or value["invocation"] != invocation or value["agent"] != agent or value["session_id"]:
        return
    expected = value["expected_id"]
    if event.get("source") != value["source"]:
        return
    sid = event.get("session_id")
    if expected and sid != expected:
        return
    if not isinstance(sid, str) or not sid.strip() or event.get("cwd") != value["cwd"]:
        raise ValueError("startup identity or working directory does not match")
    uuid.UUID(sid)
    value = dict(value, session_id=sid)
    try:
        core.call("doc.set", dict(name=document_name(terminal), version=current["version"], value=value))
    except RpcError as error:
        if error.kind != "conflict":
            raise
        # Re-read once: a newer launch or another successful capture owns the pin.
        newer = core.call("doc.get", {"name": document_name(terminal)})["value"]
        if newer and (newer["invocation"] != invocation or newer["session_id"]):
            return
        raise


def hook_command(agent):
    # Both qualified Windows CLIs execute command hooks with a POSIX shell.
    return shlex.join([sys.executable.replace("\\", "/"),
                       str(Path(__file__).resolve()).replace("\\", "/"), "capture", agent])


def vendor_command(config, session_id=None):
    agent = config["agent"]
    command = list(config["argv"])
    hook = dict(hooks=[dict(type="command", command=hook_command(agent), timeout=10)])
    if agent == "claude":
        command += ["--settings", json.dumps(dict(hooks=dict(SessionStart=[hook])))]
        command += ["--resume", session_id] if session_id else ["--session-id", str(uuid.uuid4())]
    elif agent == "codex":
        handler = '{type="command",command=' + json.dumps(hook_command(agent)) + ',timeout=10}'
        command += ["--no-daemon", "-c", 'hooks.SessionStart=[{hooks=[' + handler + ']}]']
        if session_id:
            command += ["resume", session_id]
    else:
        raise ValueError("config agent must be claude or codex")
    return command


def child(command, cwd, env):
    # Inherit the existing PTY; do not introduce a second terminal or detach the child.
    process = subprocess.Popen(command, cwd=cwd, env=env)
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return process.wait()
    finally:
        signal.signal(signal.SIGINT, previous)


def shell():
    command = [shutil.which("pwsh") or os.environ.get("COMSPEC", "cmd.exe")] if os.name == "nt" else [os.environ.get("SHELL", "/bin/sh")]
    return child(command, str(Path.home()), os.environ)


def run(args):
    config_path = Path(args.config).resolve(strict=True)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config["argv"], list) or not config["argv"] or not config["argv"][0] or not all(isinstance(s, str) for s in config["argv"]):
        raise ValueError("config argv must be a nonempty string array")
    terminal = os.environ["HC_TERMINAL_ID"]
    session_id = args.session_id
    with Core() as core:
        schema = core.call("rpc.discover", {})
        keep = next((m for m in schema.get("methods", []) if m.get("name") == "terminal.keep"), {})
        names = {p.get("name") for p in keep.get("params", [])}
        if not {"recovery", "expected_scope"}.issubset(names):
            raise RuntimeError("this core does not support managed recovery")
        record = core.call("terminal.get", dict(terminal=terminal))
        if record["address"] != terminal or record["state"] != "live":
            raise RuntimeError("terminal is not live")
        current = None
        if args.action == "recover":
            current = core.call("doc.get", dict(name=document_name(terminal)))
            pin = current["value"]
            if not pin or not pin["session_id"] or pin["agent"] != config["agent"]:
                raise RuntimeError("no captured main conversation; use managed new or resume")
            cwd, session_id = pin["cwd"], pin["session_id"]
        else:
            cwd = str(Path(args.cwd or os.getcwd()).resolve(strict=True))
        if not Path(cwd).is_dir():
            raise RuntimeError("saved working directory is unavailable")
        if session_id:
            uuid.UUID(session_id)
        command = vendor_command(config, session_id)
        # Claude lets us preallocate the root ID; Codex reports its initial startup ID.
        expected = session_id or (command[-1] if config["agent"] == "claude" else None)
        invocation = claim(core, terminal, config["agent"], cwd, expected,
                           "resume" if session_id else "startup", current=current)
        recovery = dict(argv=[sys.executable, str(Path(__file__).resolve()), "recover", "--config", str(config_path)],
                        cwd=str(Path(__file__).resolve().parent))
        core.call("terminal.keep", dict(terminal=terminal, keep=True, recovery=recovery,
                                        expected_scope=os.environ["HC_SCOPE"]))
    env = dict(os.environ, HERDR_MAIN_INVOCATION=invocation["invocation"])
    return child(command, cwd, env)


def disarm():
    if os.environ.get("HC_TERMINAL_ID") and os.environ.get("HC_TERMINAL_SOCKET"):
        with Core() as core:
            core.call("terminal.keep", dict(terminal=os.environ["HC_TERMINAL_ID"], keep=False,
                                            expected_scope=os.environ["HC_SCOPE"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("new", "resume", "recover", "capture"))
    parser.add_argument("agent", nargs="?", choices=("claude", "codex"))
    parser.add_argument("--config")
    parser.add_argument("--cwd")
    parser.add_argument("--session-id")
    args = parser.parse_args()
    fallback = False
    try:
        if args.action == "capture":
            with Core() as core:
                capture(core, os.environ["HC_TERMINAL_ID"], os.environ["HERDR_MAIN_INVOCATION"],
                        args.agent, json.load(sys.stdin.buffer))
            return 0
        with terminal_lock():
            try:
                if not args.config or (args.action == "resume" and not args.session_id):
                    raise ValueError("supply --config and an explicit --session-id for resume")
                result = run(args)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                if isinstance(error, RpcError) and error.kind == "conflict":
                    raise BusyError("the managed launch lost its ownership claim") from error
                try:
                    disarm()
                except (OSError, ValueError, RuntimeError, KeyError) as cleanup_error:
                    raise RuntimeError(f"{error}; recovery could not be cleared: {cleanup_error}") from error
                fallback = True
                raise
            # A manual wrapper returns to its outer shell, which must not retain the recipe.
            disarm()
        return result
    except BusyError as error:
        print(f"Herdr agent recovery failed: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        print(f"Herdr agent recovery failed: {error}", file=sys.stderr)
        # Only open a shell after clearing recovery and releasing the invocation lock.
        return shell() if fallback else 1


if __name__ == "__main__":
    sys.exit(main())
