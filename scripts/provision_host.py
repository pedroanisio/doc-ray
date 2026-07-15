#!/usr/bin/env python3
"""Idempotently converge an ephemeral GPU host to a doc-ray processing node.

This box is spun up on demand (cost-aware), used to run the GPU NLP workers,
then destroyed. Nothing durable lives here — the corpus, object store, and DB
live off-box (managed Postgres / DigitalOcean Spaces). Every respawn therefore
starts from a clean image and must be re-converged before it can process.

Each step is a check -> reconfigure pair: it probes current state and, unless
running with --check, applies the minimum change to reach the desired state.
Re-running on an already-converged host is a no-op. Steps:

    gpu-present       an NVIDIA GPU is visible (precondition; not fixable)
    docker-service    dockerd is active and enabled at boot
    docker-group      the operator user can run docker without sudo
    nvidia-cdi        nvidia-container-toolkit + /etc/cdi/nvidia.yaml + docker
                      CDI feature, so the worker's `nvidia.com/gpu=all` request
                      (docker-compose.gpu.yml) resolves
    scratch-volume    the DOSCRATCH volume is mounted (model / HF cache, temp)
                      with a nofail fstab entry that survives reboots
    rpcbind-lockdown  rpcbind (port 111) is disabled — pure attack surface on a
                      public droplet
    swap              optional swap file (opt in with --swap-gib N)

Stdlib only: this runs before any project virtualenv exists.

Usage:
    sudo python3 scripts/provision_host.py            # converge (default)
    python3 scripts/provision_host.py --check         # report drift, change nothing
    sudo python3 scripts/provision_host.py --swap-gib 8 --json

Apply mode needs root; it re-execs itself under sudo unless --no-sudo is given.

Exit codes: 0 converged (or already converged); 1 drift remains (--check only);
2 a step failed / an unsatisfiable precondition (no GPU, volume not attached).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class RunResult:
    """Outcome of a single system command."""

    code: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        """True when the command exited 0."""
        return self.code == 0


class System(Protocol):
    """Side-effecting host operations, injected so steps stay unit-testable."""

    def run(self, argv: Sequence[str]) -> RunResult:
        """Run a command and capture its result."""
        ...

    def read_text(self, path: str) -> str | None:
        """Return file contents, or None if the file does not exist."""
        ...

    def write_text(self, path: str, text: str) -> None:
        """Write (overwrite) a file."""
        ...

    def makedirs(self, path: str) -> None:
        """Create a directory tree, ignoring an existing one."""
        ...


class RealSystem:
    """System backed by the real host (subprocess + filesystem)."""

    def run(self, argv: Sequence[str]) -> RunResult:
        """Execute argv without a shell, capturing stdout/stderr."""
        proc = subprocess.run(list(argv), capture_output=True, text=True, check=False)
        return RunResult(proc.returncode, proc.stdout, proc.stderr)

    def read_text(self, path: str) -> str | None:
        """Read a file, returning None when it is absent."""
        try:
            return Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def write_text(self, path: str, text: str) -> None:
        """Overwrite a file with text."""
        Path(path).write_text(text, encoding="utf-8")

    def makedirs(self, path: str) -> None:
        """Create path and parents if missing."""
        Path(path).mkdir(parents=True, exist_ok=True)


class Outcome(str, Enum):
    """Result of running one step."""

    OK = "ok"  # already converged
    FIXED = "fixed"  # drift found and repaired
    DRIFT = "drift"  # drift found, left as-is (--check)
    FAILED = "failed"  # repair failed or precondition unsatisfiable


@dataclass(frozen=True)
class StepResult:
    """A step's name, outcome, and a human-readable detail line."""

    name: str
    outcome: Outcome
    detail: str


@dataclass
class Config:
    """Resolved run configuration."""

    apply: bool
    docker_user: str
    mount_point: str = "/mnt/scratch"
    volume_label: str = "DOSCRATCH"
    cdi_path: str = "/etc/cdi/nvidia.yaml"
    daemon_json: str = "/etc/docker/daemon.json"
    harden_rpcbind: bool = True
    force_cdi: bool = False
    swap_gib: int = 0


class Step:
    """Base class: a probe (read) plus an apply (converge)."""

    name = "step"

    def fixable(self) -> bool:
        """Whether apply() can repair drift (False = precondition only)."""
        return True

    def probe(self, sys: System, cfg: Config) -> tuple[bool, str]:
        """Return (converged, detail) without mutating the host."""
        raise NotImplementedError

    def apply(self, sys: System, cfg: Config) -> str:
        """Converge the host; return a detail line. Raise on failure."""
        raise NotImplementedError


class GpuPresent(Step):
    """An NVIDIA GPU must be visible; the script cannot install hardware."""

    name = "gpu-present"

    def fixable(self) -> bool:
        """No repair path — a missing GPU is an unsatisfiable precondition."""
        return False

    def probe(self, sys: System, cfg: Config) -> tuple[bool, str]:
        """Confirm nvidia-smi lists at least one GPU."""
        res = sys.run(["nvidia-smi", "-L"])
        if res.ok and res.out.strip().startswith("GPU 0"):
            return True, res.out.strip().splitlines()[0]
        return False, "no NVIDIA GPU visible (nvidia-smi -L failed)"


class DockerService(Step):
    """dockerd must be running and enabled at boot."""

    name = "docker-service"

    def probe(self, sys: System, cfg: Config) -> tuple[bool, str]:
        """Check systemd active + enabled state for docker."""
        active = sys.run(["systemctl", "is-active", "docker"]).out.strip()
        enabled = sys.run(["systemctl", "is-enabled", "docker"]).out.strip()
        if active == "active" and enabled == "enabled":
            return True, "docker active and enabled"
        return False, f"docker active={active or 'unknown'!r} enabled={enabled or 'unknown'!r}"

    def apply(self, sys: System, cfg: Config) -> str:
        """Enable and start docker in one systemctl call."""
        res = sys.run(["systemctl", "enable", "--now", "docker"])
        if not res.ok:
            raise RuntimeError(res.err.strip() or "systemctl enable --now docker failed")
        return "enabled and started docker"


class DockerGroup(Step):
    """The operator user must run docker without sudo."""

    name = "docker-group"

    def probe(self, sys: System, cfg: Config) -> tuple[bool, str]:
        """Check the user's group list for docker."""
        groups = sys.run(["id", "-nG", cfg.docker_user]).out.split()
        if "docker" in groups:
            return True, f"{cfg.docker_user} in docker group"
        return False, f"{cfg.docker_user} not in docker group"

    def apply(self, sys: System, cfg: Config) -> str:
        """Add the user to docker (takes effect on next login)."""
        res = sys.run(["usermod", "-aG", "docker", cfg.docker_user])
        if not res.ok:
            raise RuntimeError(res.err.strip() or "usermod failed")
        return f"added {cfg.docker_user} to docker group (re-login required to take effect)"


class NvidiaCdi(Step):
    """nvidia-container-toolkit + a CDI spec + docker's CDI feature.

    docker-compose.gpu.yml reserves the GPU via ``driver: cdi`` /
    ``nvidia.com/gpu=all``; that resolves only when all three are present.
    """

    name = "nvidia-cdi"

    def _toolkit(self, sys: System) -> bool:
        return sys.run(["nvidia-ctk", "--version"]).ok

    def _cdi_enabled(self, sys: System, cfg: Config) -> bool:
        raw = sys.read_text(cfg.daemon_json)
        if not raw:
            return False
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return False
        return bool(data.get("features", {}).get("cdi"))

    def probe(self, sys: System, cfg: Config) -> tuple[bool, str]:
        """Verify toolkit, a non-empty CDI spec, and docker's CDI feature."""
        if not self._toolkit(sys):
            return False, "nvidia-container-toolkit (nvidia-ctk) not installed"
        if cfg.force_cdi:
            return False, "forced CDI regeneration requested (--force-cdi)"
        spec = sys.read_text(cfg.cdi_path)
        if spec is None:
            return False, f"CDI spec {cfg.cdi_path} missing"
        if "nvidia.com/gpu" not in spec:
            return False, f"CDI spec {cfg.cdi_path} has no nvidia.com/gpu devices"
        if not self._cdi_enabled(sys, cfg):
            return False, f"docker CDI feature not enabled in {cfg.daemon_json}"
        return True, "nvidia-ctk present, CDI spec valid, docker CDI enabled"

    def apply(self, sys: System, cfg: Config) -> str:
        """Install the toolkit if needed, (re)generate the CDI spec, enable CDI."""
        actions: list[str] = []
        if not self._toolkit(sys):
            err = sys.run(["apt-get", "install", "-y", "nvidia-container-toolkit"]).err
            if not self._toolkit(sys):
                raise RuntimeError(
                    "nvidia-container-toolkit missing and apt-get install failed: "
                    + (err.strip()[:200] or "unknown")
                )
            actions.append("installed nvidia-container-toolkit")
        sys.makedirs(str(Path(cfg.cdi_path).parent))
        gen = sys.run(["nvidia-ctk", "cdi", "generate", f"--output={cfg.cdi_path}"])
        if not gen.ok:
            raise RuntimeError("nvidia-ctk cdi generate failed: " + (gen.err.strip()[:200] or "?"))
        actions.append(f"generated CDI spec at {cfg.cdi_path}")
        if self._enable_cdi(sys, cfg):
            actions.append("enabled docker CDI feature and restarted docker")
        return "; ".join(actions)

    def _enable_cdi(self, sys: System, cfg: Config) -> bool:
        raw = sys.read_text(cfg.daemon_json)
        data: dict = {}
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {}
        features = data.setdefault("features", {})
        if features.get("cdi") is True:
            return False
        features["cdi"] = True
        sys.write_text(cfg.daemon_json, json.dumps(data, indent=2) + "\n")
        sys.run(["systemctl", "restart", "docker"])
        return True


class ScratchVolume(Step):
    """Mount the DOSCRATCH volume for model / HF cache and NLP temp files."""

    name = "scratch-volume"
    _subdirs = ("models", "hf-cache", "tmp")

    def _device(self, sys: System, cfg: Config) -> str:
        res = sys.run(["blkid", "-L", cfg.volume_label])
        return res.out.strip() if res.ok else ""

    def _mounted(self, sys: System, cfg: Config) -> bool:
        return sys.run(["findmnt", "-n", cfg.mount_point]).ok

    def _in_fstab(self, sys: System, cfg: Config) -> bool:
        fstab = sys.read_text("/etc/fstab") or ""
        return cfg.volume_label in fstab or cfg.mount_point in fstab

    def probe(self, sys: System, cfg: Config) -> tuple[bool, str]:
        """Converged only when mounted AND pinned in fstab (survives reboot)."""
        if self._mounted(sys, cfg) and self._in_fstab(sys, cfg):
            return True, f"{cfg.mount_point} mounted with fstab entry"
        if self._mounted(sys, cfg):
            return False, f"{cfg.mount_point} mounted but missing from fstab"
        if not self._device(sys, cfg):
            return False, f"no volume labelled {cfg.volume_label!r} is attached"
        return False, f"{cfg.volume_label} present but not mounted at {cfg.mount_point}"

    def apply(self, sys: System, cfg: Config) -> str:
        """Mount by label, pin a nofail fstab entry, create cache subdirs."""
        actions: list[str] = []
        if not self._mounted(sys, cfg):
            device = self._device(sys, cfg)
            if not device:
                raise RuntimeError(
                    f"no volume labelled {cfg.volume_label!r} is attached — attach it in "
                    "the DigitalOcean console, then re-run"
                )
            sys.makedirs(cfg.mount_point)
            res = sys.run(["mount", device, cfg.mount_point])
            if not res.ok:
                raise RuntimeError(f"mount {device} -> {cfg.mount_point} failed: {res.err.strip()}")
            actions.append(f"mounted {device} at {cfg.mount_point}")
        if self._append_fstab(sys, cfg):
            actions.append("added nofail fstab entry")
        for sub in self._subdirs:
            sys.makedirs(str(Path(cfg.mount_point) / sub))
        actions.append(f"ensured subdirs {', '.join(self._subdirs)}")
        return "; ".join(actions)

    def _append_fstab(self, sys: System, cfg: Config) -> bool:
        if self._in_fstab(sys, cfg):
            return False
        fstab = sys.read_text("/etc/fstab") or ""
        prefix = "" if not fstab or fstab.endswith("\n") else "\n"
        fields = (
            f"LABEL={cfg.volume_label}",
            cfg.mount_point,
            "ext4",
            "defaults,nofail,noatime",
            "0",
            "2",
        )
        sys.write_text("/etc/fstab", fstab + prefix + "\t".join(fields) + "\n")
        return True


class RpcbindLockdown(Step):
    """Disable rpcbind — nothing here uses NFS/NIS and port 111 is public."""

    name = "rpcbind-lockdown"
    _dead = frozenset({"inactive", "failed", "unknown", "", "masked"})

    def probe(self, sys: System, cfg: Config) -> tuple[bool, str]:
        """Converged when hardening is disabled or rpcbind is not active."""
        if not cfg.harden_rpcbind:
            return True, "skipped (--no-harden-rpcbind)"
        states = {
            sys.run(["systemctl", "is-active", unit]).out.strip()
            for unit in ("rpcbind.socket", "rpcbind")
        }
        if states <= self._dead:
            return True, "rpcbind disabled"
        return False, "rpcbind active — public attack surface on port 111"

    def apply(self, sys: System, cfg: Config) -> str:
        """Stop, disable, and mask both rpcbind units."""
        sys.run(["systemctl", "disable", "--now", "rpcbind.socket", "rpcbind"])
        sys.run(["systemctl", "mask", "rpcbind"])
        return "disabled and masked rpcbind"


class SwapFile(Step):
    """Optional swap file as OOM insurance for large-batch NLP (opt in)."""

    name = "swap"
    _path = "/swapfile"

    def probe(self, sys: System, cfg: Config) -> tuple[bool, str]:
        """Converged when no swap requested or the swapfile is already active."""
        if cfg.swap_gib <= 0:
            return True, "skipped (no --swap-gib)"
        active = sys.run(["swapon", "--show=NAME", "--noheadings"]).out
        if self._path in active:
            return True, f"{self._path} active"
        return False, f"no swapfile ({cfg.swap_gib} GiB requested)"

    def apply(self, sys: System, cfg: Config) -> str:
        """Allocate, format, enable, and pin a swap file in fstab."""
        for argv, label in (
            (["fallocate", "-l", f"{cfg.swap_gib}G", self._path], "fallocate"),
            (["chmod", "600", self._path], "chmod"),
            (["mkswap", self._path], "mkswap"),
            (["swapon", self._path], "swapon"),
        ):
            res = sys.run(argv)
            if not res.ok:
                raise RuntimeError(f"{label} failed: {res.err.strip() or res.out.strip()}")
        fstab = sys.read_text("/etc/fstab") or ""
        if self._path not in fstab:
            prefix = "" if not fstab or fstab.endswith("\n") else "\n"
            sys.write_text("/etc/fstab", fstab + prefix + f"{self._path}\tnone\tswap\tsw\t0\t0\n")
        return f"created and enabled {cfg.swap_gib} GiB swapfile"


STEPS: tuple[Step, ...] = (
    GpuPresent(),
    DockerService(),
    DockerGroup(),
    NvidiaCdi(),
    ScratchVolume(),
    RpcbindLockdown(),
    SwapFile(),
)


def run_step(step: Step, sys: System, cfg: Config) -> StepResult:
    """Probe a step and, in apply mode, converge it — never raising."""
    try:
        converged, detail = step.probe(sys, cfg)
    except Exception as exc:  # noqa: BLE001 - a broken probe is a step failure, not a crash
        return StepResult(step.name, Outcome.FAILED, f"probe error: {exc}")
    if converged:
        return StepResult(step.name, Outcome.OK, detail)
    if not cfg.apply:
        return StepResult(step.name, Outcome.DRIFT, detail)
    if not step.fixable():
        return StepResult(step.name, Outcome.FAILED, detail)
    try:
        return StepResult(step.name, Outcome.FIXED, step.apply(sys, cfg))
    except Exception as exc:  # noqa: BLE001 - surface any apply failure as FAILED
        return StepResult(step.name, Outcome.FAILED, f"{detail}; apply failed: {exc}")


def converge(sys: System, cfg: Config) -> list[StepResult]:
    """Run every step in order and collect results."""
    return [run_step(step, sys, cfg) for step in STEPS]


def exit_code(results: Sequence[StepResult]) -> int:
    """0 converged, 1 drift remains (--check), 2 a step failed."""
    outcomes = {r.outcome for r in results}
    if Outcome.FAILED in outcomes:
        return 2
    if Outcome.DRIFT in outcomes:
        return 1
    return 0


_SYMBOL = {
    Outcome.OK: "✓",
    Outcome.FIXED: "⟳",
    Outcome.DRIFT: "•",
    Outcome.FAILED: "✗",
}


def emit(results: Sequence[StepResult], as_json: bool) -> None:
    """Print results as JSON or a human-readable summary."""
    if as_json:
        payload = [
            {"step": r.name, "outcome": r.outcome.value, "detail": r.detail} for r in results
        ]
        print(json.dumps(payload, indent=2))
        return
    for res in results:
        print(f"  {_SYMBOL[res.outcome]} [{res.outcome.value:6}] {res.name}: {res.detail}")
    failed = [r for r in results if r.outcome is Outcome.FAILED]
    drifted = [r for r in results if r.outcome is Outcome.DRIFT]
    fixed = [r for r in results if r.outcome is Outcome.FIXED]
    print()
    if failed:
        print(f"{len(failed)} step(s) FAILED — host is NOT ready to process.")
    elif drifted:
        print(f"{len(drifted)} step(s) need changes — re-run without --check to apply.")
    elif fixed:
        print(f"Host converged: {len(fixed)} change(s) applied.")
    else:
        print("Host already converged — nothing to do.")


def resolve_user(explicit: str | None) -> str:
    """Pick the operator user: explicit flag, then SUDO_USER, then USER."""
    return explicit or os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"


def build_config(args: argparse.Namespace) -> Config:
    """Translate parsed CLI args into a Config."""
    return Config(
        apply=not args.check,
        docker_user=resolve_user(args.user),
        mount_point=args.mount_point,
        volume_label=args.volume_label,
        cdi_path=args.cdi_path,
        harden_rpcbind=not args.no_harden_rpcbind,
        force_cdi=args.force_cdi,
        swap_gib=args.swap_gib,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Idempotently converge an ephemeral GPU host into a doc-ray processing node."
    )
    parser.add_argument(
        "--check", action="store_true", help="Report drift and exit; change nothing."
    )
    parser.add_argument("--json", action="store_true", help="Emit results as JSON.")
    parser.add_argument("--user", help="Operator user for docker group (default: $SUDO_USER).")
    parser.add_argument("--mount-point", default="/mnt/scratch", help="Scratch volume mount point.")
    parser.add_argument("--volume-label", default="DOSCRATCH", help="Scratch volume fs label.")
    parser.add_argument("--cdi-path", default="/etc/cdi/nvidia.yaml", help="CDI spec output path.")
    parser.add_argument("--no-harden-rpcbind", action="store_true", help="Do not disable rpcbind.")
    parser.add_argument(
        "--force-cdi", action="store_true", help="Regenerate the CDI spec even if present."
    )
    parser.add_argument(
        "--swap-gib", type=int, default=0, help="Create a swap file of N GiB (0 = none)."
    )
    parser.add_argument(
        "--no-sudo", action="store_true", help="Do not re-exec under sudo when not root."
    )
    return parser


def _reexec_under_sudo(argv: Sequence[str]) -> int:
    sudo = shutil.which("sudo")
    if not sudo:
        print("apply mode needs root and sudo is unavailable — re-run as root.", file=sys.stderr)
        return 2
    os.execvp(sudo, [sudo, sys.executable, os.path.abspath(__file__), *argv])
    return 2  # unreachable if execvp succeeds


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse, (optionally) escalate, converge, report."""
    raw = sys.argv[1:] if argv is None else argv
    args = _parser().parse_args(raw)
    cfg = build_config(args)
    if cfg.apply and not args.no_sudo and os.geteuid() != 0:
        return _reexec_under_sudo(raw)
    results = converge(RealSystem(), cfg)
    emit(results, args.json)
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
