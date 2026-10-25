# -*- coding: utf-8 -*-
"""纯 ctypes 创建 .lnk 快捷方式（不依赖 PowerShell / pywin32）"""
import ctypes
import os
from ctypes import wintypes

ole32 = ctypes.windll.ole32
shell32 = ctypes.windll.shell32

CLSID_ShellLink = "{00021401-0000-0000-C000-000000000046}"
IID_IShellLinkW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPersistFile = "{0000010B-0000-0000-C000-000000000046}"
CLSCTX_INPROC_SERVER = 1


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_str(cls, s):
        s = s.strip("{}")
        a, b, c, d, e = s.split("-")
        data4 = [int(d[0:2], 16), int(d[2:4], 16)]
        data4 += [int(e[i:i + 2], 16) for i in range(0, 12, 2)]
        return cls(int(a, 16), int(b, 16), int(c, 16),
                   (ctypes.c_ubyte * 8)(*data4))


def _coinit():
    ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED


# 独立函数原型：避免与其他模块给 ole32.CoCreateInstance 设的 argtypes 冲突
_CoCreateInstance = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(("CoCreateInstance", ole32))


def create_shortcut(lnk_path, target, workdir="", description="", arguments="",
                    icon=""):
    """创建快捷方式，成功返回 True"""
    _coinit()
    cls = GUID.from_str(CLSID_ShellLink)
    iid = GUID.from_str(IID_IShellLinkW)
    pobj = ctypes.c_void_p()
    hr = _CoCreateInstance(ctypes.byref(cls), None, CLSCTX_INPROC_SERVER,
                           ctypes.byref(iid), ctypes.byref(pobj))
    if hr != 0 or not pobj.value:
        raise OSError("CoCreateInstance ShellLink 失败 0x%08X" % (hr & 0xFFFFFFFF))

    vt = ctypes.cast(pobj.value, ctypes.POINTER(ctypes.c_void_p))[0]
    vt = ctypes.cast(vt, ctypes.POINTER(ctypes.c_void_p))

    def call(idx, *types):
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)
        return proto(vt[idx])

    try:
        # IShellLinkW::SetPath (20) / SetWorkingDirectory (9) / SetDescription (7) / SetArguments (11)
        hr = call(20, wintypes.LPCWSTR)(pobj, os.path.abspath(target))
        if hr != 0:
            raise OSError("SetPath 失败 0x%08X" % (hr & 0xFFFFFFFF))
        if workdir:
            call(9, wintypes.LPCWSTR)(pobj, os.path.abspath(workdir))
        if description:
            call(7, wintypes.LPCWSTR)(pobj, description)
        if arguments:
            call(11, wintypes.LPCWSTR)(pobj, arguments)
        if icon:
            call(17, wintypes.LPCWSTR, ctypes.c_int)(pobj, icon, 0)

        # QueryInterface -> IPersistFile
        piid = GUID.from_str(IID_IPersistFile)
        ppf = ctypes.c_void_p()
        proto_qi = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                      ctypes.POINTER(GUID),
                                      ctypes.POINTER(ctypes.c_void_p))
        hr = proto_qi(vt[0])(pobj, ctypes.byref(piid), ctypes.byref(ppf))
        if hr != 0 or not ppf.value:
            raise OSError("QueryInterface IPersistFile 失败 0x%08X" % (hr & 0xFFFFFFFF))
        pvt = ctypes.cast(ctypes.cast(ppf.value, ctypes.POINTER(ctypes.c_void_p))[0],
                          ctypes.POINTER(ctypes.c_void_p))
        proto_save = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                        wintypes.LPCWSTR, wintypes.BOOL)
        hr = proto_save(pvt[6])(ppf, os.path.abspath(lnk_path), True)
        if hr != 0:
            raise OSError("IPersistFile::Save 失败 0x%08X" % (hr & 0xFFFFFFFF))
        return True
    finally:
        proto_rel = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
        proto_rel(vt[2])(pobj)


def startup_dir():
    return os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "Microsoft",
                        "Windows", "Start Menu", "Programs", "Startup")


if __name__ == "__main__":
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    ok = create_shortcut(os.path.join(startup_dir(), "DeskSwitch.lnk"),
                         os.path.join(here, "启动 DeskSwitch.vbs"),
                         here, "DeskSwitch 桌面切换挂件")
    print("shortcut:", ok, os.path.join(startup_dir(), "DeskSwitch.lnk"))
