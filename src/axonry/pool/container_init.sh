#!/bin/bash
# Entrypoint of an owned host's containers (pool/host.sh): sshd for the orchestrator's key, as on a vast instance.
# AXONRY_CHINA=1: Ubuntu packages through a mirror. AXONRY_ROLE=eval: benchmark items are supplied, never fetched.
set -e
if ! command -v sshd >/dev/null; then
  if [ "${AXONRY_CHINA:-}" = 1 ]; then
    sed -i 's#http://\(archive\|security\).ubuntu.com#https://mirrors.tuna.tsinghua.edu.cn#g' \
      /etc/apt/sources.list.d/ubuntu.sources /etc/apt/sources.list 2>/dev/null || true
  fi
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server >/dev/null
fi
if [ "$(cat /etc/ssh/.axonry-host 2>/dev/null)" != "$(hostname)" ]; then   # a new container: its own host keys
  rm -f /etc/ssh/ssh_host_*
  ssh-keygen -A >/dev/null
fi
mkdir -p /run/sshd /root/.ssh && chmod 700 /root/.ssh
install -m 600 /srv/axonry-init/authorized_keys /root/.ssh/authorized_keys
if [ "${AXONRY_ROLE:-}" = eval ] && ! grep -q HF_HUB_OFFLINE /etc/environment 2>/dev/null; then
  echo HF_HUB_OFFLINE=1 >> /etc/environment
fi
hostname > /etc/ssh/.axonry-host
exec /usr/sbin/sshd -D -e
