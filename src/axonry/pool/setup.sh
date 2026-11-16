#!/bin/bash
# Machine setup (pool/vast.py), run as root on a fresh vast instance of the pinned vLLM image: the competition
# toolkit's runtime and ours, the competition repo, the verified base(s), and the unprivileged `agent` user the run's
# agent logs in as. Ends with /srv/axonry/READY, or /srv/axonry/FAILED. Argument: the GPUs the machine must have.
#
# Layout: /srv/axonry/models/<org>__<name>@<rev>/ (verified), /srv/axonry/repos/WeightSpaceProgramming/,
# /srv/axonry/wsp_work/ (evaluation: benchmark data cache), /srv/axonry/eval/ (evaluation jobs). For the agent:
# /workspace/base, /workspace/models/<dir>, /workspace/WeightSpaceProgramming (read-only), /workspace/work (its own).
set -euo pipefail
R=/srv/axonry
B=$(cd "$(dirname "$0")" && pwd)
rm -f $R/READY $R/FAILED
trap 'echo "setup failed at line $LINENO" >&2; mkdir -p $R; touch $R/FAILED' ERR

test "$(nvidia-smi -L | wc -l)" -eq "$1"

pip install -q --no-cache-dir math-verify lm-eval langdetect immutabledict nltk datasets flash-linear-attention boto3
python3 -m nltk.downloader -q -d /usr/share/nltk_data punkt_tab punkt || true   # exits 0 even when it failed:
NLTK_DATA=/usr/share/nltk_data python3 -c "import nltk; nltk.data.find('tokenizers/punkt_tab/english/')"   # IFEval needs it
pip install -q --no-cache-dir --no-deps "$B/axonry.tar.gz"
for t in time check; do
  printf '#!/bin/sh\nexec python3 -m axonry.sandbox_tools %s "$@"\n' $t > /usr/local/bin/axonry-$t
  chmod 755 /usr/local/bin/axonry-$t
done

mkdir -p $R/models $R/repos/WeightSpaceProgramming $R/wsp_work $R/eval /workspace/models
chmod 700 $R/wsp_work $R/eval          # evaluation data: never readable by the agent user
tar -xf "$B/wsp.tar" -C $R/repos/WeightSpaceProgramming
for doc in "$B"/bases/*.json; do
  d=$(basename "$doc" .json)
  python3 "$B/stage_base.py" "$doc" "$R/models/$d"
  ln -sfn "$R/models/$d" "/workspace/models/$d"
  ln -sfn "$R/models/$d" /workspace/base       # one base per season
done
ln -sfn $R/repos/WeightSpaceProgramming /workspace/WeightSpaceProgramming
chmod -R a+rX,go-w $R/models $R/repos

# the agent: no password, no sudo, its home is its work directory; root's files and the provider's secrets stay
# root's. vast copies the container's environment (HOME=/root included) into /etc/environment, which PAM applies to
# every ssh session: only what describes the image stays.
id agent >/dev/null 2>&1 || useradd -M -d /workspace/work -s /bin/bash agent
usermod -p '*' agent
gpasswd -d agent sudo >/dev/null 2>&1 || true
install -d -o agent -g agent -m 755 /workspace/work
chmod 700 /root
sed -i '/^\(HOME\|PWD\|USER\)=/d; /KEY\|TOKEN\|SECRET\|PASSWORD/d' /etc/environment 2>/dev/null || true
cat >> /etc/ssh/sshd_config <<'S'
Match User agent
    AllowTcpForwarding no
    X11Forwarding no
    PermitTunnel no
    SetEnv WSP_WORK=/workspace/work/wsp WSP_CASES=/workspace/work/cases WSP_MODELS_DIR=/workspace/models PYTHONPATH=/workspace/WeightSpaceProgramming HF_HUB_OFFLINE=1 NLTK_DATA=/usr/share/nltk_data
S
sshd -t
pkill -HUP -x sshd || true

python3 -c "import torch; n = torch.cuda.device_count(); assert n == $1, n; [torch.ones(1, device=f'cuda:{i}') for i in range(n)]"
date +%s > $R/READY
