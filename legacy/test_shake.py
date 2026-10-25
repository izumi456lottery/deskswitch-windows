# -*- coding: utf-8 -*-
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

"""VIA/私有协议握手验证"""
import hid_core as H

VID, PID = 0x373B, 0x1267
VIA_USAGE_PAGE = 0xFF60

def find_keyboard():
    devs = H.list_hid_devices(vid=VID, pid=PID, usage_page=VIA_USAGE_PAGE)
    return devs[0] if devs else None

def transfer(dev, cmd, timeout_ms=800):
    dev.write_report(cmd)
    return dev.read_report(timeout_ms)

if __name__ == "__main__":
    info = find_keyboard()
    if not info:
        print("未找到键盘 VIA 接口")
        raise SystemExit(1)
    print("使用接口:", info["path"])
    print("in/out 长度:", info["input_len"], info["output_len"])

    with H.HidDevice(info["path"], output_len=info["output_len"], input_len=info["input_len"]) as dev:
        for label, cmd in [
            ("VIA get_protocol_version [0x01]", [0x01]),
            ("VIA get_keyboard_value [0x02 id=1]", [0x02, 0x01]),
            ("priv customId(150) feature get", [150, 17, 0]),
        ]:
            payload = list(cmd) + [0] * (384 - len(cmd))
            res = transfer(dev, payload)
            if res is None:
                print("%-40s -> 无响应(超时)" % label)
            else:
                data = bytes(res)
                nonzero = max([i + 1 for i, b in enumerate(data[:48]) if b] or [0])
                print("%-40s -> %d 字节, 前%d字节: %s" % (label, len(data), nonzero, data[:nonzero].hex()))
