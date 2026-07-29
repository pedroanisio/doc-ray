"""Tests for scripts/provision_host.py using an in-memory fake host.

The fake models just enough system state (systemd unit states, group
membership, block-device labels, mounts, files) that a step's apply() actually
mutates what its probe() reads back — so idempotency ("apply, then re-probe is
OK", "apply twice touches fstab once") is exercised for real, not asserted
against a stub.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import provision_host as ph  # noqa: E402


class FakeSystem:
    def __init__(self, *, gpu=True, toolkit=True):
        self.gpu = gpu
        self.toolkit = toolkit
        #: Models a dockerd that installs but refuses to start — the real
        #: failure seen when a leftover daemon.json names a runtime the new
        #: engine cannot resolve.
        self.docker_start_fails = False
        self.journal = (
            "dockerd[900]: failed to start daemon: error initializing graphdriver: "
            "exec: nvidia-container-runtime: no such file or directory\n"
        )
        self.active = {"docker": "inactive", "rpcbind.socket": "active", "rpcbind": "active"}
        self.enabled = {"docker": "disabled"}
        self.groups = {"pals": {"pals"}}
        self.labels = {"DOSCRATCH": "/dev/vdc1"}
        self.mounted = set()
        self.swap = False
        self.files = {
            "/etc/fstab": "# <file system> <mount point>\nLABEL=root / ext4 defaults 0 1\n"
        }
        self.calls = []

    # -- System protocol -------------------------------------------------
    def run(self, argv):
        c = list(argv)
        self.calls.append(c)
        return self._dispatch(c)

    def read_text(self, path):
        return self.files.get(path)

    def write_text(self, path, text):
        self.files[path] = text

    def makedirs(self, path):
        self.files.setdefault("dir:" + path, "")

    # -- command interpreter --------------------------------------------
    def _dispatch(self, c):  # noqa: C901 - a flat command switch is clearest here
        ok = ph.RunResult(0)
        if c[:2] == ["nvidia-smi", "-L"]:
            return (
                ph.RunResult(0, "GPU 0: NVIDIA H200 (UUID: GPU-x)\n")
                if self.gpu
                else ph.RunResult(1)
            )
        if c[0] == "nvidia-ctk" and c[1:2] == ["--version"]:
            return (
                ph.RunResult(0, "1.16.0\n") if self.toolkit else ph.RunResult(127, err="not found")
            )
        if c[:3] == ["nvidia-ctk", "cdi", "generate"]:
            out = next(
                (a.split("=", 1)[1] for a in c if a.startswith("--output=")), "/etc/cdi/nvidia.yaml"
            )
            self.files[out] = "cdiVersion: 0.6.0\ndevices:\n- name: all  # nvidia.com/gpu=all\n"
            return ok
        if c[:1] == ["apt-get"]:
            self.toolkit = True
            return ok
        if c[:2] == ["systemctl", "is-active"]:
            return ph.RunResult(0, self.active.get(c[2], "inactive") + "\n")
        if c[:2] == ["systemctl", "is-enabled"]:
            return ph.RunResult(0, self.enabled.get(c[2], "disabled") + "\n")
        if c[:2] == ["systemctl", "enable"]:
            units = [u for u in c[2:] if u != "--now"]
            if self.docker_start_fails and "docker" in units:
                return ph.RunResult(
                    1,
                    err="Job for docker.service failed because the control "
                    "process exited with error code.\n",
                )
            for unit in units:
                self.active[unit], self.enabled[unit] = "active", "enabled"
            return ok
        if c[:2] == ["journalctl", "-u"]:
            return ph.RunResult(0, self.journal)
        if c[:2] == ["systemctl", "disable"]:
            for unit in [u for u in c[2:] if u != "--now"]:
                self.active[unit], self.enabled[unit] = "inactive", "disabled"
            return ok
        if c[:2] in (["systemctl", "mask"], ["systemctl", "restart"]):
            return ok
        if c[:2] == ["id", "-nG"]:
            return ph.RunResult(0, " ".join(sorted(self.groups.get(c[2], set()))) + "\n")
        if c[0] == "usermod":
            self.groups.setdefault(c[-1], set()).add(c[c.index("-aG") + 1])
            return ok
        if c[:2] == ["blkid", "-L"]:
            dev = self.labels.get(c[2])
            return ph.RunResult(0, dev + "\n") if dev else ph.RunResult(2)
        if c[0] == "findmnt":
            return ph.RunResult(0, c[-1] + "\n") if c[-1] in self.mounted else ph.RunResult(1)
        if c[0] == "mount":
            self.mounted.add(c[-1])
            return ok
        if c[0] == "swapon" and "--show=NAME" in c:
            return ph.RunResult(0, "/swapfile\n" if self.swap else "")
        if c[0] == "swapon":
            self.swap = True
            return ok
        if c[0] in ("fallocate", "chmod", "mkswap"):
            return ok
        return ph.RunResult(127, err="unhandled: " + " ".join(c))


def cfg(**kw):
    base = {"apply": True, "docker_user": "pals"}
    base.update(kw)
    return ph.Config(**base)


# -- individual steps ----------------------------------------------------


def test_gpu_present_is_precondition_not_fixable():
    step, sys_ = ph.GpuPresent(), FakeSystem(gpu=False)
    res = ph.run_step(step, sys_, cfg())
    assert res.outcome is ph.Outcome.FAILED
    assert not step.fixable()


def test_docker_service_apply_then_converged():
    sys_, c = FakeSystem(), cfg()
    assert ph.run_step(ph.DockerService(), sys_, c).outcome is ph.Outcome.FIXED
    assert ph.DockerService().probe(sys_, c)[0] is True


def test_docker_group_apply_adds_user():
    sys_, c = FakeSystem(), cfg()
    assert ph.run_step(ph.DockerGroup(), sys_, c).outcome is ph.Outcome.FIXED
    assert "docker" in sys_.groups["pals"]


def test_nvidia_cdi_generates_spec_and_enables_feature():
    sys_, c = FakeSystem(), cfg()
    assert ph.run_step(ph.NvidiaCdi(), sys_, c).outcome is ph.Outcome.FIXED
    assert "nvidia.com/gpu" in sys_.files[c.cdi_path]
    assert '"cdi": true' in sys_.files[c.daemon_json]
    assert ph.NvidiaCdi().probe(sys_, c)[0] is True


def test_nvidia_cdi_installs_toolkit_when_missing():
    sys_ = FakeSystem(toolkit=False)
    assert ph.run_step(ph.NvidiaCdi(), sys_, cfg()).outcome is ph.Outcome.FIXED
    assert sys_.toolkit is True


def test_scratch_volume_mounts_and_pins_fstab():
    sys_, c = FakeSystem(), cfg()
    assert ph.run_step(ph.ScratchVolume(), sys_, c).outcome is ph.Outcome.FIXED
    assert c.mount_point in sys_.mounted
    assert "LABEL=DOSCRATCH" in sys_.files["/etc/fstab"]


def test_scratch_volume_fstab_is_idempotent():
    sys_, c = FakeSystem(), cfg()
    ph.run_step(ph.ScratchVolume(), sys_, c)
    ph.run_step(ph.ScratchVolume(), sys_, c)
    assert sys_.files["/etc/fstab"].count("LABEL=DOSCRATCH") == 1


def test_scratch_volume_unattached_fails_with_guidance():
    sys_ = FakeSystem()
    sys_.labels.clear()
    res = ph.run_step(ph.ScratchVolume(), sys_, cfg())
    assert res.outcome is ph.Outcome.FAILED
    assert "attach it in the DigitalOcean console" in res.detail


def test_rpcbind_lockdown_disables_units():
    sys_, c = FakeSystem(), cfg()
    assert ph.run_step(ph.RpcbindLockdown(), sys_, c).outcome is ph.Outcome.FIXED
    assert ph.RpcbindLockdown().probe(sys_, c)[0] is True


def test_rpcbind_skipped_when_hardening_disabled():
    sys_ = FakeSystem()
    ok, detail = ph.RpcbindLockdown().probe(sys_, cfg(harden_rpcbind=False))
    assert ok is True and "skipped" in detail


def test_swap_skipped_by_default():
    ok, _ = ph.SwapFile().probe(FakeSystem(), cfg())
    assert ok is True


def test_swap_apply_when_requested():
    sys_, c = FakeSystem(), cfg(swap_gib=8)
    assert ph.run_step(ph.SwapFile(), sys_, c).outcome is ph.Outcome.FIXED
    assert sys_.swap is True
    assert "/swapfile" in sys_.files["/etc/fstab"]


# -- orchestration -------------------------------------------------------


def test_check_mode_reports_drift_without_mutating():
    sys_ = FakeSystem()
    results = ph.converge(sys_, cfg(apply=False))
    assert ph.exit_code(results) == 1
    assert all(r.outcome in (ph.Outcome.OK, ph.Outcome.DRIFT) for r in results)
    assert sys_.active["docker"] == "inactive"  # untouched


def test_apply_then_check_is_clean_and_idempotent():
    sys_ = FakeSystem()
    assert ph.exit_code(ph.converge(sys_, cfg())) == 0
    # Second converge on the now-ready host changes nothing.
    second = ph.converge(sys_, cfg())
    assert ph.exit_code(second) == 0
    assert all(r.outcome is ph.Outcome.OK for r in second)


def test_missing_gpu_makes_whole_run_fail():
    results = ph.converge(FakeSystem(gpu=False), cfg())
    assert ph.exit_code(results) == 2


# -- CPU-only hosts ------------------------------------------------------
# The same script provisions every droplet, not only the ephemeral GPU box, so
# the GPU lane and the scratch volume must be opt-out rather than preconditions.


def test_gpu_steps_are_skipped_on_a_cpu_only_host():
    sys_ = FakeSystem(gpu=False, toolkit=False)
    c = cfg(gpu=False)
    for step in (ph.GpuPresent(), ph.NvidiaCdi()):
        res = ph.run_step(step, sys_, c)
        assert res.outcome is ph.Outcome.OK, step.name
        assert "no-gpu" in res.detail
    # Skipping must not silently write a CDI spec or touch daemon.json.
    assert "/etc/cdi/nvidia.yaml" not in sys_.files
    assert "/etc/docker/daemon.json" not in sys_.files


def test_scratch_volume_is_skipped_when_no_volume_is_expected():
    sys_ = FakeSystem()
    sys_.labels.clear()  # nothing attached
    res = ph.run_step(ph.ScratchVolume(), sys_, cfg(scratch=False))
    assert res.outcome is ph.Outcome.OK
    assert "no-scratch" in res.detail
    assert "LABEL=DOSCRATCH" not in sys_.files["/etc/fstab"]


def test_cpu_only_host_converges_cleanly_without_gpu_or_volume():
    """A plain droplet: no card, no DOSCRATCH — still a ready doc-ray host."""
    sys_ = FakeSystem(gpu=False, toolkit=False)
    sys_.labels.clear()
    c = cfg(gpu=False, scratch=False)

    assert ph.exit_code(ph.converge(sys_, c)) == 0
    assert sys_.active["docker"] == "active"
    assert "docker" in sys_.groups["pals"]
    # Idempotent on the second pass, exactly like the GPU path.
    assert all(r.outcome is ph.Outcome.OK for r in ph.converge(sys_, c))


def test_gpu_host_still_fails_closed_when_the_card_is_missing():
    """--no-gpu is opt-in: the default must keep catching a GPU-less GPU host."""
    assert ph.exit_code(ph.converge(FakeSystem(gpu=False), cfg(gpu=True))) == 2


@pytest.mark.parametrize(
    ("argv", "expect_gpu", "expect_scratch"),
    [
        ([], True, True),
        (["--no-gpu"], False, True),
        (["--no-scratch"], True, False),
        (["--no-gpu", "--no-scratch"], False, False),
    ],
)
def test_cli_flags_map_to_config(argv, expect_gpu, expect_scratch):
    built = ph.build_config(ph._parser().parse_args(argv))
    assert built.gpu is expect_gpu
    assert built.scratch is expect_scratch


def test_docker_start_failure_reports_the_journal():
    """A bare 'exit code 1' is unactionable; the reason lives in the journal."""
    sys_ = FakeSystem()
    sys_.docker_start_fails = True

    res = ph.run_step(ph.DockerService(), sys_, cfg())

    assert res.outcome is ph.Outcome.FAILED
    assert "daemon.json" in res.detail or "journal" in res.detail
    assert "nvidia-container-runtime: no such file" in res.detail


@pytest.mark.parametrize(
    ("env", "explicit", "expected"),
    [
        ({"SUDO_USER": "pals"}, None, "pals"),
        ({"USER": "bob"}, None, "bob"),
        ({"SUDO_USER": "pals"}, "carol", "carol"),
    ],
)
def test_resolve_user(monkeypatch, env, explicit, expected):
    for key in ("SUDO_USER", "USER"):
        monkeypatch.delenv(key, raising=False)
    for key, val in env.items():
        monkeypatch.setenv(key, val)
    assert ph.resolve_user(explicit) == expected


def test_emit_json(capsys):
    results = [ph.StepResult("docker-service", ph.Outcome.FIXED, "started")]
    ph.emit(results, as_json=True)
    out = capsys.readouterr().out
    assert '"outcome": "fixed"' in out
