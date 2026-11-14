#!/usr/bin/env python3
"""Native Debian launcher for the persistent UWS control plane and OpenCode workers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from persistent_control_plane import ControlPlane, HERE, V2, status


class DebianControlPlane(ControlPlane):
    def __init__(
        self,
        runtime: Path,
        backend: str,
        routes: Path | None,
        opencode_bin: Path,
        rorch_model: str,
        qa_model: str,
    ) -> None:
        super().__init__(runtime, backend, routes)
        self.opencode_bin = opencode_bin.resolve()
        self.rorch_model = rorch_model
        self.qa_model = qa_model

    def _wait_socket(self, path: str, process: subprocess.Popen[str], label: str) -> None:
        for _ in range(300):
            if Path(path).is_socket():
                return
            if process.poll() is not None:
                out, err = process.communicate()
                raise RuntimeError(f"{label} exited: {out} {err}")
            time.sleep(0.05)
        raise RuntimeError(f"{label} socket timeout")

    def start_transport(self) -> None:
        command = [
            sys.executable,
            str(HERE / "transport_rpc.py"),
            "serve",
            "--db",
            str(self.transport_db),
            "--key-dir",
            str(self.keys),
            "--socket",
            self.socket,
        ]
        self.daemon = subprocess.Popen(
            command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self._wait_socket(self.socket, self.daemon, "transport")
        if not all((self.keys / f"{actor}.key").exists() for actor in ("morch", "rorch", "qa", "uws")):
            raise RuntimeError("transport identity material missing")
        endpoint = {
            "socket": self.socket,
            "pid": os.getpid(),
            "started_at": time.time(),
            "backend": self.backend,
            "platform": "debian-native",
        }
        (self.runtime / "endpoint.json").write_text(
            json.dumps(endpoint, sort_keys=True), encoding="utf-8"
        )

    def start_model_broker(self) -> None:
        if self.backend != "model-broker":
            return
        command = [
            sys.executable,
            str(HERE / "model_broker.py"),
            "serve",
            "--socket",
            self.model_socket,
            "--db",
            str(self.runtime / "model_calls.db"),
            "--key-dir",
            str(self.keys),
            "--work-root",
            str(self.runtime / "model_calls"),
            "--routes",
            str(self.routes),
        ]
        self.model_daemon = subprocess.Popen(
            command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self._wait_socket(self.model_socket, self.model_daemon, "model broker")
        endpoint = json.loads((self.runtime / "endpoint.json").read_text(encoding="utf-8"))
        endpoint.update(
            {
                "model_socket": self.model_socket,
                "model_broker_pid": self.model_daemon.pid,
                "routes": str(self.routes),
                "credential_boundary": "HOST_TRUSTED_BROKER_SECRET_ZERO_GUEST",
            }
        )
        (self.runtime / "endpoint.json").write_text(
            json.dumps(endpoint, sort_keys=True), encoding="utf-8"
        )

    def stop_model_broker(self) -> None:
        if not self.model_daemon or self.model_daemon.poll() is not None:
            return
        request = {
            "op": "shutdown",
            "actor": "MORCH",
            "key": (self.keys / "morch.key").read_bytes().hex(),
        }
        with __import__("socket").socket(__import__("socket").AF_UNIX) as sock:
            sock.connect(self.model_socket)
            sock.sendall((json.dumps(request, sort_keys=True) + "\n").encode())
            sock.recv(4096)
        self.model_daemon.wait(timeout=15)

    def start_delegate(self) -> None:
        command = [
            sys.executable,
            str(HERE / "morch_delegate.py"),
            "serve",
            "--socket",
            self.delegate_socket,
            "--db",
            str(self.runtime / "delegate.db"),
            "--transport-socket",
            self.socket,
            "--control-db",
            str(self.db),
            "--key-dir",
            str(self.keys),
        ]
        self.delegate_daemon = subprocess.Popen(
            command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self._wait_socket(self.delegate_socket, self.delegate_daemon, "delegate")
        endpoint = json.loads((self.runtime / "endpoint.json").read_text(encoding="utf-8"))
        route_models = {"RORCH": self.rorch_model, "QA": self.qa_model}
        if self.backend == "model-broker":
            route_config = json.loads(self.routes.read_text(encoding="utf-8"))
            route_models = {
                actor: route_config["roles"][actor]["model"] for actor in ("RORCH", "QA")
            }
        endpoint.update(
            {
                "delegate_socket": self.delegate_socket,
                "delegate_pid": self.delegate_daemon.pid,
                "authority": "MORCH_LOCAL_DELEGATE",
                "opencode_bin_sha256": self._sha256(self.opencode_bin),
                "rorch_model": route_models["RORCH"],
                "qa_model": route_models["QA"],
            }
        )
        (self.runtime / "endpoint.json").write_text(
            json.dumps(endpoint, sort_keys=True), encoding="utf-8"
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        import hashlib

        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def stop_delegate(self) -> None:
        if not self.delegate_daemon or self.delegate_daemon.poll() is not None:
            return
        subprocess.run(
            [
                sys.executable,
                str(HERE / "morch_delegate.py"),
                "client",
                "--socket",
                self.delegate_socket,
                "--key",
                str(self.keys / "human.key"),
                "--operation",
                "shutdown",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.delegate_daemon.wait(timeout=15)

    def reconcile(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(HERE / "transport_rpc.py"),
                "client",
                "--socket",
                self.socket,
                "--key",
                str(self.keys / "uws.key"),
                "--actor",
                "UWS",
                "reconcile",
            ],
            text=True,
            capture_output=True,
            timeout=20,
        )
        if completed.returncode:
            raise RuntimeError(completed.stdout + completed.stderr)


    def stop_transport(self) -> None:
        if not self.daemon or self.daemon.poll() is not None:
            return
        subprocess.run(
            [
                sys.executable,
                str(HERE / "transport_rpc.py"),
                "client",
                "--socket",
                self.socket,
                "--key",
                str(self.keys / "uws.key"),
                "--actor",
                "UWS",
                "shutdown",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.daemon.wait(timeout=15)

    def worker_cmd(self, actor: str, mid: str, oid: str) -> list[str]:
        objective = self.runtime / "objectives" / oid
        app = objective / "app"
        dev = objective / "dev"
        qa = objective / "qa"
        home = objective / "homes" / actor.lower()
        for path in (app, dev, qa, home):
            path.mkdir(parents=True, exist_ok=True)

        command = [
            "/usr/bin/bwrap",
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup",
            "--cap-drop",
            "ALL",
            "--clearenv",
            "--ro-bind",
            "/usr",
            "/usr",
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--symlink",
            "usr/sbin",
            "/sbin",
            "--ro-bind",
            "/etc",
            "/etc",
            "--dir",
            "/mnt",
            "--dir",
            "/mnt/wsl",
            "--ro-bind",
            "/mnt/wsl/resolv.conf",
            "/mnt/wsl/resolv.conf",
            "--ro-bind",
            str(HERE),
            "/code",
            "--ro-bind",
            str(self.keys / f"{actor.lower()}.key"),
            "/identity/key.bin",
            "--bind",
            self.socket_dir,
            "/ipc",
            "--bind",
            str(home),
            "/role/home",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--setenv",
            "HOME",
            "/role/home",
            "--setenv",
            "PATH",
            "/usr/bin:/bin",
            "--setenv",
            "PYTHONDONTWRITEBYTECODE",
            "1",
        ]
        if actor == "RORCH":
            command += ["--bind", str(app), "/role/app", "--bind", str(dev), "/role/dev"]
        elif actor == "QA":
            command += [
                "--ro-bind",
                str(app),
                "/role/app",
                "--ro-bind",
                str(dev),
                "/role/dev",
                "--bind",
                str(qa),
                "/role/qa",
            ]
        else:
            command += [
                "--ro-bind",
                str(app),
                "/role/app",
                "--ro-bind",
                str(dev),
                "/role/dev",
                "--ro-bind",
                str(qa),
                "/role/qa",
            ]

        if self.backend == "model-broker":
            command += ["--bind", self.model_socket_dir, "/model"]

        if self.backend == "opencode" and actor in {"RORCH", "QA"}:
            command += ["--ro-bind", str(self.opencode_bin), "/opt/opencode/opencode"]

        command += [
            "--chdir",
            "/code",
            "/usr/bin/python3",
            "/code/control_actor.py",
            "--actor",
            actor,
            "--key",
            "/identity/key.bin",
            "--socket",
            "/ipc/transport.sock",
            "--message-id",
            mid,
            "--backend",
            self.backend,
            "--opencode-bin",
            "/opt/opencode/opencode",
            "--opencode-rorch-model",
            self.rorch_model,
            "--opencode-qa-model",
            self.qa_model,
        ]
        if self.backend == "model-broker":
            command += ["--model-socket", "/model/model.sock"]
        return command


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "status"))
    parser.add_argument("--runtime", type=Path, default=V2 / "runtime" / "debian_opencode")
    parser.add_argument("--poll", type=float, default=0.2)
    parser.add_argument("--max-idle", type=float)
    parser.add_argument("--backend", choices=("deterministic", "opencode", "model-broker"), default="model-broker")
    parser.add_argument("--model-routes", type=Path)
    parser.add_argument(
        "--opencode-bin",
        type=Path,
        default=Path("/home/psm/.opencode/bin/opencode"),
    )
    parser.add_argument("--rorch-model", default="opencode/glm-5.3-flash-free")
    parser.add_argument("--qa-model", default="opencode/beam-free")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(status(args.runtime), indent=2, sort_keys=True))
        return 0
    control = DebianControlPlane(
        args.runtime,
        args.backend,
        args.model_routes,
        args.opencode_bin,
        args.rorch_model,
        args.qa_model,
    )
    return control.run(args.poll, args.max_idle)


if __name__ == "__main__":
    raise SystemExit(main())
