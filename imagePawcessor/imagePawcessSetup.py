import sys
from cx_Freeze import setup, Executable


build_exe_options = {
    "packages": [
        "os",
        "sys",
        "random",
        "threading",
        "tempfile",
        "json",
        "time",
        "math",
        "pathlib",
        "socket",
        "filelock",
        "concurrent.futures",
        "webbrowser",
        "cv2",
        "PIL",
        "numpy",
        "shutil",
        "sklearn",
        "joblib",
        "PySide6",
        "onnxruntime",
    ],
    "includes": [
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "html",
    ],
    "include_files": [
    ],
    "include_msvcr": True,
    "excludes": [
        "tkinter",
        "email",
        "http",
        "xml",
    ],
    "optimize": 2,
}

# Base settings
base = None
if sys.platform == "win32":
    base = "Win32GUI"

# Executable settings
executables = [
    Executable(
        script="imagePawcess.py",
        base=base,
        target_name="imagePawcess.exe",
        icon="app_icon.ico",
    )
]

# Setup configuration
setup(
    name="ImagePawcess",
    version="2.0.2",
    description="Create stamps with any image and save your in-game art! F4 for menu",
    options={"build_exe": build_exe_options},
    executables=executables,
)
