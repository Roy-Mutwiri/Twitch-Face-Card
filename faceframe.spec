# PyInstaller build: .venv\Scripts\python -m PyInstaller faceframe.spec --noconfirm
# Produces dist\FaceFrame\FaceFrame.exe (onedir; includes CUDA PyTorch, models and the extension).
from PyInstaller.utils.hooks import collect_submodules
block_cipher = None
a = Analysis(['faceframe_main.py'], pathex=['.'],
             datas=[('models/face_detection_yunet_2023mar.onnx', 'models'),
                    ('models/realesr-general-x4v3.pth', 'models'),
                    ('models/RealESRGAN_x2plus.pth', 'models'),
                    ('extension', 'extension'), ('README.md', '.')],
             hiddenimports=['faceframe.core.sr_engine', 'faceframe.core.sr_models', 'pynvml', 'psutil',
                            'websockets.asyncio.server'] + collect_submodules('streamlink.plugins') + ['av'],
             excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtPdf',
                       'PySide6.Qt3DCore', 'PySide6.QtQuick', 'PySide6.QtQml', 'tkinter', 'pytest'],
             noarchive=False)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='FaceFrame', console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='FaceFrame')
