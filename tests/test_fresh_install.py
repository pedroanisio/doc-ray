"""Host-shape detection in static/ref/fresh-install.sh.

The script decides GPU-vs-CPU once, up front, and every later step branches on
that decision — including whether `provision_host.py` is asked to install the
container toolkit and generate the CDI spec.

The failure this guards against is silent degradation, not a crash. Detection
was `nvidia-smi -L || HAS_GPU=0`, so a GPU droplet whose driver was missing,
still loading, or mismatched was provisioned as a CPU-only host, printed
"Host ready", and omitted the GPU lane entirely. The operator then ran
`make gpu-worker-up` and got `unresolvable CDI devices nvidia.com/gpu=all` from
Docker — an error naming neither the driver nor the skipped provisioning step.

A card that is physically present but unusable is the one case that must stop
the run: it is the only one where the operator is paying GPU rates for a host
that cannot use the card.
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
    pci = _pci_fixture(tmp_path, ["0x1af4", "0x8086"])  # virtio, Intel
    result = _detect(tmp_path, PCI_DEVICES_DIR=str(pci))
    assert result.returncode == 0, result.stderr
    assert "HAS_GPU=0" in result.stdout


def test_empty_pci_tree_does_not_crash_under_set_u(tmp_path: Path) -> None:
    """An unmatched glob must not be treated as a literal path."""
    _fake_bin(tmp_path, "nvidia-smi", 1)
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
