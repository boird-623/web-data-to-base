#!/usr/bin/env bash
# 仅在 setup_linux_keyring.sh 输出 KEYRING_NEEDS_GUI 时运行：
# 在图形会话（含 Xvfb）上自动创建一个“空密码、自动解锁”的默认 keyring。
# 空密码 keyring 会被持久化到 HOME，之后无需再运行本脚本。
set -euo pipefail

PERSIST_DATA_HOME="${PERSIST_DATA_HOME:-$HOME/.local/share}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/dbus-session-bus}"
export XDG_DATA_HOME="$PERSIST_DATA_HOME"
export HOME="${HOME_OVERRIDE:-$HOME}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "${DISPLAY:-}" ]; then
  echo "需要图形环境 DISPLAY（云沙箱通常为 :99.0）；无法创建 keyring。"
  exit 2
fi
command -v xdotool >/dev/null 2>&1 || { echo "缺少 xdotool"; exit 2; }

cat >/tmp/_kb_create.py <<'PY'
import sys, secretstorage
from contextlib import closing
with closing(secretstorage.dbus_init()) as conn:
    if not secretstorage.check_service_availability(conn):
        print("service unavailable"); sys.exit(1)
    try:
        secretstorage.get_default_collection(conn); print("already exists")
    except Exception:
        secretstorage.create_collection(conn, "Default keyring", alias="default"); print("created")
PY

# 后台触发，图形对话框会弹出
XDG_DATA_HOME="$PERSIST_DATA_HOME" python3 /tmp/_kb_create.py >/tmp/_kb_create.out 2>&1 &
CREATE_PID=$!
sleep 5

# 对话框1：密码与确认都留空，Tab 到 Continue 并回车
xdotool key Tab  # -> Confirm
xdotool key Tab  # -> Cancel
xdotool key Tab  # -> Continue
xdotool key Return
sleep 2

# 对话框2：“Store passwords unencrypted?”，焦点默认在 Cancel，Tab 到 Continue 回车
xdotool key Tab
xdotool key Return
wait "$CREATE_PID" 2>/dev/null || true

cat /tmp/_kb_create.out
echo "keyring 文件:"; ls -la "$PERSIST_DATA_HOME/keyrings/" 2>/dev/null || true
