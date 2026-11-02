#!/usr/bin/env bash
# Linux 云沙箱密钥库引导（幂等）。
# 作用：确保 Freedesktop Secret Service 可用，并让密钥库数据持久化到用户主目录，
#      这样容器/系统层重置后只需重跑本脚本，已存的平台密码不必重新输入。
# 仅在 Linux 且 secure_credentials 报 linux_secret_service_unavailable 时使用。
# Windows / macOS 无需运行（分别使用 DPAPI / 登录钥匙串）。
set -euo pipefail

PERSIST_DATA_HOME="${PERSIST_DATA_HOME:-$HOME/.local/share}"
SVC_FILE="/usr/share/dbus-1/services/org.freedesktop.secrets.service"
DBUS_ADDR="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/dbus-session-bus}"
export DBUS_SESSION_BUS_ADDRESS="$DBUS_ADDR"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "[setup_linux_keyring] $*"; }

# 1) 安装 gnome-keyring
if ! command -v gnome-keyring-daemon >/dev/null 2>&1; then
  log "未找到 gnome-keyring-daemon，尝试 apt 安装（需要 sudo）..."
  sudo apt-get update -y
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y gnome-keyring
fi

# 2) 持久化目录
mkdir -p "$PERSIST_DATA_HOME/keyrings"

# 3) 覆盖 dbus 服务文件，强制使用持久 HOME
NEED_SVC=1
if [ -f "$SVC_FILE" ] && grep -q "XDG_DATA_HOME=$PERSIST_DATA_HOME" "$SVC_FILE"; then
  NEED_SVC=0
fi
if [ "$NEED_SVC" = "1" ]; then
  log "写入 dbus secrets 服务覆盖 -> $PERSIST_DATA_HOME"
  sudo tee "$SVC_FILE" >/dev/null <<EOF
[D-BUS Service]
Name=org.freedesktop.secrets
Exec=/usr/bin/env XDG_DATA_HOME=$PERSIST_DATA_HOME HOME=$HOME /usr/bin/gnome-keyring-daemon --start --foreground --components=secrets
EOF
  # 通知 dbus 重新加载
  DBUS_PID=$(pgrep -x dbus-daemon | head -1 || true)
  [ -n "$DBUS_PID" ] && kill -HUP "$DBUS_PID" || true
fi

# 4) 清理旧 daemon，让 dbus 用覆盖后的配置重新激活
for p in $(pgrep -x gnome-keyring-d) $(pgrep -x gnome-keyring-daemon); do
  kill -9 "$p" 2>/dev/null || true
done
sleep 1

# 5) 探测 Secret Service 是否可用；不可用则手动启动一次
available() {
  XDG_DATA_HOME="$PERSIST_DATA_HOME" HOME="$HOME" python3 - "$SCRIPT_DIR" <<'PY'
import sys, secretstorage
from contextlib import closing
sys.path.insert(0, sys.argv[1])
with closing(secretstorage.dbus_init()) as conn:
    print("yes" if secretstorage.check_service_availability(conn) else "no")
PY
}

STATE="$(available | tr -d '[:space:]')"
if [ "$STATE" != "yes" ]; then
  log "手动启动 gnome-keyring-daemon ..."
  XDG_DATA_HOME="$PERSIST_DATA_HOME" HOME="$HOME" nohup gnome-keyring-daemon \
    --start --components=secrets,pkcs11,ssh,gpg >/tmp/gnome-keyring-bootstrap.log 2>&1 &
  sleep 3
fi

# 6) 检查默认 keyring；若不存在且有图形环境，自动创建空密码 keyring
XDG_DATA_HOME="$PERSIST_DATA_HOME" HOME="$HOME" python3 - "$SCRIPT_DIR" <<'PY'
import sys, secretstorage
sys.path.insert(0, sys.argv[1])
from contextlib import closing
with closing(secretstorage.dbus_init()) as conn:
    if not secretstorage.check_service_availability(conn):
        print("KEYRING_NEEDS_GUI")
    else:
        try:
            dc = secretstorage.get_default_collection(conn)
            if dc.is_locked():
                try:
                    dc.unlock()  # 空密码 keyring 会自动解锁
                except Exception:
                    pass
            print("KEYRING_READY" if not dc.is_locked() else "KEYRING_LOCKED")
        except Exception:
            print("KEYRING_NEEDS_GUI")
PY
