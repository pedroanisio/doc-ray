"""Identify the compute device this worker process actually uses.

A GPU-only worker host and a CPU host cost different amounts per hour, so cost
telemetry has to record which one ran the work — and record what the process
*observed*, not what its configuration hoped for. A container that was started
with a GPU reservation but cannot see the card silently falls back to CPU; if
telemetry copied the intent, the whole batch would be priced as GPU time.

The probe is lazy and cached: torch is a heavy import that the fast lane's image
may not even carry, and the answer cannot change during a process's life.
"""

from __future__ import annotations

import os
from functools import lru_cache

#: Device string used when no accelerator is visible to this process.
DEVICE_CPU = "cpu"


@lru_cache(maxsize=1)
def describe_device() -> str:
    """Return a stable description of this process's compute device.

    ``DOCRAY_DEVICE_LABEL`` overrides the probe, for hosts where the operator
    knows better than the runtime (a MIG slice, a shared card) — it is recorded
    verbatim, so a wrong label is an operator error rather than a silent one.

    Returns:
        ``cuda:<device name>`` when CUDA is visible, else ``cpu``. Never raises:
        a probe failure is reported as ``cpu``, which is what the process will
        in fact use.
    """
    override = os.environ.get("DOCRAY_DEVICE_LABEL", "").strip()
    if override:
        return override
    try:
        import torch

        if not torch.cuda.is_available():
            return DEVICE_CPU
        return f"cuda:{torch.cuda.get_device_name(0)}"
    except Exception:
        return DEVICE_CPU
