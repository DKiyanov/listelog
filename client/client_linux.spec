# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['client.py'],
    pathex=[],
    binaries=[
        ('/usr/lib/x86_64-linux-gnu/libmpv.so.2', '.'),
        ('/usr/lib/x86_64-linux-gnu/libsecret-1.so.0', '.'),
        ('/usr/lib/x86_64-linux-gnu/libpulse.so.0', '.'),
        ('/usr/lib/x86_64-linux-gnu/libgstapp-1.0.so.0', '.'),
    ],
    datas=[('assets', 'assets'), ('flet_client/linux', 'flet_client/linux')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='listelog',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
	icon=['assets/icon.png']	
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='listelog',
)
