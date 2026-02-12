# -*- mode: python ; coding: utf-8 -*-
"""
OddOpp PyInstaller spec file.

Build with:  pyinstaller oddopp.spec --clean --noconfirm
Output:      dist/OddOpp.exe
"""

from pathlib import Path

block_cipher = None

a = Analysis(
    ['backend/launcher.py'],
    pathex=['backend'],
    binaries=[],
    datas=[
        # Frontend static build
        ('frontend/dist', 'frontend/dist'),

        # Config files (bundled defaults)
        ('backend/src/config/providers.yaml', 'config'),
        ('backend/src/config/sports.yaml', 'config'),

        # Team name aliases
        ('backend/src/matching/aliases.yaml', 'matching'),

        # Boost scraper scripts (imported as package by scheduler)
        ('backend/scripts/__init__.py', 'scripts'),
        ('backend/scripts/scrape_specials.py', 'scripts'),
    ],
    hiddenimports=[
        # --- SQLAlchemy ---
        'sqlalchemy.ext.declarative',
        'sqlalchemy.sql.default_comparator',

        # --- uvicorn ---
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.wsproto_impl',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'uvicorn.lifespan.off',

        # --- FastAPI / Starlette ---
        'multipart',
        'python_multipart',

        # --- aiohttp ---
        'aiohttp',

        # --- Pydantic ---
        'pydantic.deprecated.decorator',

        # --- pywebview ---
        'webview',
        'webview.platforms',
        'webview.platforms.edgechromium',
        'clr_loader',
        'pythonnet',

        # --- Provider modules (dynamically loaded by factory) ---
        'src.providers.pinnacle',
        'src.providers.polymarket',
        'src.providers.kambi',
        'src.providers.altenar',
        'src.providers.gecko_v2',
        'src.providers.spectate',
        'src.providers.snabbare',
        'src.providers.comeon_multileague',
        'src.providers.hajper',
        'src.providers.vbet',
        'src.providers.interwetten',
        'src.providers.coolbet',
        'src.providers.tipwin',
        'src.providers.tenbet',
        'src.providers.mixins.rsocket',

        # --- Matching ---
        'thefuzz',
        'thefuzz.fuzz',
        'thefuzz.process',

        # --- Boost scraper ---
        'scripts.scrape_specials',

        # --- Other ---
        'yaml',
        'dotenv',
        'websockets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Dev/test tools — not needed in production
        'pytest',
        'black',
        'isort',
        'mypy',
        'ruff',
        'tkinter',
        '_tkinter',
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
    name='OddOpp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,         # GUI mode — no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='frontend/public/terminal.ico' if Path('frontend/public/terminal.ico').exists() else None,
)
