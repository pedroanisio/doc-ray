#!/usr/bin/env bash
#
# Bootstrap a fresh DigitalOcean H200 GPU Droplet into a doc-ray GPU worker host.
#
# This host runs WORKERS ONLY. The corpus (managed PostgreSQL), the blobs
# (Spaces) and the serving tier (api/webapp/mcp) live elsewhere; nothing durable
# is kept here, because the box exists to be destroyed when the queue drains.
# It therefore never runs migrations — the always-on tier owns the schema, and a
# machine you intend to delete must not be the one that changes it.
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

export DEBIAN_FRONTEND=noninteractive

log()  { printf '\n=== %s\n' "$*"; }
die()  { printf '\nFATAL: %s\n' "$*" >&2; exit "${2:-2}"; }

as_operator() { runuser -u "$OPERATOR_USER" -- "$@"; }

# Runs a command in the operator's login shell so installers see a real HOME.
# runuser keeps this a non-interactive child of THIS script — unlike `su -l`,
# which opens an interactive shell and silently drops everything after it back
# to root, which is how the previous version installed the operator's toolchain
# into /root.
as_operator_sh() { runuser -u "$OPERATOR_USER" -- bash -lc "$1"; }

[[ $EUID -eq 0 ]] || die "run as root" 1
[[ -n "$SSH_PUBKEY" ]] || die "set SSH_PUBKEY='ssh-ed25519 AAAA... you@host' before running" 1

# ------------------------------------------------------------- base packages --
log "Base packages"
apt-get update -y
apt-get upgrade -y
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
log "Docker engine"
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
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
usermod -aG docker "$OPERATOR_USER"
# The group only takes effect on a NEW login, so every docker call below runs as
# root (this script) or through a fresh runuser login shell.
systemctl enable --now docker

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

# ------------------------------------------------------------ GPU readiness --
# THE step the previous version omitted. Without the NVIDIA driver, the container
# toolkit and a CDI spec, `--device nvidia.com/gpu=all` does not resolve and the
# worker runs CPU-only WITHOUT ERROR — spacy.prefer_gpu() is a no-op when it sees
# no card. You would pay GPU rates for CPU throughput and only notice it in the
# job durations.
log "GPU readiness (driver, container toolkit, CDI)"
if ! nvidia-smi -L >/dev/null 2>&1; then
  die "no NVIDIA GPU visible (nvidia-smi -L failed).
     The driver ships with DigitalOcean's AI/ML-ready image; this script does not
     install one, because a driver/CUDA mismatch is worse than a clear stop.
     Rebuild the droplet from a GPU image, then re-run."
fi
# provision_host.py owns the rest and is idempotent: nvidia-container-toolkit,
# /etc/cdi/nvidia.yaml, docker's CDI feature, the scratch volume, rpcbind
# lockdown. Stdlib-only, so it runs before any virtualenv exists.
python3 "$APPS_DIR/doc-ray/scripts/provision_host.py" --no-sudo

log "Verifying the container runtime can actually see the GPU"
docker run --rm --device nvidia.com/gpu=all nvidia/cuda:13.0-base-ubuntu24.04 nvidia-smi -L \
  || die "CDI GPU reservation failed — the worker would silently run on CPU"

# ------------------------------------------------------------------ handoff --
log "Host ready"
host_ip="$(hostname -I | awk '{print $1}')"
cat <<EOF

This host is provisioned. It is NOT yet processing, because it still needs the
credentials that must never live in a repo:

  1. Copy the production env file onto the box:
         scp .env.prod root@$host_ip:$APPS_DIR/doc-ray/

  2. Confirm the database trusts this host (VPC membership, or its IP in the
     managed cluster's Trusted Sources) — otherwise the worker starts and then
     fails to connect.

  3. Start the GPU worker pool (workers only; migrations belong to the always-on
     tier, never to a droplet you plan to destroy):

         cd $APPS_DIR/doc-ray
         DOCRAY_WORKERS=6 DOCRAY_WORKER_HOST=$DOCRAY_WORKER_HOST make gpu-up

     Size DOCRAY_WORKERS by the SMALLEST of: VRAM / ~6 GB per trf worker, the
     host's vCPU count, and the managed cluster's spare backends (2 connections
     per worker). Leave headroom — a co-tenant GPU process (an Ollama model
     pinned with OLLAMA_KEEP_ALIVE=-1 holds its VRAM forever) has caused
     annotate jobs to burn their whole retry budget on CUDA OOM in seconds.

  4. Watch the queue drain, then DESTROY the droplet — a powered-off GPU droplet
     still bills:
         doctl compute droplet delete <name>

EOF
