# -*- coding: utf-8 -*-
"""
Windows 音频设备枚举与默认设备切换（纯 ctypes，无第三方依赖）

依赖 COM 接口：
    IMMDeviceEnumerator  枚举播放/录音设备
    IPolicyConfig        切换默认设备（Windows 未公开但稳定可用）
"""
import ctypes
from ctypes import wintypes, byref, POINTER, c_wchar_p, c_wchar

ole32 = ctypes.windll.ole32
oleaut32 = ctypes.windll.oleaut32

CLSCTX_ALL = 23
COINIT_MULTITHREADED = 0x0
COINIT_APARTMENTTHREADED = 0x2

# 数据流方向
RENDER = 0   # 播放（输出）
CAPTURE = 1  # 录音（输入）
ALL = 2

DEVICE_STATE_ACTIVE = 0x00000001

# 角色
ROLE_CONSOLE = 0
ROLE_MULTIMEDIA = 1
ROLE_COMMUNICATIONS = 2
ROLE_ALL = 3   # 用于同时设置全部角色

CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IMMDevice = "{D666063F-1587-4E43-81F1-B948E807363F}"
IID_IMMDeviceCollection = "{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}"
IID_IPropertyStore = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"

# IPolicyConfigVista：设置默认设备
# 注意：Windows 11 24H2/25H2 上旧版 IPolicyConfig({870AF99C..} + {F8679F50..})
# 已不再可用（E_NOINTERFACE），需用下面这对 Vista 版本。
CLSID_PolicyConfigVistaClient = "{294935CE-F637-4E7C-A41B-AB255460B862}"
IID_IPolicyConfigVista = "{568B9108-44BF-40B4-9006-86AFE5B5A620}"

# 兼容旧系统
CLSID_PolicyConfig = "{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}"
IID_IPolicyConfig = "{F8679F50-850A-41CF-9C72-430F290290C7}"

# SetDefaultEndpoint 的 vtable 索引（IUnknown 3 个 + 9 个方法）
SET_DEFAULT_ENDPOINT_INDEX = 12

PKEY_Device_FriendlyName = "{A45C254E-DF1C-4EFD-8020-67D146A850E0},14"
PKEY_Device_DeviceDesc = "{A45C254E-DF1C-4EFD-8020-67D146A850E0},2"
PKEY_Device_InterfaceFriendlyName = "{026E516E-B4FA-414B-9829-487AEF16A8FF},2"


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32),
                ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]

    def __str__(self):
        return "{%08X-%04X-%04X-%s-%s}" % (
            self.Data1, self.Data2, self.Data3,
            "".join("%02X" % b for b in self.Data4[:2]),
            "".join("%02X" % b for b in self.Data4[2:]))


def make_guid(s):
    if isinstance(s, GUID):
        return s
    s = s.strip()
    assert s.startswith("{") and s.endswith("}"), s
    parts = s[1:-1].split("-")
    g = GUID()
    g.Data1 = int(parts[0], 16)
    g.Data2 = int(parts[1], 16)
    g.Data3 = int(parts[2], 16)
    rest = parts[3] + parts[4]
    g.Data4 = (ctypes.c_ubyte * 8)(*[int(rest[i:i + 2], 16) for i in range(0, 16, 2)])
    return g


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


def make_propkey(s):
    guid_s, pid = s.split(",")
    return PROPERTYKEY(make_guid(guid_s), int(pid))


class PROPVARIANT(ctypes.Structure):
    """只用到 VT_LPWSTR"""
    _fields_ = [("vt", ctypes.c_ushort),
                ("wReserved1", ctypes.c_ushort),
                ("wReserved2", ctypes.c_ushort),
                ("wReserved3", ctypes.c_ushort),
                ("pwszVal", ctypes.c_void_p),
                ("unused", ctypes.c_ubyte * 16)]


# ---- COM 基本函数 ----
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CoCreateInstance.argtypes = [POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                   POINTER(GUID), POINTER(ctypes.c_void_p)]
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
# PropVariantClear 在不同系统版本上位于不同 DLL，逐个尝试
_propclear = None
for _dll_name in ("ole32", "propsys", "oleaut32"):
    try:
        _dll = ctypes.windll.LoadLibrary(_dll_name)
        _propclear = getattr(_dll, "PropVariantClear")
        _propclear.argtypes = [POINTER(PROPVARIANT)]
        break
    except (OSError, AttributeError):
        continue

_com_threads = set()


def _ensure_com():
    """COM 必须按线程初始化：后台 QThread 里也要各自 CoInitializeEx"""
    global _com_threads
    import threading
    tid = threading.get_ident()
    if tid in _com_threads:
        return True
    hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    _com_threads.add(tid)
    return True


class COMError(Exception):
    pass


class ComObject:
    """简易 COM 对象包装：按 vtable 索引调用方法"""

    def __init__(self, ptr, cls_name=""):
        self.ptr = ctypes.c_void_p(ptr)
        self.cls_name = cls_name
        self._released = False

    def vtable(self):
        return ctypes.cast(self.ptr, POINTER(POINTER(ctypes.c_void_p)))

    def call(self, index, *args, restype=ctypes.c_long, argtypes=None):
        vt = self.vtable()
        fn = vt[0][index]
        proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes) if argtypes else None
        if proto:
            f = proto(fn)
            return f(self.ptr, *args)
        # 无类型信息：按指针个数推断
        raise ValueError("需要 argtypes")

    def release(self):
        if not self._released and self.ptr:
            try:
                vt = self.vtable()
                release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[0][2])
                release(self.ptr)
            except Exception:
                pass
            self._released = True

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass


# ---- IMMDeviceEnumerator ----
ENUM_VT = {
    "EnumAudioEndpoints": 3,
    "GetDefaultAudioEndpoint": 4,
    "GetDevice": 5,
}


class AudioSession:
    def __init__(self):
        _ensure_com()
        guid_cls = make_guid(CLSID_MMDeviceEnumerator)
        guid_iid = make_guid(IID_IMMDeviceEnumerator)
        ptr = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(byref(guid_cls), None, CLSCTX_ALL,
                                    byref(guid_iid), byref(ptr))
        if hr != 0:
            raise COMError("创建 MMDeviceEnumerator 失败: 0x%08X" % hr)
        self.enum = ComObject(ptr.value, "IMMDeviceEnumerator")

    # ---------- 底层调用 ----------
    def _enum_endpoints(self, data_flow):
        """返回 IMMDeviceCollection"""
        argtypes = [ctypes.c_uint, wintypes.DWORD, POINTER(ctypes.c_void_p)]
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
        vt = self.enum.vtable()
        fn = proto(vt[0][ENUM_VT["EnumAudioEndpoints"]])
        coll = ctypes.c_void_p()
        hr = fn(self.enum.ptr, data_flow, DEVICE_STATE_ACTIVE, byref(coll))
        if hr != 0:
            raise COMError("EnumAudioEndpoints 失败: 0x%08X" % hr)
        return ComObject(coll.value, "IMMDeviceCollection")

    def _collection_count(self, coll):
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, POINTER(wintypes.DWORD))
        vt = coll.vtable()
        fn = proto(vt[0][3])
        n = wintypes.DWORD()
        hr = fn(coll.ptr, byref(n))
        if hr != 0:
            raise COMError("GetCount 失败: 0x%08X" % hr)
        return n.value

    def _collection_item(self, coll, i):
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.DWORD,
                                   POINTER(ctypes.c_void_p))
        vt = coll.vtable()
        fn = proto(vt[0][4])
        dev = ctypes.c_void_p()
        hr = fn(coll.ptr, i, byref(dev))
        if hr != 0:
            raise COMError("Item 失败: 0x%08X" % hr)
        return ComObject(dev.value, "IMMDevice")

    def _device_id(self, dev):
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, POINTER(ctypes.c_void_p))
        vt = dev.vtable()
        fn = proto(vt[0][5])   # GetId
        buf = ctypes.c_void_p()
        hr = fn(dev.ptr, byref(buf))
        if hr != 0:
            raise COMError("GetId 失败: 0x%08X" % hr)
        s = ctypes.wstring_at(buf)
        ole32.CoTaskMemFree(buf)
        return s

    def _open_property_store(self, dev):
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.DWORD,
                                   POINTER(ctypes.c_void_p))
        vt = dev.vtable()
        fn = proto(vt[0][4])   # OpenPropertyStore
        store = ctypes.c_void_p()
        hr = fn(dev.ptr, 0, byref(store))
        if hr != 0:
            raise COMError("OpenPropertyStore 失败: 0x%08X" % hr)
        return ComObject(store.value, "IPropertyStore")

    def _prop_value(self, store, propkey_str):
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, POINTER(PROPERTYKEY),
                                   POINTER(PROPVARIANT))
        vt = store.vtable()
        fn = proto(vt[0][5])   # GetValue
        key = make_propkey(propkey_str)
        pv = PROPVARIANT()
        hr = fn(store.ptr, byref(key), byref(pv))
        if hr != 0 or pv.vt != 31:  # VT_LPWSTR
            return ""
        try:
            return ctypes.wstring_at(pv.pwszVal)
        finally:
            _propclear(byref(pv)) if _propclear else None

    def _default_endpoint(self, data_flow, role=ROLE_MULTIMEDIA):
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_uint, POINTER(ctypes.c_void_p))
        vt = self.enum.vtable()
        fn = proto(vt[0][ENUM_VT["GetDefaultAudioEndpoint"]])
        dev = ctypes.c_void_p()
        hr = fn(self.enum.ptr, data_flow, role, byref(dev))
        if hr != 0:
            return None
        obj = ComObject(dev.value, "IMMDevice")
        try:
            return self._device_id(obj)
        finally:
            obj.release()

    # ---------- 对外接口 ----------
    def list_devices(self, data_flow):
        """枚举设备：返回 [{'id':..., 'name':...}, ...]"""
        coll = self._enum_endpoints(data_flow)
        try:
            n = self._collection_count(coll)
            out = []
            for i in range(n):
                dev = self._collection_item(coll, i)
                try:
                    dev_id = self._device_id(dev)
                    store = self._open_property_store(dev)
                    try:
                        name = (self._prop_value(store, PKEY_Device_InterfaceFriendlyName)
                                or self._prop_value(store, PKEY_Device_FriendlyName)
                                or self._prop_value(store, PKEY_Device_DeviceDesc)
                                or "未知设备")
                    finally:
                        store.release()
                    out.append({"id": dev_id, "name": name})
                finally:
                    dev.release()
            return out
        finally:
            coll.release()

    def get_default(self, data_flow, role=ROLE_MULTIMEDIA):
        return self._default_endpoint(data_flow, role)

    def set_default(self, device_id, role=ROLE_ALL):
        """切换默认设备。role=ROLE_ALL 时同时设置三个角色"""
        # 先试新版 IPolicyConfigVista，再回退旧版
        combos = [(CLSID_PolicyConfigVistaClient, IID_IPolicyConfigVista),
                  (CLSID_PolicyConfig, IID_IPolicyConfig)]
        roles = ([ROLE_CONSOLE, ROLE_MULTIMEDIA, ROLE_COMMUNICATIONS]
                 if role == ROLE_ALL else [role])
        last_err = None
        for clsid, iid in combos:
            guid_cls = make_guid(clsid)
            guid_iid = make_guid(iid)
            ptr = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(byref(guid_cls), None, CLSCTX_ALL,
                                        byref(guid_iid), byref(ptr))
            if hr != 0 or not ptr.value:
                last_err = "创建接口失败 0x%08X" % (hr & 0xFFFFFFFF)
                continue
            cfg = ComObject(ptr.value, "IPolicyConfig")
            try:
                vt = cfg.vtable()
                proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                           c_wchar_p, ctypes.c_uint)
                fn = proto(vt[0][SET_DEFAULT_ENDPOINT_INDEX])
                ok = True
                for r in roles:
                    hr = fn(cfg.ptr, device_id, r)
                    if hr != 0:
                        ok = False
                        last_err = "SetDefaultEndpoint 返回 0x%08X" % (hr & 0xFFFFFFFF)
                        break
                if ok:
                    return True
            finally:
                cfg.release()
        raise COMError(last_err or "切换默认设备失败")


def snapshot():
    """返回当前设备与默认设备状态"""
    sess = AudioSession()
    try:
        return {
            "output": sess.list_devices(RENDER),
            "input": sess.list_devices(CAPTURE),
            "default_output": sess.get_default(RENDER),
            "default_input": sess.get_default(CAPTURE),
        }
    finally:
        sess.enum.release()


if __name__ == "__main__":
    data = snapshot()
    print("=== 输出设备（播放）===")
    for d in data["output"]:
        mark = " *" if d["id"] == data["default_output"] else ""
        print("  %s%s" % (d["name"], mark))
    print("=== 输入设备（录音）===")
    for d in data["input"]:
        mark = " *" if d["id"] == data["default_input"] else ""
        print("  %s%s" % (d["name"], mark))
