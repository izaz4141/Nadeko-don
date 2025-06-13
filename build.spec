# build.spec
block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[os.getcwd()],
    binaries=[
        ('/usr/lib/qt6/plugins/platformthemes', 'plugins/platformthemes'),
        ('/usr/lib/qt6/plugins/styles', 'plugins/styles'),
    ],
    datas=[
        ('assets/*', 'assets'),
        ('gui/*.py', 'gui'),
        ('network/*.py', 'network'),
        ('utils/*.py', 'utils'),
    ],
    hiddenimports=[
        'network',
        'utils',
        'gui'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "unittest", "pydoc",
        "setuptools", "distutils", "test", "tomllib",
        "multiprocessing", "pkg_resources",
        "xmlrpc", "lib2to3", "sqlite3",
        "matplotlib", "scipy", "numpy", "pandas", "PIL", "torch", "tensorflow", "cv2",
        "cryptography"
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
    name='nadeko-don',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Set to True if you need console
    icon='assets/nadeko-don.ico', 
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)