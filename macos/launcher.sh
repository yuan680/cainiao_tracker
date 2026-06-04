#!/bin/bash
# CainiaoTracker macOS 启动脚本
# 双击 .app 时，通过 AppleScript 打开 Terminal.app 运行实际程序

# 获取 .app bundle 中 Resources 目录的路径
RESOURCES_DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
EXECUTABLE="$RESOURCES_DIR/cainiao_tracker"

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

# 通过 AppleScript 打开 Terminal 运行程序
osascript <<EOF
tell application "Terminal"
    activate
    do script "clear && echo '========================================' && echo '   菜鸟国际物流批量查询工具' && echo '========================================' && echo '' && echo '数据目录: ~/Library/Application Support/CainiaoTracker/' && echo '' && '$EXECUTABLE' ; echo '' ; echo '程序运行完毕，按回车关闭窗口...' ; read"
end tell
EOF
