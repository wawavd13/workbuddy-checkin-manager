# -*- mode: python ; coding: utf-8 -*-
# macOS .app bundle build: python -m PyInstaller --noconfirm WorkBuddyManager-macos.spec
# The legacy tkinter fallback is intentionally excluded (needs python-tk; Qt is the UI).

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('vendor/auth_backend.py', 'vendor')],
    hiddenimports=['runner'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['legacy_tk_app', 'tkinter'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WorkBuddyManager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

app = BUNDLE(
    exe,
    a.binaries,
    a.datas,
    name='WorkBuddyManager.app',
    bundle_identifier='com.wawavd13.workbuddy-manager',
    info_plist={
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
        'CFBundleShortVersionString': '1.1.0',
        'CFBundleVersion': '1.1.0',
        'NSHumanReadableCopyright': 'MIT License',
    },
)
