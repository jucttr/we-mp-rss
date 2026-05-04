#!/bin/bash
set -e

cd /app/
plantform="$(uname -m)"
PLANT_PATH=${PLANT_PATH:-/app/env}
plant="${PLANT_PATH}_${plantform}"
source /app/environment.sh
source "$plant/bin/activate"

# 启动 Xvfb（如果需要非 headless 模式）
if [ "$HEADLESS" != "true" ] || [ "$ENABLE_XVFB" = "true" ]; then
    echo "启动 Xvfb 虚拟 X Server..."
    export DISPLAY=:99

    # 清理旧的 Xvfb 进程和锁文件
    pkill -f "Xvfb :99" 2>/dev/null || true
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true

    # 等待清理完成
    sleep 1

    # 启动 Xvfb
    Xvfb :99 -screen 0 1920x1080x24 -ac &
    XVFB_PID=$!
    echo "Xvfb 已启动 (PID: $XVFB_PID, DISPLAY=$DISPLAY)"

    # 等待 Xvfb 启动
    sleep 2

    # 验证 Xvfb 是否启动成功
    if ! ps -p $XVFB_PID > /dev/null 2>&1; then
        echo "Xvfb 启动失败，尝试使用现有 Xvfb..."
        # 尝试使用已存在的 Xvfb
        export DISPLAY=:99
    fi
fi

python3 main.py -job True -init True
