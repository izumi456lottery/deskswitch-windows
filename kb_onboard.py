# -*- coding: utf-8 -*-
"""键盘板载配置切换（原生 HID，一条命令搞定）

通过 usagePage 0xFF60 的 RawHID 接口收发 32 字节命令帧：
    读当前板载配置：  帧 [2, 150, 35]          响应 res[4] = 当前索引
    切换板载配置：    帧 [3, 150, 35, index]   键盘立即重置并重新枚举（约 1 秒）

索引是 **0-based**：0 = 板载配置1，3 = 板载配置4。
切换后键盘的所有设置（灯效 / RT / 行程 / DKS / 键位）整体变成那套板载配置，
不需要逐项下发，也不会写坏任何数据。
"""
import time

import hid_core as H

VID = 0x373B
PID = 0x1267
USAGE_PAGE = 0xFF60

CMD_GET_KEYBOARD_VALUE = 2
CMD_SET_KEYBOARD_VALUE = 3
SUB_CUSTOM_ID = 150
SUB_PROFILE = 35

FRAME_SIZE = 32


class KeyboardNotFound(Exception):
    pass


def find_device():
    infos = H.list_hid_devices(vid=VID, pid=PID, usage_page=USAGE_PAGE)
    if not infos:
        return None
    return infos[0]


def _open():
    info = find_device()
    if not info:
        raise KeyboardNotFound("未找到键盘（VID=%04X PID=%04X）" % (VID, PID))
    dev = H.HidDevice(info["path"], output_len=info["output_len"],
                      input_len=info["input_len"])
    return dev, info


def _frame(*parts):
    f = list(parts)[:FRAME_SIZE]
    return f + [0] * (FRAME_SIZE - len(f))


def _transfer(dev, frame, out_len, timeout_ms=800):
    payload = list(frame) + [0] * (FRAME_SIZE - len(frame))
    data = payload + [0] * max(0, out_len - len(payload))
    dev.write_report(data)
    res = dev.read_report(timeout_ms)
    return bytes(res) if res else None


def get_profile(timeout_ms=800):
    """读取当前板载配置索引（0-based）。失败抛异常。"""
    dev, info = _open()
    try:
        res = _transfer(dev, _frame(CMD_GET_KEYBOARD_VALUE, SUB_CUSTOM_ID,
                                    SUB_PROFILE), info["output_len"], timeout_ms)
        if res is None or len(res) < 5:
            raise KeyboardNotFound("读取板载配置无响应")
        return res[4]
    finally:
        try:
            dev.close()
        except Exception:
            pass


def set_profile(index, wait_ready=True, timeout=8.0):
    """切换到指定板载配置（0-based）。

    返回 (ok, 说明)。切换会让键盘重置并重新枚举，函数会等它回来并读回校验。
    """
    if not 0 <= index <= 255:
        return False, "索引超出范围：%s" % index

    dev, info = _open()
    try:
        _transfer(dev, _frame(CMD_SET_KEYBOARD_VALUE, SUB_CUSTOM_ID, SUB_PROFILE,
                              index), info["output_len"], timeout_ms=600)
    except Exception:
        # 键盘收到切换命令会立刻断开，写入报错属正常
        pass
    finally:
        try:
            dev.close()
        except Exception:
            pass

    if not wait_ready:
        return True, "已发送切换指令"

    # 等键盘重新枚举
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.25)
        if find_device():
            break
    else:
        return False, "切换后键盘未重新连接"

    time.sleep(0.4)
    try:
        cur = get_profile()
    except Exception as e:
        return False, "切换后读取失败：%s" % e
    if cur == index:
        return True, "已切换到板载配置 %d" % (index + 1)
    return False, "切换未生效（当前仍是板载配置 %s）" % (
        (cur + 1) if cur is not None else "?")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Fuzzy 60 V2 板载配置切换")
    ap.add_argument("--get", action="store_true", help="读取当前板载配置")
    ap.add_argument("--set", type=int, metavar="N", help="切换到板载配置 N（1 起算）")
    a = ap.parse_args()
    if a.set is not None:
        ok, msg = set_profile(a.set - 1)
        print(("OK  " if ok else "FAIL"), msg)
    else:
        cur = get_profile()
        print("当前板载配置：%d（索引 %d）" % (cur + 1, cur))
