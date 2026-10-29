"""Personal, opt-in pinned-main conversation recovery for an existing Herdr terminal."""

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
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
    """Attach-only newline RPC; never start a stopped core."""

    def __enter__(self):
        self.address = os.environ["HC_TERMINAL_SOCKET"]
        self.runner = asyncio.Runner()
        self.reader = self.writer = None
        self.sequence = 0
        self.deadline = time.monotonic() + 5
        try:
            self.runner.run(asyncio.wait_for(self.connect(), 5))
        except BaseException:
            try:
                self.runner.run(self.close())
            finally:
                self.runner.close()
            raise
        return self

    async def connect(self):
        # Match core wire.rs MAX_CONTROL; discovery alone nearly fills 64 KiB.
        limit = 64 * 1024 * 1024
        if os.name == "nt":
            loop = asyncio.get_running_loop()
            self.reader = asyncio.StreamReader(limit=limit)
            protocol = asyncio.StreamReaderProtocol(self.reader)
            transport, _ = await loop.create_pipe_connection(lambda: protocol, self.address)
            self.writer = asyncio.StreamWriter(transport, protocol, self.reader, loop)
        else:
            self.reader, self.writer = await asyncio.open_unix_connection(self.address, limit=limit)

    def call(self, method, params):
        try:
            return self.runner.run(asyncio.wait_for(self.exchange(method, params), max(0, self.deadline - time.monotonic())))
        except TimeoutError:
            raise RuntimeError("terminal core did not answer") from None

    async def exchange(self, method, params):
        self.sequence += 1
        self.writer.write((json.dumps(dict(jsonrpc="2.0", id=self.sequence, method=method, params=params)) + "\n").encode())
        await self.writer.drain()
        line = await self.reader.readline()
        if not line:
            raise RuntimeError("terminal core is unavailable")
        reply = json.loads(line)
        if reply.get("id") != self.sequence:
            raise RuntimeError("unexpected core reply")
        if "error" in reply:
            raise RpcError(reply["error"])
        return reply["result"]

    async def close(self):
        if self.writer is not None:
            try:
                self.writer.close()
                await asyncio.wait_for(self.writer.wait_closed(), 1)
            except OSError:
                pass

    def __exit__(self, *exception):
        try:
            self.runner.run(self.close())
        finally:
            self.runner.close()


def document_name(terminal):
    return "herdr-main-" + terminal


def claim(core, terminal, agent, cwd, session_id=None, current=None, explicit_resume=False):
    if current is None:
        current = core.call("doc.get", {"name": document_name(terminal)})
    value = dict(invocation=str(uuid.uuid4()), agent=agent, cwd=cwd, session_id=session_id)
    if explicit_resume:
        value["explicit_resume"] = True
    # A competing managed launch wins or loses explicitly; never overwrite its claim.
    core.call("doc.set", dict(name=document_name(terminal), version=current["version"], value=value))
    return value


def vendor_command(config, session_id=None):
    agent = config["agent"]
    command = list(config["argv"])
    if agent == "claude":
        command += ["--resume", session_id] if session_id else ["--session-id", str(uuid.uuid4())]
    elif agent == "codex":
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
        schemas = schema.get("components", {}).get("schemas", {})
        if config["agent"] == "codex" and "resume_session" not in schemas.get("Terminal", {}).get("properties", {}):
            raise RuntimeError("this core does not support native Codex session recovery")
        if config["agent"] == "codex" and "session_token" not in schemas.get("Recovery", {}).get("properties", {}):
            raise RuntimeError("this core does not support native session correlation")
        record = core.call("terminal.get", dict(terminal=terminal))
        if record["address"] != terminal or record["state"] != "live":
            raise RuntimeError("terminal is not live")
        current = None
        explicit_resume = config["agent"] == "codex" and args.action == "resume"
        if args.action == "recover":
            current = core.call("doc.get", dict(name=document_name(terminal)))
            pin = current["value"]
            if not pin or pin["agent"] != config["agent"]:
                raise RuntimeError("no main conversation identity; use managed new or resume")
            cwd = pin["cwd"]
            if config["agent"] == "codex":
                session_id = record.get("resume_session")
                if not session_id and pin.get("explicit_resume") is True:
                    session_id, explicit_resume = pin["session_id"], True
            else:
                session_id = pin["session_id"]
            if not session_id:
                raise RuntimeError("no main conversation identity; use managed new or resume")
        else:
            cwd = str(Path(args.cwd or os.getcwd()).resolve(strict=True))
        if not Path(cwd).is_dir():
            raise RuntimeError("saved working directory is unavailable")
        if session_id:
            uuid.UUID(session_id)
        command = vendor_command(config, session_id)
        # Native Codex identities remain in core; only explicit selections can be a fallback.
        expected = session_id or (command[-1] if config["agent"] == "claude" else None)
        if config["agent"] == "codex" and args.action == "recover" and not explicit_resume:
            expected = None
        invocation = claim(core, terminal, config["agent"], cwd, expected,
                           current=current, explicit_resume=explicit_resume)
        recovery = dict(argv=[sys.executable, str(Path(__file__).resolve()), "recover", "--config", str(config_path)],
                        cwd=str(Path(__file__).resolve().parent))
        if config["agent"] == "codex":
            recovery["session_token"] = invocation["invocation"]
        scope = dict(terminal=terminal, expected_scope=os.environ["HC_SCOPE"])
        if args.action != "recover":
            core.call("terminal.keep", dict(scope, keep=False))
        core.call("terminal.keep", dict(scope, keep=True, recovery=recovery))
    env = dict(os.environ)
    if config["agent"] == "codex":
        env["HERDR_MAIN_INVOCATION"] = invocation["invocation"]
    return child(command, cwd, env)


def disarm():
    if os.environ.get("HC_TERMINAL_ID") and os.environ.get("HC_TERMINAL_SOCKET"):
        with Core() as core:
            core.call("terminal.keep", dict(terminal=os.environ["HC_TERMINAL_ID"], keep=False,
                                            expected_scope=os.environ["HC_SCOPE"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("new", "resume", "recover"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--cwd")
    parser.add_argument("--session-id")
    args = parser.parse_args()
    if args.session_id and args.action != "resume":
        parser.error("--session-id selects a conversation only with resume")
    fallback = False
    try:
        with terminal_lock():
            try:
                if args.action == "resume" and not args.session_id:
                    raise ValueError("supply an explicit --session-id for resume")
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
