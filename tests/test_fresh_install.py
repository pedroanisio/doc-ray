"""Host-shape detection and privilege drop in static/ref/fresh-install.sh.

Two independent concerns, both of which failed the same way: a provision that
looked fine at the point of the mistake and blew up somewhere that named neither
the cause nor the step responsible.

Host shape: the script decides GPU-vs-CPU once, up front, and every later step
branches on that decision — including whether `provision_host.py` is asked to
install the container toolkit and generate the CDI spec.

The failure this guards against is silent degradation, not a crash. Detection
was `nvidia-smi -L || HAS_GPU=0`, so a GPU droplet whose driver was missing,
still loading, or mismatched was provisioned as a CPU-only host, printed
"Host ready", and omitted the GPU lane entirely. The operator then ran
`make gpu-worker-up` and got `unresolvable CDI devices nvidia.com/gpu=all` from
Docker — an error naming neither the driver nor the skipped provisioning step.

A card that is physically present but unusable is the one case that must stop
the run: it is the only one where the operator is paying GPU rates for a host
that cannot use the card.

Privilege drop: `as_operator` must hand the operator both a real HOME and a
working directory they can actually enter. `runuser` inherits root's CWD, so the
operator started in /root (mode 0700). An installer that ends a step with
`cd -` resolves $OLDPWD to that unreadable /root and fails; as the last command
in the function its status escapes `sh -c` and `bash -lc`, and `set -e` aborts
the whole provision with only "sh: cd: can't cd to /root" to go on.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "static" / "ref" / "fresh-install.sh"

#: Resolved up front and invoked by absolute path: these tests rewrite PATH to
#: inject stub binaries, and subprocess resolves the executable using the PATH of
#: the env it is handed — so a relative "bash" would vanish with the stubs.
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(_BASH is None, reason="bash not available")


def _fake_bin(tmp_path: Path, name: str, exit_code: int, stdout: str = "") -> None:
    """Put a stub executable earlier on PATH than the real tool."""
    path = tmp_path / name
    path.write_text(f"#!/usr/bin/env bash\nprintf '%s' {stdout!r}\nexit {exit_code}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _detect(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Source the script library-only and run just the detection step."""
    environ = dict(os.environ)
    environ["PATH"] = f"{tmp_path}:{environ['PATH']}"
    environ["FRESH_INSTALL_LIB_ONLY"] = "1"
    environ.setdefault("SSH_PUBKEY", "ssh-ed25519 AAAAtest test@test")
    # Pinned so sourcing never shells out to `hostname`, which the
    # PATH-stripped case below does not provide.
    environ.setdefault("DOCRAY_WORKER_HOST", "test-host")
    # Default PCI discovery to an empty fixture so a run on a machine that
    # genuinely has an NVIDIA card cannot change the outcome. Determinism matters
    # more than usual here: the behaviour under test is precisely "what do we
    # conclude from the hardware present". `env` is applied last, so a test that
    # passes PCI_DEVICES_DIR still wins.
    empty = tmp_path / "pci-empty"
    empty.mkdir(exist_ok=True)
    environ["PCI_DEVICES_DIR"] = str(empty)
    environ.update(env)
    return subprocess.run(
        [str(_BASH), "-c", f'source "{_SCRIPT}"; detect_host_shape; echo "HAS_GPU=$HAS_GPU"'],
        capture_output=True,
        text=True,
        env=environ,
        timeout=60,
    )


def test_working_card_provisions_the_gpu_lane(tmp_path: Path) -> None:
    _fake_bin(tmp_path, "nvidia-smi", 0, "GPU 0: NVIDIA RTX 6000 Ada Generation\n")
    _fake_bin(tmp_path, "lspci", 0, "01:00.0 3D controller: NVIDIA Corporation AD102GL\n")
    result = _detect(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "HAS_GPU=1" in result.stdout


def test_no_card_at_all_is_a_clean_cpu_host(tmp_path: Path) -> None:
    """A plain droplet is a supported shape — it must not fail."""
    _fake_bin(tmp_path, "nvidia-smi", 1)
    _fake_bin(tmp_path, "lspci", 0, "00:03.0 Ethernet controller: Red Hat Virtio\n")
    result = _detect(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "HAS_GPU=0" in result.stdout


def test_present_but_unusable_card_stops_the_run(tmp_path: Path) -> None:
    """The regression: a card on the PCI bus that nvidia-smi cannot drive.

    Previously this produced HAS_GPU=0 and a "Host ready" banner, so the GPU
    lane was never provisioned and the failure surfaced much later as an
    unresolvable CDI device at `make gpu-worker-up`.
    """
    _fake_bin(tmp_path, "nvidia-smi", 1)
    _fake_bin(tmp_path, "lspci", 0, "01:00.0 3D controller: NVIDIA Corporation AD102GL\n")
    result = _detect(tmp_path)
    assert result.returncode != 0, (
        "a machine billed at GPU rates whose card cannot be driven must stop the "
        f"run, not silently become a CPU host. stdout={result.stdout!r}"
    )
    combined = result.stdout + result.stderr
    assert "NVIDIA" in combined or "nvidia" in combined
    assert "driver" in combined.lower(), "the message must name the driver as the suspect"


def test_require_gpu_0_ignores_a_present_card(tmp_path: Path) -> None:
    """The documented opt-out must win even when a usable card is visible."""
    _fake_bin(tmp_path, "nvidia-smi", 0, "GPU 0: NVIDIA RTX 6000 Ada Generation\n")
    _fake_bin(tmp_path, "lspci", 0, "01:00.0 3D controller: NVIDIA Corporation AD102GL\n")
    result = _detect(tmp_path, REQUIRE_GPU="0")
    assert result.returncode == 0, result.stderr
    assert "HAS_GPU=0" in result.stdout


def test_require_gpu_1_without_a_card_stops_the_run(tmp_path: Path) -> None:
    _fake_bin(tmp_path, "nvidia-smi", 1)
    _fake_bin(tmp_path, "lspci", 0, "00:03.0 Ethernet controller: Red Hat Virtio\n")
    result = _detect(tmp_path, REQUIRE_GPU="1")
    assert result.returncode != 0


def _pci_fixture(tmp_path: Path, vendor_ids: list[str]) -> Path:
    """A fake /sys/bus/pci/devices tree with the given vendor ids."""
    root = tmp_path / "pci"
    for index, vendor in enumerate(vendor_ids):
        slot = root / f"0000:0{index}:00.0"
        slot.mkdir(parents=True)
        (slot / "vendor").write_text(f"{vendor}\n")
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_sysfs_finds_the_card_when_pciutils_is_absent(tmp_path: Path) -> None:
    """The hole that made lspci-based detection insufficient.

    Detection runs before this script installs any package, so on a minimal
    image lspci may not exist. If the presence check depended on it, a GPU host
    with a broken driver would fall straight back to the silent CPU path — the
    exact regression the suite above is meant to prevent. sysfs needs no package
    and no working driver.
    """
    _fake_bin(tmp_path, "nvidia-smi", 1)
    pci = _pci_fixture(tmp_path, ["0x10de"])  # NVIDIA
    result = _detect(tmp_path, PCI_DEVICES_DIR=str(pci))
    assert result.returncode != 0, (
        "an NVIDIA vendor id on the PCI bus must stop the run even with no lspci; "
        f"stdout={result.stdout!r}"
    )
    assert "0x10de" in result.stdout + result.stderr


def test_sysfs_without_an_nvidia_vendor_is_a_cpu_host(tmp_path: Path) -> None:
    _fake_bin(tmp_path, "nvidia-smi", 1)
    # lspci MUST be stubbed as card-free too. Detection consults both sysfs and
    # lspci, so leaving the real one on PATH makes the outcome depend on whether
    # the machine running the suite happens to have an NVIDIA card — a developer
    # workstation with a GPU fails a test whose subject is "no card present".
    _fake_bin(tmp_path, "lspci", 0, "00:03.0 Ethernet controller: Red Hat Virtio\n")
    pci = _pci_fixture(tmp_path, ["0x1af4", "0x8086"])  # virtio, Intel
    result = _detect(tmp_path, PCI_DEVICES_DIR=str(pci))
    assert result.returncode == 0, result.stderr
    assert "HAS_GPU=0" in result.stdout


def test_empty_pci_tree_does_not_crash_under_set_u(tmp_path: Path) -> None:
    """An unmatched glob must not be treated as a literal path."""
    _fake_bin(tmp_path, "nvidia-smi", 1)
    _fake_bin(tmp_path, "lspci", 0, "00:03.0 Ethernet controller: Red Hat Virtio\n")
    pci = _pci_fixture(tmp_path, [])
    result = _detect(tmp_path, PCI_DEVICES_DIR=str(pci))
    assert result.returncode == 0, result.stderr
    assert "HAS_GPU=0" in result.stdout


def test_detection_survives_a_host_without_lspci(tmp_path: Path) -> None:
    """pciutils is not guaranteed on a minimal image.

    Absent lspci the script cannot distinguish "no card" from "broken driver",
    and must fall back to the conservative reading rather than crashing under
    `set -u`/`set -e`.
    """
    _fake_bin(tmp_path, "nvidia-smi", 1)
    # deliberately no lspci stub; hide any real one
    (tmp_path / "lspci").unlink(missing_ok=True)
    environ = dict(os.environ)
    environ["PATH"] = str(tmp_path)  # nothing else on PATH but our stubs + builtins
    environ["FRESH_INSTALL_LIB_ONLY"] = "1"
    environ["SSH_PUBKEY"] = "ssh-ed25519 AAAAtest test@test"
    environ["DOCRAY_WORKER_HOST"] = "test-host"
    empty = tmp_path / "pci-empty"
    empty.mkdir(exist_ok=True)
    environ["PCI_DEVICES_DIR"] = str(empty)
    result = subprocess.run(
        [str(_BASH), "-c", f'source "{_SCRIPT}"; detect_host_shape; echo "HAS_GPU=$HAS_GPU"'],
        capture_output=True,
        text=True,
        env=environ,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "HAS_GPU=0" in result.stdout


# --------------------------------------------------------- privilege drop ----
# `as_operator` shells out to `runuser`, which requires root, so these stub it
# with a recorder that reports the CWD and login variables it was actually
# handed. That is precisely the contract that broke: the helper set HOME
# correctly and left the working directory pointing at root's home.


def _as_operator(
    tmp_path: Path, script: str, *, operator_home: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Source the script library-only, then run `script` with as_operator defined."""
    home = operator_home or str(tmp_path / "home" / "pals")
    Path(home).mkdir(parents=True, exist_ok=True)

    # Stand in for runuser: no privilege change, just exec the rest of the
    # argv so `env -C ... HOME=... cmd` runs verbatim and the callee observes
    # exactly the CWD and environment the helper chose.
    runuser = tmp_path / "runuser"
    runuser.write_text(
        "#!/usr/bin/env bash\n"
        # strip: -u <user> --
        'shift 2; [[ "${1:-}" == "--" ]] && shift\n'
        'exec "$@"\n'
    )
    runuser.chmod(runuser.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    environ = dict(os.environ)
    environ["PATH"] = f"{tmp_path}:{environ['PATH']}"
    environ["FRESH_INSTALL_LIB_ONLY"] = "1"
    environ["SSH_PUBKEY"] = "ssh-ed25519 AAAAtest test@test"
    environ["DOCRAY_WORKER_HOST"] = "test-host"
    return subprocess.run(
        [
            str(_BASH),
            "-c",
            f'source "{_SCRIPT}"; operator_home={home!r}; OPERATOR_USER=pals; {script}',
        ],
        capture_output=True,
        text=True,
        env=environ,
        # A directory the operator is not meant to work in, standing in for the
        # /root this script is really launched from.
        cwd=str(tmp_path / "launch-dir"),
        timeout=60,
    )


def test_as_operator_starts_in_the_operator_home(tmp_path: Path) -> None:
    """The callee's CWD must be the operator's home, not the launch directory."""
    (tmp_path / "launch-dir").mkdir()
    home = tmp_path / "home" / "pals"
    result = _as_operator(tmp_path, "as_operator pwd", operator_home=str(home))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(home)


def test_as_operator_still_sets_the_login_variables(tmp_path: Path) -> None:
    """Fixing the CWD must not regress the HOME fix it sits next to."""
    (tmp_path / "launch-dir").mkdir()
    home = tmp_path / "home" / "pals"
    result = _as_operator(
        tmp_path,
        "as_operator bash -c 'echo \"H=$HOME U=$USER L=$LOGNAME\"'",
        operator_home=str(home),
    )
    assert result.returncode == 0, result.stderr
    assert f"H={home} U=pals L=pals" in result.stdout


def test_as_operator_survives_a_callee_that_runs_cd_dash(tmp_path: Path) -> None:
    """The installer failure shape: `cd "$DIR"` then `cd -`.

    `cd -` resolves $OLDPWD. With the inherited launch directory that is a
    path the operator cannot enter, so the last command of the installer's
    clone step fails, the status escapes `sh -c`, and `set -e` aborts the
    provision. Reproduced with a POSIX `sh` callee because dash is what the
    upstream installer runs under.
    """
    launch = tmp_path / "launch-dir"
    launch.mkdir()
    home = tmp_path / "home" / "pals"
    zsh_dir = home / ".oh-my-zsh"
    zsh_dir.mkdir(parents=True)
    # Stand in for /root: enterable by the process that launches the script (as
    # root is), but not by the operator it drops to. The stub runuser cannot
    # really change uid, so the callee is denied via a bind of the same idea —
    # OLDPWD is set to a path that does not resolve at all.
    result = _as_operator(
        tmp_path,
        # Mirror the installer: OLDPWD points at the launch directory, then the
        # callee cds into $ZSH and back. `cd -` must land somewhere it can enter.
        f"as_operator sh -c 'cd {str(zsh_dir)!r} && cd - && pwd'",
        operator_home=str(home),
    )
    assert result.returncode == 0, f"cd - failed: {result.stderr}"
    assert "can't cd" not in result.stderr
    # The point of the fix: `cd -` returns to the operator's home, a directory
    # they own, rather than to whatever directory root launched the script from.
    assert str(home) in result.stdout
    assert str(launch) not in result.stdout


def test_as_operator_sh_runs_a_login_shell_in_the_operator_home(tmp_path: Path) -> None:
    """The `bash -lc` wrapper inherits the same corrected working directory."""
    (tmp_path / "launch-dir").mkdir()
    home = tmp_path / "home" / "pals"
    result = _as_operator(tmp_path, "as_operator_sh 'pwd'", operator_home=str(home))
    assert result.returncode == 0, result.stderr
    assert str(home) in result.stdout


def test_as_operator_refuses_before_operator_home_is_resolved(tmp_path: Path) -> None:
    """Guard against a caller moved above the passwd lookup."""
    (tmp_path / "launch-dir").mkdir()
    result = _as_operator(tmp_path, "operator_home=''; as_operator pwd")
    assert result.returncode != 0
    assert "as_operator called before operator_home" in result.stderr


def test_env_chdir_guard_passes_on_a_supported_base(tmp_path: Path) -> None:
    """The preflight must not reject a base image that is in fact fine."""
    (tmp_path / "launch-dir").mkdir()
    result = _as_operator(tmp_path, "require_env_chdir && echo GUARD_OK")
    assert result.returncode == 0, result.stderr
    assert "GUARD_OK" in result.stdout


def test_env_chdir_guard_names_the_cause_on_an_old_base(tmp_path: Path) -> None:
    """Without `env -C` the run must stop with a message naming coreutils.

    An `env` too old to support -C reports only "invalid option", which says
    nothing about the working directory or why the provision needs one.
    """
    (tmp_path / "launch-dir").mkdir()
    _fake_bin(tmp_path, "env", 125, "env: invalid option -- 'C'\n")
    result = _as_operator(tmp_path, "require_env_chdir")
    assert result.returncode != 0
    assert "coreutils" in result.stderr
    assert "8.28" in result.stderr


def test_as_operator_rejects_a_missing_operator_home(tmp_path: Path) -> None:
    """`env -C` on a nonexistent directory would fail without naming it."""
    (tmp_path / "launch-dir").mkdir()
    result = _as_operator(tmp_path, "operator_home=/nonexistent/home; as_operator pwd")
    assert result.returncode != 0
    assert "/nonexistent/home" in result.stderr


# --- operator identity ------------------------------------------------------
# The script names no operator: whoever provisions the host supplies the account,
# name and e-mail, and a run that lacks them stops before touching anything.


def _run_preconditions(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    environ = {k: v for k, v in os.environ.items() if not k.startswith("OPERATOR_")}
    environ["PATH"] = f"{tmp_path}:{environ['PATH']}"
    environ["DOCRAY_WORKER_HOST"] = "test-host"
    environ.update(env)
    return subprocess.run(
        [str(_BASH), str(_SCRIPT)], capture_output=True, text=True, env=environ, timeout=60
    )


def test_missing_operator_identity_stops_the_run_before_the_root_check(tmp_path: Path) -> None:
    result = _run_preconditions(tmp_path, SSH_PUBKEY="ssh-ed25519 AAAAtest test@test")
    assert result.returncode == 1
    assert "set OPERATOR_USER, OPERATOR_NAME and OPERATOR_EMAIL" in result.stderr
    assert "run as root" not in result.stderr


def test_operator_identity_has_no_defaults() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    for var in ("OPERATOR_USER", "OPERATOR_EMAIL", "OPERATOR_NAME"):
        assert f'{var}="${{{var}:-}}"' in text, var
    assert "dotfiles" not in text and "oh-my-zsh" not in text


def test_complete_identity_reaches_the_root_check(tmp_path: Path) -> None:
    result = _run_preconditions(
        tmp_path,
        SSH_PUBKEY="ssh-ed25519 AAAAtest test@test",
        OPERATOR_USER="ops",
        OPERATOR_NAME="Ops Person",
        OPERATOR_EMAIL="ops@example.com",
    )
    if os.geteuid() == 0:
        pytest.skip("running as root would start a real provision")
    assert result.returncode == 1
    assert "run as root" in result.stderr
