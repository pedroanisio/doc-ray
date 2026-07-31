#!/usr/bin/env bash
#
# Bootstrap any fresh DigitalOcean droplet into a doc-ray host.
#
# One script, two shapes, decided by what the hardware actually is:
#
#   * GPU droplet  — an NVIDIA card is visible. Provisions the GPU lane
#     (container toolkit, CDI spec) and proves a container can reserve the card.
#     This host runs WORKERS ONLY and exists to be destroyed when the queue
#     drains.
#   * Plain droplet — no card. Provisions everything else and stops before the
#     GPU lane rather than failing. Useful for a serving tier, an admin box, or
#     anything else that needs the same base.
#
# Detection is automatic; override with REQUIRE_GPU=1 (fail if no card, for when
# you are certain you paid for one) or REQUIRE_GPU=0 (never provision the GPU
# lane even if a card is present).
#
# Nothing durable is kept on either shape: the corpus (managed PostgreSQL) and
# the blobs (Spaces) live elsewhere. Neither shape runs migrations — the
# always-on tier owns the schema, and a machine you intend to delete must not be
# the one that changes it.
#
# Run as root on a first boot:
#
#     export SSH_PUBKEY='ssh-ed25519 AAAA... you@host'
#     bash fresh-install.sh
#
# Idempotent: every step checks before it changes, so re-running a partially
# completed host converges it rather than duplicating work.
#
# NOT DONE HERE, ON PURPOSE:
#   * .env.prod (DATABASE_URL, S3_*) — secrets never belong in a repo file.
#     Copy it over after this finishes:  scp .env.prod root@<host>:/apps/doc-ray/
#   * Managed-Postgres Trusted Sources — a respawned droplet gets a NEW public
#     IP, so put this host in the database's VPC and trust the VPC once, rather
#     than editing the firewall on every respawn.
#
# Exit codes: 0 host ready; 1 usage/precondition failure; 2 a step failed.

set -euo pipefail

# ------------------------------------------------------------------ settings --
OPERATOR_USER="${OPERATOR_USER:-pals}"
OPERATOR_EMAIL="${OPERATOR_EMAIL:-pedroanisio@faz.ai}"
OPERATOR_NAME="${OPERATOR_NAME:-Pedro Anisio Silva}"
# Public key that may log in as OPERATOR_USER. Required: a box with no key and
# no password is one you cannot get back into.
SSH_PUBKEY="${SSH_PUBKEY:-}"
APPS_DIR="${APPS_DIR:-/apps}"
DOCRAY_REPO="${DOCRAY_REPO:-https://github.com/pedroanisio/doc-ray.git}"
# Recorded on every telemetry row so GPU hours are attributable to a machine you
# can put on an invoice — a container id changes on every restart and names
# nothing. Defaults to the droplet hostname.
DOCRAY_WORKER_HOST="${DOCRAY_WORKER_HOST:-$(hostname)}"
INSTALL_GH="${INSTALL_GH:-1}"
# auto = provision the GPU lane iff a card is visible; 1 = require one; 0 = never.
REQUIRE_GPU="${REQUIRE_GPU:-auto}"
# Image used only to prove the CDI GPU reservation resolves. Must be a full
# patch version — see the verification step below. The container's userspace is
# independent of the host release, so a 24.04-based probe is correct on 22.04.
CUDA_PROBE_IMAGE="${CUDA_PROBE_IMAGE:-nvidia/cuda:13.0.1-base-ubuntu24.04}"
# Where to look for PCI hardware when nvidia-smi cannot enumerate a card.
# Overridable so the detection tests can point it at a fixture directory.
PCI_DEVICES_DIR="${PCI_DEVICES_DIR:-/sys/bus/pci/devices}"

export DEBIAN_FRONTEND=noninteractive

log()  { printf '\n=== %s\n' "$*"; }
warn() { printf '\nWARN: %s\n' "$*" >&2; }
die()  { printf '\nFATAL: %s\n' "$*" >&2; exit "${2:-2}"; }

# Run as the operator with a REAL home directory.
#
# `runuser -u USER --` drops privileges but does NOT reset the environment (only
# `-l` does that, and `-l` picks the user's login shell rather than bash). HOME
# therefore stays /root, and `bash -lc` does not fix it — a login shell sources
# profile files but never sets HOME itself. Every installer that writes to ~ then
# aims at root's home: oh-my-zsh dies outright with "cd: can't cd to /root", and
# `git config --global` would quietly try to write /root/.gitconfig.
#
# Setting the three login variables explicitly keeps bash semantics AND the
# non-interactive guarantee — unlike `su -l`, which opens an interactive shell
# and silently drops everything after it back to root.
#
# $operator_home is resolved from passwd before the first call below.
as_operator() {
  [[ -n "${operator_home:-}" ]] || die "internal: as_operator called before operator_home"
  runuser -u "$OPERATOR_USER" -- \
    env HOME="$operator_home" USER="$OPERATOR_USER" LOGNAME="$OPERATOR_USER" "$@"
}

as_operator_sh() { as_operator bash -lc "$1"; }

# ------------------------------------------------------- what is this host? ---
# Decided BEFORE anything is installed, so every later step can branch on it and
# the handoff text describes the machine you actually have.
#
# THE HARD CASE IS A CARD THAT IS PRESENT BUT NOT USABLE. `nvidia-smi -L` fails
# identically whether the machine has no GPU at all or has one whose driver is
# missing, still loading after a first boot, or mismatched against the CUDA
# build. Treating those two the same is how a GPU droplet gets provisioned as a
# CPU host, prints "Host ready", and only reveals the problem much later, when
# `make gpu-worker-up` dies with `unresolvable CDI devices nvidia.com/gpu=all` —
# an error that names neither the driver nor the provisioning step that was
# skipped. So ask the PCI bus as well. A card on the bus that nvidia-smi cannot
# drive stops the run: it is the one case where the operator is paying GPU rates
# for a machine that cannot use the card, and silence there is expensive.
detect_host_shape() {
  [[ "$REQUIRE_GPU" =~ ^(auto|0|1)$ ]] || die "REQUIRE_GPU must be auto, 0, or 1" 1

  if [[ "$REQUIRE_GPU" == "0" ]]; then
    HAS_GPU=0
    echo "GPU lane disabled by REQUIRE_GPU=0"
    return 0
  fi

  if nvidia-smi -L >/dev/null 2>&1; then
    HAS_GPU=1
    nvidia-smi -L
    return 0
  fi

  # nvidia-smi could not enumerate a card. Ask the bus what hardware is really
  # here.
  #
  # sysfs FIRST, lspci only as a fallback. Detection runs before this script has
  # installed anything, and lspci ships in pciutils, which a minimal image may
  # not carry — so making the safety check depend on it would reopen the very
  # silent-degradation hole it exists to close. /sys/bus/pci is always present on
  # Linux, needs no package, and does not require a working driver. Vendor id
  # 0x10de is NVIDIA.
  local card=""
  local detail=""
  local vendor_file
  if [[ -d "$PCI_DEVICES_DIR" ]]; then
    for vendor_file in "$PCI_DEVICES_DIR"/*/vendor; do
      [[ -r "$vendor_file" ]] || continue
      if [[ "$(cat "$vendor_file" 2>/dev/null)" == "0x10de" ]]; then
        card="NVIDIA PCI device $(basename "$(dirname "$vendor_file")") (vendor 0x10de)"
        break
      fi
    done
  fi
  if [[ -z "$card" ]] && command -v lspci >/dev/null 2>&1; then
    card="$(lspci 2>/dev/null | grep -i 'nvidia' || true)"
  fi

  if [[ -n "$card" ]]; then
    die "an NVIDIA card is on the PCI bus but nvidia-smi cannot drive it:
       $card
     Provisioning stops here rather than silently building a CPU-only box: this
     host is billed at GPU rates, and its worker pool would later fail with
     'unresolvable CDI devices nvidia.com/gpu=all' with nothing pointing back to
     this moment.
     The driver ships with DigitalOcean's AI/ML-ready image; this script does not
     install one, because a driver/CUDA mismatch is worse than a clear stop.
     Check 'nvidia-smi' directly. The worker image is built against CUDA 13.0, so
     the driver must report CUDA Version 13.0 or newer — an older driver
     enumerates the card yet cannot run the container.
     Rebuild the droplet from a GPU image, or pass REQUIRE_GPU=0 to provision
     this host as CPU-only on purpose." 1
  fi

  HAS_GPU=0
  if command -v nvidia-smi >/dev/null 2>&1; then
    detail="nvidia-smi is installed but lists no GPU, and no NVIDIA card is on the PCI bus"
  else
    detail="no nvidia-smi and no NVIDIA card on the PCI bus"
  fi
  if [[ "$REQUIRE_GPU" == "1" ]]; then
    die "REQUIRE_GPU=1 but no NVIDIA GPU is visible — $detail.
     The driver ships with DigitalOcean's AI/ML-ready image; this script does not
     install one, because a driver/CUDA mismatch is worse than a clear stop.
     Rebuild the droplet from a GPU image, then re-run." 1
  fi
  echo "CPU-only host — $detail; the GPU lane will be skipped"
}

# Sourcing with FRESH_INSTALL_LIB_ONLY=1 defines the helpers above and stops, so
# tests can exercise detect_host_shape without running an installer or being
# root. It sits before the root check for exactly that reason.
if [[ -n "${FRESH_INSTALL_LIB_ONLY:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi

[[ $EUID -eq 0 ]] || die "run as root" 1
[[ -n "$SSH_PUBKEY" ]] || die "set SSH_PUBKEY='ssh-ed25519 AAAA... you@host' before running" 1

log "Host shape"
detect_host_shape

# ------------------------------------------------------------- base packages --
log "Base packages"
apt-get update -y
# Deliberately NOT `apt-get upgrade`. On a GPU image the NVIDIA driver ships as
# apt packages: an unattended upgrade can install newer driver userspace against
# the running kernel module and turn a working card into
# "Failed to initialize NVML: Driver/library version mismatch" — a host this
# script would then refuse, for a fault it introduced. A box you intend to
# destroy does not need a distro upgrade. Patch deliberately if you must.
apt-get install -y \
  acl apt-transport-https autoconf autopoint automake btop ca-certificates \
  chrony curl direnv dnsutils dstat ffmpeg fio git gnupg gnupg-agent htop jq \
  libtool lsb-release ncdu net-tools nfs-common parted pkg-config psmisc pv \
  python3 python3-dev python3-pip python3-virtualenv qemu-guest-agent rsync \
  screen smartmontools sudo sysstat tmux traceroute tree ufw unison unzip vim \
  wget zsh

# -------------------------------------------------------------------- ufw ----
# Outbound stays open: the worker reaches managed Postgres and Spaces from here.
# Inbound is SSH only — this host serves no traffic.
#
# Caveat worth knowing: Docker programs its own iptables rules and BYPASSES ufw,
# so any container that publishes a port is reachable from the internet even
# while `ufw status` reads "deny incoming". That is safe here only because the
# worker overlay publishes nothing. Do not treat this rule set as protection for
# a host that runs the serving tier.
log "Firewall (inbound: SSH only)"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw --force enable
ufw status verbose

# ---------------------------------------------------------- operator account --
log "Operator account: $OPERATOR_USER"
if id -u "$OPERATOR_USER" >/dev/null 2>&1; then
  echo "user exists, leaving as-is"
else
  useradd -m -s /bin/zsh "$OPERATOR_USER"
fi

# Validate BEFORE installing: a malformed sudoers drop-in disables sudo entirely,
# on a box whose only other way in is the DO web console.
sudoers_tmp="$(mktemp)"
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$OPERATOR_USER" > "$sudoers_tmp"
visudo -cf "$sudoers_tmp" >/dev/null || die "generated sudoers entry is invalid"
install -m 0440 -o root -g root "$sudoers_tmp" "/etc/sudoers.d/$OPERATOR_USER"
rm -f "$sudoers_tmp"

log "SSH key"
operator_home="$(getent passwd "$OPERATOR_USER" | cut -d: -f6)"
install -d -m 0700 -o "$OPERATOR_USER" -g "$OPERATOR_USER" "$operator_home/.ssh"
authorized="$operator_home/.ssh/authorized_keys"
touch "$authorized"
# sshd refuses keys from a group/world-writable .ssh and ignores a file the user
# does not own — both are silent failures that look like "key rejected".
chown "$OPERATOR_USER:$OPERATOR_USER" "$authorized"
chmod 600 "$authorized"
if grep -qxF "$SSH_PUBKEY" "$authorized"; then
  echo "key already authorized"
else
  printf '%s\n' "$SSH_PUBKEY" >> "$authorized"
fi

# A per-host key for outbound git/ssh. Non-interactive: no prompt, no passphrase,
# and skipped when one already exists.
if [[ ! -f "$operator_home/.ssh/id_ed25519" ]]; then
  as_operator ssh-keygen -t ed25519 -N '' -C "$OPERATOR_EMAIL" \
    -f "$operator_home/.ssh/id_ed25519"
fi

# ----------------------------------------------------------------- docker ----
# ADOPT a working Docker; only install one when there isn't one.
#
# DigitalOcean's AI/ML-ready images ship Docker ALREADY WIRED TO THE GPU. The
# previous version of this script installed docker-ce unconditionally, which
# apt resolves by REMOVING docker.io/containerd/runc — and the replacement then
# inherits the image's /etc/docker/daemon.json, which names an nvidia runtime the
# new engine cannot resolve, so dockerd refuses to start. That turns a working
# host into a broken one, in the name of an upgrade nobody asked for.
#
# The only thing docker-ce buys us over the distro package is Compose v2, and
# that is a standalone CLI plugin we can add on its own.
docker_works() { docker info >/dev/null 2>&1; }
compose_works() { docker compose version >/dev/null 2>&1; }

add_docker_apt_repo() {
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
  fi
  tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get update -y
}

# Recover a dockerd that installed but will not start. Both causes are config
# the engine INHERITED from a predecessor, not the engine itself, so both fixes
# are "move the stale file aside and let the package default apply".
recover_docker() {
  warn "docker failed to start — trying the two known causes"
  if [[ -f /etc/docker/daemon.json ]]; then
    local backup="/etc/docker/daemon.json.pre-docker-ce.$$"
    warn "moving /etc/docker/daemon.json aside to $backup (it may name a runtime this engine lacks)"
    mv /etc/docker/daemon.json "$backup"
    systemctl restart docker && return 0
  fi
  if [[ -f /etc/containerd/config.toml ]]; then
    local backup="/etc/containerd/config.toml.pre-containerd-io.$$"
    warn "regenerating /etc/containerd/config.toml (old copy at $backup)"
    mv /etc/containerd/config.toml "$backup"
    containerd config default > /etc/containerd/config.toml
    systemctl restart containerd
    systemctl restart docker && return 0
  fi
  return 1
}

log "Docker engine"
if docker_works; then
  echo "docker already working (image-provided) — keeping it, not replacing it"
  if ! compose_works; then
    echo "adding Compose v2 only"
    add_docker_apt_repo
    apt-get install -y docker-compose-plugin
    compose_works || die "docker-compose-plugin installed but 'docker compose' does not resolve.
     This CLI does not scan /usr/libexec/docker/cli-plugins. Re-run with
     FORCE_DOCKER_CE=1 to swap in docker-ce instead."
  fi
elif [[ "${FORCE_DOCKER_CE:-0}" == "1" ]] || ! command -v docker >/dev/null 2>&1; then
  add_docker_apt_repo
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker || recover_docker || die "docker will not start. Diagnose with:
       systemctl status docker.service --no-pager -l
       journalctl -xeu docker.service --no-pager -n 60
       systemctl status containerd.service --no-pager -l"
else
  die "docker is installed but 'docker info' fails, and this script will not
     replace a broken engine blind — that is how a working GPU host gets wrecked.
     Diagnose first:
       systemctl status docker.service --no-pager -l
       journalctl -xeu docker.service --no-pager -n 60
     Then re-run, or force the swap with FORCE_DOCKER_CE=1."
fi
usermod -aG docker "$OPERATOR_USER"
# The group only takes effect on a NEW login, so every docker call below runs as
# root (this script) or through a fresh runuser login shell.
systemctl enable --now docker
docker_works || die "docker is enabled but 'docker info' still fails"

# -------------------------------------------------------- operator toolchain --
log "Operator shell + toolchain"
# Unattended: the upstream installer starts an interactive zsh otherwise, which
# would hang a scripted provision exactly the way `su -l` did.
if [[ ! -d "$operator_home/.oh-my-zsh" ]]; then
  as_operator_sh 'RUNZSH=no CHSH=no sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"'
fi
[[ -d "$operator_home/.nvm" ]] || \
  as_operator_sh 'curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.6/install.sh | bash'
[[ -x "$operator_home/.local/bin/uv" ]] || \
  as_operator_sh 'curl -LsSf https://astral.sh/uv/install.sh | sh'

# Dotfiles: clone to a stable path and copy, so a re-run refreshes rather than
# leaving an orphaned checkout in $HOME.
if [[ -d "$operator_home/.dotfiles/.git" ]]; then
  as_operator_sh 'git -C ~/.dotfiles pull --ff-only || true'
else
  as_operator_sh 'git clone https://github.com/pedroanisio/zshrc.git ~/.dotfiles'
fi
as_operator_sh 'cp -f ~/.dotfiles/.zshrc ~/.zshrc'

as_operator git config --global user.email "$OPERATOR_EMAIL"
as_operator git config --global user.name "$OPERATOR_NAME"

if [[ "$INSTALL_GH" == "1" ]] && ! command -v gh >/dev/null 2>&1; then
  log "GitHub CLI"
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg status=none
  chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | tee /etc/apt/sources.list.d/github-cli.list >/dev/null
  apt-get update -y
  apt-get install -y gh
  # No `gh auth login` — it is interactive, and the checkout below is a public
  # HTTPS clone that needs no credentials. Authenticate by hand if you need it.
fi

# ------------------------------------------------------------------- repo ----
log "doc-ray checkout"
install -d -o "$OPERATOR_USER" -g "$OPERATOR_USER" "$APPS_DIR"
if [[ -d "$APPS_DIR/doc-ray/.git" ]]; then
  as_operator git -C "$APPS_DIR/doc-ray" pull --ff-only
else
  as_operator git clone "$DOCRAY_REPO" "$APPS_DIR/doc-ray"
fi

# Persist the worker identity for every future login shell. Without this, an
# operator who later runs `make gpu-worker-up` from a fresh session gets the
# compose default (empty) and GPU hours stop being attributable to a machine —
# a silent loss, since nothing fails when the field is blank.
printf 'export DOCRAY_WORKER_HOST=%q\n' "$DOCRAY_WORKER_HOST" \
  > /etc/profile.d/docray-worker-host.sh
chmod 0644 /etc/profile.d/docray-worker-host.sh

# ---------------------------------------------------------- host convergence --
# provision_host.py is idempotent and stdlib-only, so it runs before any
# virtualenv exists. On a CPU-only box the GPU lane and the DOSCRATCH volume are
# skipped rather than failing; on a GPU box both are required, because a GPU
# host without its model cache thrashes the root disk.
log "Host convergence (docker, volumes, rpcbind lockdown)"
provision_args=(--no-sudo)
if [[ "$HAS_GPU" -eq 0 ]]; then
  provision_args+=(--no-gpu --no-scratch)
fi
python3 "$APPS_DIR/doc-ray/scripts/provision_host.py" "${provision_args[@]}"

# ------------------------------------------------------------ GPU readiness --
# Without the container toolkit and a CDI spec, `--device nvidia.com/gpu=all`
# does not resolve and the worker runs CPU-only WITHOUT ERROR —
# spacy.prefer_gpu() is a no-op when it sees no card. You would pay GPU rates for
# CPU throughput and only notice it in the job durations. provision_host.py has
# installed the toolkit and generated the spec by now; this proves it works.
if [[ "$HAS_GPU" -eq 1 ]]; then
  log "Verifying the container runtime can actually see the GPU"
  # Pin a FULL patch version: nvidia/cuda publishes no `13.0` rolling tag, so a
  # major.minor reference fails to resolve and this check dies on a host whose
  # GPU is in fact fine — a false negative that stops a good provision.
  docker run --rm --device nvidia.com/gpu=all "$CUDA_PROBE_IMAGE" nvidia-smi -L \
    || die "CDI GPU reservation failed — the worker would silently run on CPU"
fi

# ------------------------------------------------------------------ handoff --
log "Host ready"
# Prefer the address DO says is public: on a droplet inside a VPC the first
# entry from `hostname -I` is the PRIVATE interface, and an scp line pointing at
# it fails from anywhere outside the VPC.
host_ip="$(curl -fsS --max-time 2 \
  http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null \
  || hostname -I | awk '{print $1}')"

cat <<EOF

This host is provisioned. It is NOT yet processing, because it still needs the
credentials that must never live in a repo:

  1. Copy the production env file onto the box:
         scp .env.prod root@$host_ip:$APPS_DIR/doc-ray/

  2. Confirm the database trusts this host (VPC membership, or its IP in the
     managed cluster's Trusted Sources) — otherwise the worker starts and then
     fails to connect.
EOF

if [[ "$HAS_GPU" -eq 1 ]]; then
cat <<EOF

  3. Start the GPU worker pool (workers only; migrations belong to the always-on
     tier, never to a droplet you plan to destroy):

         cd $APPS_DIR/doc-ray
         DOCRAY_WORKERS=6 make gpu-worker-up

     DOCRAY_WORKER_HOST=$DOCRAY_WORKER_HOST is exported for you from
     /etc/profile.d/docray-worker-host.sh, so GPU hours stay attributable.

     Use gpu-worker-up, NOT gpu-up: only the former layers
     docker-compose.gpuworker.yml, which pins WORKER_ONLY_KINDS to the GPU lane
     and scales the data + serving tiers to zero. Without it this host claims
     user-facing fast-lane jobs — work that would then run on a machine you
     intend to destroy, and it would publish ports that ufw does not filter.

     Size DOCRAY_WORKERS by the SMALLEST of: VRAM / ~6 GB per trf worker, the
     host's vCPU count, and the managed cluster's spare backends (2 connections
     per worker). Leave headroom — a co-tenant GPU process (an Ollama model
     pinned with OLLAMA_KEEP_ALIVE=-1 holds its VRAM forever) has caused
     annotate jobs to burn their whole retry budget on CUDA OOM in seconds.

  4. Watch the queue drain, then DESTROY the droplet — a powered-off GPU droplet
     still bills:
         doctl compute droplet delete <name>

EOF
else
cat <<EOF

  3. This is a CPU-only host: the GPU lane was skipped, so do NOT run
     gpu-worker-up here. Bring up whichever tier this box is for, e.g.

         cd $APPS_DIR/doc-ray
         make prod-up

     GPU-bound job kinds (annotate, reannotate, visual_extract, semantic_embed,
     visual_embed) will run on CPU here if a worker claims them — correct, but
     slow enough to matter. Pin the lane deliberately rather than by accident.

EOF
fi
