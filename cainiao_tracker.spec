# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec 文件 - 菜鸟物流查询工具 macOS 打包配置

用法（在 macOS 上执行）:
    pyinstaller cainiao_tracker.spec
"""

import sys
import os

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'DrissionPage',
        'DrissionPage._pages.chromium_page',
        'DrissionPage._pages.chromium_tab',
        'DrissionPage._configs.chromium_options',
        'DrissionPage._units.actions',
        'DrissionPage._units.listener',
        'DrissionPage._units.waiter',
        'DrissionPage._units.setter',
        'DrissionPage._units.screencast',
        'DrissionPage._units.cookies_setter',
        'DrissionPage._units.downloader',
        'DrissionPage._units.rect',
        'DrissionPage._units.scroller',
        'DrissionPage._units.states',
        'DrissionPage._elements',
        'DrissionPage._base',
        'websocket',
        'requests',
        'certifi',
        'urllib3',
        'charset_normalizer',
        'tldextract',
        'lxml',
        'cssselect',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'PIL',
        'pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='cainiao_tracker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,  # macOS 上 UPX 可能导致问题
    console=True,  # 终端应用
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,  # 自动检测架构
    codesign_identity=None,
    entitlements_file=None,
)
