"""本地启动脚本，修复 Windows 中文系统下 psycopg2 的编码问题。"""
import ctypes
ctypes.windll.kernel32.SetConsoleCP(65001)
ctypes.windll.kernel32.SetConsoleOutputCP(65001)

import sys
import os

# 强制系统编码为 UTF-8
if sys.platform == "win32":
    import _locale
    _locale._getdefaultlocale = lambda *args: ('en_US', 'utf-8')

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

# 启动主程序
import start
start.main()
