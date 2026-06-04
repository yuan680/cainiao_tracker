#!/bin/bash
# ============================================================
# macOS 构建脚本 - 菜鸟物流查询工具
# 
# 在 macOS 上运行此脚本来：
# 1. PyInstaller 打包 Python 为单文件可执行
# 2. 组装 .app bundle
# 3. 生成 .dmg 安装包
#
# 前置条件:
#   - Python 3.9+
#   - pip install -r requirements.txt pyinstaller
#   - brew install create-dmg (可选，用于生成美观的 DMG)
# ============================================================

set -e

echo "=========================================="
echo "  菜鸟物流查询工具 - macOS 构建"
echo "=========================================="

# 项目根目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 输出目录
BUILD_DIR="$SCRIPT_DIR/build"
DIST_DIR="$SCRIPT_DIR/dist"
APP_NAME="CainiaoTracker"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
DMG_NAME="CainiaoTracker.dmg"

# 清理旧构建
echo ""
echo "[1/4] 清理旧构建..."
rm -rf "$BUILD_DIR" "$DIST_DIR"

# PyInstaller 打包
echo ""
echo "[2/4] PyInstaller 打包..."
pyinstaller cainiao_tracker.spec --noconfirm
echo "  ✓ 可执行文件: $DIST_DIR/cainiao_tracker"

# 验证产物
if [ ! -f "$DIST_DIR/cainiao_tracker" ]; then
    echo "  ✗ 错误：PyInstaller 构建失败，找不到输出文件"
    exit 1
fi

# 组装 .app bundle
echo ""
echo "[3/4] 组装 .app bundle..."
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

# 复制文件
cp "$SCRIPT_DIR/macos/Info.plist" "$APP_BUNDLE/Contents/"
cp "$SCRIPT_DIR/macos/launcher.sh" "$APP_BUNDLE/Contents/MacOS/"
cp "$DIST_DIR/cainiao_tracker" "$APP_BUNDLE/Contents/Resources/"

# 设置可执行权限
chmod +x "$APP_BUNDLE/Contents/MacOS/launcher.sh"
chmod +x "$APP_BUNDLE/Contents/Resources/cainiao_tracker"

# 如果有图标文件则复制
if [ -f "$SCRIPT_DIR/macos/icon.icns" ]; then
    cp "$SCRIPT_DIR/macos/icon.icns" "$APP_BUNDLE/Contents/Resources/"
fi

echo "  ✓ 应用包: $APP_BUNDLE"

# 生成 .dmg
echo ""
echo "[4/4] 生成 DMG 安装包..."

DMG_STAGING="$DIST_DIR/dmg_staging"
mkdir -p "$DMG_STAGING"
cp -R "$APP_BUNDLE" "$DMG_STAGING/"

# 添加 Applications 快捷方式（方便用户拖拽安装）
ln -sf /Applications "$DMG_STAGING/Applications"

# 尝试使用 create-dmg（更美观）
if command -v create-dmg &> /dev/null; then
    create-dmg \
        --volname "$APP_NAME" \
        --volicon "$SCRIPT_DIR/macos/icon.icns" 2>/dev/null \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "$APP_NAME.app" 150 200 \
        --app-drop-link 450 200 \
        --no-internet-enable \
        "$DIST_DIR/$DMG_NAME" \
        "$DMG_STAGING/" \
    || {
        # create-dmg 有时因为图标问题失败，fallback 到 hdiutil
        echo "  create-dmg 失败，使用 hdiutil 替代..."
        hdiutil create -volname "$APP_NAME" \
            -srcfolder "$DMG_STAGING" \
            -ov -format UDZO \
            "$DIST_DIR/$DMG_NAME"
    }
else
    # 使用系统自带的 hdiutil
    hdiutil create -volname "$APP_NAME" \
        -srcfolder "$DMG_STAGING" \
        -ov -format UDZO \
        "$DIST_DIR/$DMG_NAME"
fi

# 清理临时文件
rm -rf "$DMG_STAGING"

echo ""
echo "=========================================="
echo "  构建完成!"
echo "=========================================="
echo ""
echo "  .app 路径: $APP_BUNDLE"
echo "  .dmg 路径: $DIST_DIR/$DMG_NAME"
echo "  文件大小: $(du -sh "$DIST_DIR/$DMG_NAME" | cut -f1)"
echo ""
