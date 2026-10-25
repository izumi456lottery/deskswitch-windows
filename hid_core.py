# -*- coding: utf-8 -*-
"""
Windows HID 原生访问层（纯 ctypes，不依赖任何第三方库 / node）

提供：
    list_hid_devices()  -> 枚举系统所有 HID 接口（含 VID/PID/usagePage/usage/path）
    open_device(path)   -> 打开设备句柄
    write_report()      -> 发送 output report
    read_report(timeout)-> 读取 input report（带超时）
"""
import ctypes
from ctypes import wintypes, byref, sizeof, POINTER, create_string_buffer

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
WAIT_OBJECT_0 = 0x0
WAIT_TIMEOUT = 0x102
ERROR_IO_PENDING = 997

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

MAX_REPORT_BYTES = 512


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


HID_CLASS_GUID = GUID(0x4D1E55B2, 0xF16F, 0x11CF,
                      (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30))


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    ]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", ctypes.c_ulong),
        ("VendorID", ctypes.c_ushort),
        ("ProductID", ctypes.c_ushort),
        ("VersionNumber", ctypes.c_ushort),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", ctypes.c_ushort),
        ("UsagePage", ctypes.c_ushort),
        ("InputReportByteLength", ctypes.c_ushort),
        ("OutputReportByteLength", ctypes.c_ushort),
        ("FeatureReportByteLength", ctypes.c_ushort),
        ("Reserved", ctypes.c_ushort * 17),
        ("NumberLinkCollectionNodes", ctypes.c_ushort),
        ("NumberInputButtonCaps", ctypes.c_ushort),
        ("NumberInputValueCaps", ctypes.c_ushort),
        ("NumberInputDataIndices", ctypes.c_ushort),
        ("NumberOutputButtonCaps", ctypes.c_ushort),
        ("NumberOutputValueCaps", ctypes.c_ushort),
        ("NumberOutputDataIndices", ctypes.c_ushort),
        ("NumberFeatureButtonCaps", ctypes.c_ushort),
        ("NumberFeatureValueCaps", ctypes.c_ushort),
        ("NumberFeatureDataIndices", ctypes.c_ushort),
    ]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.POINTER(ctypes.c_ulong)),
        ("InternalHigh", ctypes.POINTER(ctypes.c_ulong)),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]

setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE, POINTER(SP_DEVINFO_DATA), POINTER(GUID), wintypes.DWORD, POINTER(SP_DEVICE_INTERFACE_DATA)]

setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE, POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p, wintypes.DWORD,
    POINTER(wintypes.DWORD), POINTER(SP_DEVINFO_DATA)]

setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]

kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel32.WriteFile.restype = wintypes.BOOL
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.restype = wintypes.DWORD

hid.HidD_GetAttributes.restype = wintypes.BOOL
hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.restype = wintypes.BOOL
hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.restype = wintypes.BOOL
hid.HidP_GetCaps.restype = ctypes.c_ulong
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, POINTER(HIDP_CAPS)]
hid.HidD_GetProductString = getattr(hid, "HidD_GetProductString", None)


def _enum_device_paths():
    """枚举所有 HID 接口的设备路径"""
    paths = []
    devs = setupapi.SetupDiGetClassDevsW(byref(HID_CLASS_GUID), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if devs == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        index = 0
        while True:
            ifdata = SP_DEVICE_INTERFACE_DATA()
            ifdata.cbSize = sizeof(SP_DEVICE_INTERFACE_DATA)
            ok = setupapi.SetupDiEnumDeviceInterfaces(devs, None, byref(HID_CLASS_GUID), index, byref(ifdata))
            if not ok:
                if ctypes.get_last_error() == 259:  # ERROR_NO_MORE_ITEMS
                    break
                raise ctypes.WinError(ctypes.get_last_error())

            required = wintypes.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(devs, byref(ifdata), None, 0, byref(required), None)
            buf = create_string_buffer(required.value)
            detail_ptr = ctypes.cast(buf, ctypes.c_void_p)
            # DevicePath 的实际偏移在 32/64 位下不同，逐个尝试取合法路径
            cb_size = 8 if sizeof(ctypes.c_void_p) == 8 else 6
            ctypes.memmove(buf, ctypes.byref(wintypes.DWORD(cb_size)), ctypes.sizeof(wintypes.DWORD))
            ok = setupapi.SetupDiGetDeviceInterfaceDetailW(
                devs, byref(ifdata), detail_ptr, required, byref(required), None)
            if ok:
                found = None
                for off in (4, 8, 6):
                    cand = ctypes.cast(ctypes.addressof(buf) + off, ctypes.c_wchar_p).value
                    if cand and cand.startswith("\\\\"):
                        found = cand
                        break
                if found:
                    paths.append(found)
            index += 1
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(devs)
    return paths


def _get_device_info(path):
    """打开设备并读取 VID/PID/usagePage/usage 等信息；返回 dict 或 None"""
    handle = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                                  FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                  OPEN_EXISTING, 0, None)
    if handle == INVALID_HANDLE_VALUE:
        return None
    try:
        attrs = HIDD_ATTRIBUTES()
        attrs.Size = sizeof(HIDD_ATTRIBUTES)
        if not hid.HidD_GetAttributes(handle, byref(attrs)):
            return None
        preparsed = ctypes.c_void_p()
        caps = HIDP_CAPS()
        if hid.HidD_GetPreparsedData(handle, byref(preparsed)) and preparsed:
            try:
                hid.HidP_GetCaps(preparsed, byref(caps))
                usage_page, usage = caps.UsagePage, caps.Usage
                in_len, out_len = caps.InputReportByteLength, caps.OutputReportByteLength
            finally:
                hid.HidD_FreePreparsedData(preparsed)
        else:
            usage_page, usage, in_len, out_len = 0, 0, 0, 0
        product = ""
        if hid.HidD_GetProductString:
            hid.HidD_GetProductString.restype = wintypes.BOOL
            hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_ulong]
            pbuf = create_string_buffer(256)
            if hid.HidD_GetProductString(handle, pbuf, 256):
                product = pbuf.raw.decode("utf-16-le", "ignore").split("\x00")[0]
        return dict(path=path, vendor_id=attrs.VendorID, product_id=attrs.ProductID,
                    version=attrs.VersionNumber, usage_page=usage_page, usage=usage,
                    input_len=in_len, output_len=out_len, product=product)
    finally:
        kernel32.CloseHandle(handle)


def list_hid_devices(vid=None, pid=None, usage_page=None):
    """枚举 HID 设备，可按 VID/PID/usagePage 过滤"""
    result = []
    for path in _enum_device_paths():
        info = _get_device_info(path)
        if not info:
            continue
        if vid is not None and info["vendor_id"] != vid:
            continue
        if pid is not None and info["product_id"] != pid:
            continue
        if usage_page is not None and info["usage_page"] != usage_page:
            continue
        result.append(info)
    return result


class HidDevice:
    """同步读写的 HID 设备封装"""

    def __init__(self, path, output_len=MAX_REPORT_BYTES, input_len=MAX_REPORT_BYTES):
        self.handle = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                                           FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                           OPEN_EXISTING, 0, None)
        if self.handle == INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())
        self.path = path
        self.output_len = output_len
        self.input_len = input_len
        self._closed = False

    def close(self):
        if not self._closed and self.handle != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(self.handle)
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def write_report(self, data, report_id=0):
        """data 为不含 report id 的命令字节；实际发送时前置 report id"""
        payload = bytes([report_id]) + bytes(data)
        buf = create_string_buffer(payload, max(self.output_len, len(payload)))
        written = wintypes.DWORD()
        ok = kernel32.WriteFile(self.handle, buf, max(self.output_len, len(buf)), byref(written), None)
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return written.value

    def read_report(self, timeout_ms=600):
        """读取 input report，返回 bytes（去掉首字节 report id），超时返回 None"""
        event = kernel32.CreateEventW(None, True, False, None)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
        ov = OVERLAPPED()
        ov.hEvent = event
        try:
            buf = create_string_buffer(max(self.input_len, MAX_REPORT_BYTES))
            read = wintypes.DWORD()
            ok = kernel32.ReadFile(self.handle, buf, len(buf), byref(read), byref(ov))
            if not ok:
                err = ctypes.get_last_error()
                if err != ERROR_IO_PENDING:
                    return None
                wait = kernel32.WaitForSingleObject(event, timeout_ms)
                if wait == WAIT_TIMEOUT:
                    kernel32.CancelIo(self.handle)
                    return None
                if wait != WAIT_OBJECT_0:
                    return None
                kernel32.GetOverlappedResult(self.handle, byref(ov), byref(read), False)
            return buf.raw[:read.value]
        finally:
            kernel32.CloseHandle(event)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="HID 设备探测")
    ap.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    ap.add_argument("--usage-page", type=lambda x: int(x, 16), default=None)
    args = ap.parse_args()
    for d in list_hid_devices(vid=args.vid, usage_page=args.usage_page):
        print("VID=%04X PID=%04X usagePage=%04X usage=%d in=%d out=%d  %s"
              % (d["vendor_id"], d["product_id"], d["usage_page"], d["usage"],
                 d["input_len"], d["output_len"], d["product"]))
        print("    ", d["path"])
