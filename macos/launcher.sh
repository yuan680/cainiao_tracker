#!/bin/bash
# CainiaoTracker macOS 启动脚本
# 双击 .app 时在 Terminal 中运行一次查询后退出

# 获取 .app bundle 中 Resources 目录的路径
RESOURCES_DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
EXECUTABLE="$RESOURCES_DIR/cainiao_tracker"

# 数据目录
DATA_DIR="$HOME/Library/Application Support/CainiaoTracker"
mkdir -p "$DATA_DIR"

# 检查可执行文件是否存在
if [ ! -f "$EXECUTABLE" ]; then
    osascript -e 'display dialog "错误：找不到 cainiao_tracker 可执行文件。\n请重新安装应用。" buttons {"确定"} default button 1 with icon stop with title "菜鸟物流查询"'
    exit 1
fi

# 检查 Chrome 是否安装
if [ ! -d "/Applications/Google Chrome.app" ] && [ ! -d "$HOME/Applications/Google Chrome.app" ]; then
    osascript -e 'display dialog "请先安装 Google Chrome 浏览器。\n\n下载地址: https://www.google.com/chrome/" buttons {"确定"} default button 1 with icon caution with title "菜鸟物流查询"'
    open "https://www.google.com/chrome/"
    exit 1
fi

# ============ 在 Terminal 中运行查询 ============
osascript <<EOF
tell application "Terminal"
    activate
    do script "clear && echo '========================================' && echo '   菜鸟国际物流批量查询工具' && echo '========================================' && echo '' && echo '数据目录: ~/Library/Application Support/CainiaoTracker/' && echo '' && echo '--- 正在执行查询 ---' && echo '' && '$EXECUTABLE' ; echo '' ; echo '查询完成！' ; echo '' ; echo '按回车关闭窗口...' ; read"
end tell
EOF
