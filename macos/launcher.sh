#!/bin/bash
# CainiaoTracker macOS 启动脚本
# 双击 .app 时：
# 1. 自动安装 LaunchAgent（开机自启）
# 2. 在 Terminal 中以定时循环模式运行

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

# ============ 安装 LaunchAgent（开机自启） ============
PLIST_LABEL="com.cainiaotracker.autoquery"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

# 先卸载旧服务（如果存在）
if [ -f "$PLIST_PATH" ]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null
fi

# 生成 plist 文件
cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${EXECUTABLE}</string>
        <string>--once</string>
    </array>

    <key>StartInterval</key>
    <integer>7200</integer>

    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${DATA_DIR}/launchd_stdout.log</string>

    <key>StandardErrorPath</key>
    <string>${DATA_DIR}/launchd_stderr.log</string>

    <key>WorkingDirectory</key>
    <string>${DATA_DIR}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
PLIST

# 加载服务
launchctl load "$PLIST_PATH" 2>/dev/null

# ============ 在 Terminal 中运行首次查询 ============
osascript <<EOF
tell application "Terminal"
    activate
    do script "clear && echo '========================================' && echo '   菜鸟国际物流批量查询工具' && echo '========================================' && echo '' && echo '数据目录: ~/Library/Application Support/CainiaoTracker/' && echo '' && echo '✓ 开机自启服务已安装（每2小时自动查询）' && echo '  如需卸载: $EXECUTABLE --uninstall' && echo '' && echo '--- 正在执行首次查询 ---' && echo '' && '$EXECUTABLE' ; echo '' ; echo '查询完成！后续将每2小时自动执行。' ; echo '可以关闭此窗口，后台服务会继续运行。' ; echo '' ; echo '按回车关闭窗口...' ; read"
end tell
EOF
