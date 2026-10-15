"""FaceFrame.exe - tiny launcher built with PyInstaller (no third-party imports).

Starts the installed app with the private Python environment next to it. If the
environment is missing or broken it offers to run the repair (bootstrap.ps1)."""

import ctypes
import os
import subprocess
import sys

MB_YESNO, MB_ICONWARNING, IDYES = 0x04, 0x30, 6


def main() -> int:
    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    pyw = os.path.join(here, ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(pyw) or not os.path.exists(os.path.join(here, "installed.flag")):
        r = ctypes.windll.user32.MessageBoxW(
            None, "FaceFrame's components are not installed or were removed.\n\nRepair now? "
                  "(needs an internet connection, takes a few minutes)", "FaceFrame", MB_YESNO | MB_ICONWARNING)
        if r == IDYES:
            subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                              os.path.join(here, "bootstrap.ps1")], cwd=here)
        return 1
    flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    # PyInstaller's bootloader variables must not leak into the app: anything the app starts later (the
    # updater -> installer -> FaceFrame.exe) would otherwise look for this launcher's deleted temp folder
    # ("Failed to load Python DLL ... _MEIxxxx").
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("_MEI", "_PYI"))}
    subprocess.Popen([pyw, "-m", "faceframe", *sys.argv[1:]], cwd=here, creationflags=flags, close_fds=True, env=env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
