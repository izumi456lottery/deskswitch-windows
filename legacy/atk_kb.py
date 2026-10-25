# -*- coding: utf-8 -*-
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

"""
ATK / Velota Fuzzy 60 V2 键盘 HID 协议实现

报文格式（VIA 兼容，32 字节）:
    [0]     VIA 命令 ID
    [1]     子命令 / 功能段 ID
    [2..]   参数

已知命令:
    id_get_protocol_version = 1
    id_get_keyboard_value   = 2
    id_set_keyboard_value   = 3
    id_custom_set_value     = 7
    id_custom_get_value     = 8

    customId            = 150
    setCustomLamplight  = 66
    getCustomLamplight  = 69
    saveLamplight       = 18
"""
import time
import math

import hid_core as H

VID, PID = 0x373B, 0x1267
VIA_USAGE_PAGE = 0xFF60

FRAME_SIZE = 32
REPORT_SIZE = 384   # 帧数据部分（不含 report id）

# VIA 命令
CMD_GET_PROTOCOL_VERSION = 0x01
CMD_GET_KEYBOARD_VALUE = 0x02
CMD_SET_KEYBOARD_VALUE = 0x03
CMD_CUSTOM_SET_VALUE = 0x07
CMD_CUSTOM_GET_VALUE = 0x08
CMD_LIGHTING_SET_VALUE = 0x07
CMD_LIGHTING_GET_VALUE = 0x08

# 私有子命令
SUB_CUSTOM_ID = 150
SUB_SAVE_LAMPLIGHT = 18
SUB_SET_CUSTOM_LAMPLIGHT = 66
SUB_GET_CUSTOM_LAMPLIGHT = 69
SUB_LIGHT_INFO = 65          # 背光模式/速度/亮度
SUB_DEAD_BAND = 11           # 上下死区
SUB_BUFFER_RT = 14           # RT 批量写入
SUB_BUFFER_TOG_TH = 13       # 行程批量写入
CMD_SET_KEYMAP = 19
CMD_GET_KEYMAP = 18

# DKS
SUB_DKS = 14
SUB_FIELD_GET = 1
SUB_FIELD_SET = 3

# flash 操作
SUB_OP_NORMAL = 0
SUB_OP_ERASE = 1
SUB_OP_WRITE = 2


class KeyboardNotFound(Exception):
    pass


class Fuzzy60:
    """Velota / ATK Fuzzy 60 V2 键盘控制"""

    def __init__(self, rows=5, cols=14):
        infos = H.list_hid_devices(vid=VID, pid=PID, usage_page=VIA_USAGE_PAGE)
        if not infos:
            raise KeyboardNotFound("未找到键盘的 VIA 接口（VID=%04X PID=%04X）" % (VID, PID))
        info = infos[0]
        self.path = info["path"]
        self.product = info["product"]
        self.dev = H.HidDevice(info["path"], output_len=info["output_len"], input_len=info["input_len"])
        self.rows = rows
        self.cols = cols

    def close(self):
        self.dev.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ---------- 基础传输 ----------
    def transfer(self, frame, timeout_ms=700):
        """发送 32 字节命令帧，返回响应字节（含 report id）"""
        payload = list(frame) + [0] * (FRAME_SIZE - len(frame))
        data = payload + [0] * (REPORT_SIZE - len(payload))
        self.dev.write_report(data)
        res = self.dev.read_report(timeout_ms)
        return bytes(res) if res else None

    def frame(self, *parts):
        f = list(parts)[:FRAME_SIZE]
        return f + [0] * (FRAME_SIZE - len(f))

    # ---------- 查询 ----------
    def get_protocol_version(self):
        res = self.transfer(self.frame(CMD_GET_PROTOCOL_VERSION))
        if res is None:
            return None
        return (res[2] << 8) | res[3]

    def get_custom_led(self, row, col, num):
        """读取一行中的若干灯色，返回 [(r,g,b), ...]"""
        pkt = self.frame(CMD_CUSTOM_GET_VALUE, SUB_GET_CUSTOM_LAMPLIGHT, row, col, num)
        res = self.transfer(pkt)
        if res is None:
            return None
        # res: [reportId, cmd, sub, row, col, num, r,g,b...]
        got_num = res[5] if len(res) > 5 else 0
        out = []
        for i in range(got_num):
            base = 6 + i * 3
            out.append(tuple(res[base:base + 3]))
        return out

    def get_custom_led_matrix(self, rows=None, cols=None):
        """读取整片自定义灯色矩阵"""
        rows = rows or self.rows
        cols = cols or self.cols
        matrix = []
        for r in range(rows):
            line = []
            start = 0
            while start < cols:
                num = min(8, cols - start)
                got = self.get_custom_led(r, start, num)
                if got is None:
                    raise KeyboardNotFound("读取第 %d 行灯效无响应" % r)
                line.extend(got)
                start += num
            matrix.append(line)
        return matrix

    # ---------- 下发 ----------
    def set_custom_led(self, row, col_start, colors):
        """写入一行中的若干灯色（最多 8 个）"""
        pkt = self.frame(CMD_CUSTOM_SET_VALUE, SUB_SET_CUSTOM_LAMPLIGHT,
                         row, col_start, len(colors))
        for i, (rr, gg, bb) in enumerate(colors):
            base = 5 + i * 3
            pkt[base], pkt[base + 1], pkt[base + 2] = rr, gg, bb
        return self.transfer(pkt)

    def save_lamplight(self):
        """保存灯效到键盘"""
        pkt = self.frame(CMD_SET_KEYBOARD_VALUE, SUB_CUSTOM_ID, SUB_SAVE_LAMPLIGHT)
        return self.transfer(pkt)

    def _write_matrix_frames(self, matrix, frame_delay=0.035):
        """把矩阵按每帧 8 色写入设备（不保存）"""
        for r in range(len(matrix)):
            line = matrix[r]
            cols = len(line)
            start = 0
            while start < cols:
                num = min(8, cols - start)
                self.set_custom_led(r, start, line[start:start + num])
                start += num
                if start < cols:
                    time.sleep(frame_delay)
            if r < len(matrix) - 1:
                time.sleep(frame_delay)

    def apply_custom_led_matrix(self, matrix, verify=True, max_retry=4, tolerance=12):
        """下发整片灯效矩阵并保存

        设备偶尔会丢帧（右下角部分按键不生效），所以写入后逐段校验，
        不一致就补写，最多重试 max_retry 轮。
        """
        self._write_matrix_frames(matrix)
        time.sleep(0.25)
        self.save_lamplight()
        time.sleep(0.25)

        mismatches = []
        if verify:
            for attempt in range(max_retry):
                mismatches = []
                try:
                    current = self.get_custom_led_matrix(rows=len(matrix),
                                                         cols=len(matrix[0]) if matrix else 0)
                except Exception:
                    break
                for r in range(len(matrix)):
                    for c in range(len(matrix[r])):
                        target = matrix[r][c]
                        got = current[r][c] if r < len(current) and c < len(current[r]) else None
                        if got is None or any(abs(a - b) > tolerance for a, b in zip(target, got)):
                            mismatches.append((r, c))
                if not mismatches:
                    return True, []
                # 按所属帧分组补写
                fixes = {}
                for r, c in mismatches:
                    seg = (r, c // 8)
                    fixes.setdefault(seg, []).append(c)
                for (r, seg), cols_in in fixes.items():
                    start = seg * 8
                    line = matrix[r]
                    self.set_custom_led(r, start, line[start:start + 8])
                    time.sleep(0.04)
                time.sleep(0.2)
                self.save_lamplight()
                time.sleep(0.25)
        return len(mismatches) == 0, mismatches


    # ---------- RT（Rapid Trigger）/ 行程 ----------
    def _read_buffer(self, cmd, sub, offset, size, width, timeout_ms=700):
        """通用：读取 KeyBuffer（RT 5 字节/键，行程 2 字节/键）"""
        pkt = self.frame(CMD_GET_KEYBOARD_VALUE, SUB_CUSTOM_ID, sub, 0, 0,
                         (offset >> 8) & 0xFF, offset & 0xFF, size)
        res = self.transfer(pkt, timeout_ms)
        if res is None:
            return None
        # 响应：reportId, cmd, 150, sub, ?, ?, offset_hi, offset_lo, size, data...
        size_got = res[8] if len(res) > 8 else size
        start = 9
        return list(res[start:start + width * size_got])

    def get_all_rt(self, rows=6, cols=14):
        """读取全部按键的 RT 参数，返回 [[{releaseRt,pressRt,on}, ...], ...]"""
        total = rows * cols
        raw = []
        for i in range(0, math.ceil(total / 4)):
            chunk = self._read_buffer(CMD_GET_KEYBOARD_VALUE, SUB_BUFFER_RT, 4 * i, 4, 5)
            if chunk is None:
                raise KeyboardNotFound("读取 RT 第 %d 组无响应" % i)
            raw.extend(chunk)
        out = []
        idx = 0
        for r in range(rows):
            line = []
            for c in range(cols):
                base = idx * 5
                if base + 4 >= len(raw):
                    line.append({"releaseRt": 0, "pressRt": 0, "on": 0})
                else:
                    on = raw[base]
                    rel = (raw[base + 1] << 8) | raw[base + 2]
                    pre = (raw[base + 3] << 8) | raw[base + 4]
                    line.append({"releaseRt": rel / 1000.0, "pressRt": pre / 1000.0, "on": on})
                idx += 1
            out.append(line)
        return out

    def set_all_rt(self, values, frame_delay=0.2):
        """下发全部 RT 参数

        values: [[{releaseRt, pressRt, on}, ...], ...] 行优先
        """
        flat = []
        for line in values:
            for cell in line:
                rel = int(round(float(cell.get("releaseRt", 0)) * 1000)) & 0xFFFF
                pre = int(round(float(cell.get("pressRt", 0)) * 1000)) & 0xFFFF
                flat.extend([int(cell.get("on", 0)) & 0xFF,
                             (rel >> 8) & 0xFF, rel & 0xFF,
                             (pre >> 8) & 0xFF, pre & 0xFF])
        chunks = [flat[i:i + 20] for i in range(0, len(flat), 20)]
        for idx, chunk in enumerate(chunks):
            op = SUB_OP_ERASE if idx == 0 else (SUB_OP_WRITE if idx == len(chunks) - 1 else SUB_OP_NORMAL)
            pkt = self.frame(CMD_SET_KEYBOARD_VALUE, SUB_CUSTOM_ID, SUB_BUFFER_RT, 0, 0,
                             ((4 * idx) >> 8) & 0xFF, (4 * idx) & 0xFF, 4, op)
            for k, byte in enumerate(chunk):
                pkt[9 + k] = byte
            self.transfer(pkt)
            if idx == 0 or idx == len(chunks) - 1:
                time.sleep(frame_delay)
        return True

    def get_all_travel(self, rows=6, cols=14):
        """读取全部按键行程点"""
        total = rows * cols
        raw = []
        for i in range(0, math.ceil(total / 11)):
            chunk = self._read_buffer(CMD_GET_KEYBOARD_VALUE, SUB_BUFFER_TOG_TH, 11 * i, 11, 2)
            if chunk is None:
                raise KeyboardNotFound("读取行程第 %d 组无响应" % i)
            raw.extend(chunk)
        out = []
        idx = 0
        for r in range(rows):
            line = []
            for c in range(cols):
                base = idx * 2
                val = ((raw[base] << 8) | raw[base + 1]) / 1000.0 if base + 1 < len(raw) else 0
                line.append({"point": val})
                idx += 1
            out.append(line)
        return out

    def set_all_travel(self, values, frame_delay=0.2):
        """下发全部按键行程点：[[{point}, ...], ...]"""
        flat = []
        for line in values:
            for cell in line:
                v = int(round(float(cell.get("point", 0)) * 1000)) & 0xFFFF
                flat.extend([(v >> 8) & 0xFF, v & 0xFF])
        chunks = [flat[i:i + 22] for i in range(0, len(flat), 22)]
        for idx, chunk in enumerate(chunks):
            op = SUB_OP_ERASE if idx == 0 else (SUB_OP_WRITE if idx == len(chunks) - 1 else SUB_OP_NORMAL)
            pkt = self.frame(CMD_SET_KEYBOARD_VALUE, SUB_CUSTOM_ID, SUB_BUFFER_TOG_TH, 0, 0,
                             ((11 * idx) >> 8) & 0xFF, (11 * idx) & 0xFF, 11, op)
            for k, byte in enumerate(chunk):
                pkt[9 + k] = byte
            self.transfer(pkt)
            if idx == 0 or idx == len(chunks) - 1:
                time.sleep(frame_delay)
        return True

    # ---------- 键位映射（VIA 标准 keymap） ----------
    def get_keymap(self, layers=4, rows=5, cols=14):
        """读取全部键位，返回 [layer][row][col] = (keyClass, keyId)"""
        per_row = cols
        out = []
        total = layers * rows * cols
        pos = 0
        flat = []
        while pos < total:
            n = min(14, total - pos)
            pkt = self.frame(CMD_GET_KEYMAP)
            off = 2 * pos
            pkt[1], pkt[2], pkt[3] = (off >> 8) & 0xFF, off & 0xFF, 2 * n
            res = self.transfer(pkt)
            if res is None:
                raise KeyboardNotFound("读取键位无响应 @%d" % pos)
            for i in range(n):
                flat.append((res[5 + 2 * i], res[6 + 2 * i]))
            pos += n
        idx = 0
        for L in range(layers):
            layer = []
            for r in range(rows):
                row = [flat[idx + c] for c in range(cols)]
                idx += cols
                layer.append(row)
            out.append(layer)
        return out

    def build_keymap(self, key_actions, layers=4, rows=5, cols=14):
        """由配置的 keyActions（含 position）构造设备所需的满矩阵

        配置里只列出存在物理按键的位置，空缺槽位补 (0, 0)。
        """
        mat = [[[(0, 0)] * cols for _ in range(rows)] for _ in range(layers)]
        for L, layer in enumerate(key_actions):
            if L >= layers:
                break
            for row in layer:
                for key in row:
                    pos = key.get("position") or key.get("actualPosition")
                    if not pos:
                        continue
                    r, c = int(pos[0]), int(pos[1])
                    if 0 <= r < rows and 0 <= c < cols:
                        ck = key.get("changeKey") or [0, 0]
                        mat[L][r][c] = (int(ck[0]) & 0xFF, int(ck[1]) & 0xFF)
        return mat

    def set_keymap(self, mat, verify=True):
        """写入全部键位，按每帧 14 键"""
        flat = [pair for layer in mat for row in layer for pair in row]
        for i in range(0, len(flat), 14):
            chunk = flat[i:i + 14]
            off = 2 * i
            pkt = self.frame(CMD_SET_KEYMAP, (off >> 8) & 0xFF, off & 0xFF, 2 * len(chunk))
            for k, (kc, ki) in enumerate(chunk):
                pkt[4 + 2 * k], pkt[5 + 2 * k] = kc & 0xFF, ki & 0xFF
            self.transfer(pkt)
            time.sleep(0.03)
        if verify:
            time.sleep(0.15)
            cur = self.get_keymap(layers=len(mat), rows=len(mat[0]), cols=len(mat[0][0]))
            bad = [(i, j) for i in range(len(mat)) for j, row in enumerate(mat[i])
                   for k in range(len(row)) for _ in [0]
                   if False]
            mism = []
            for L in range(len(mat)):
                for r in range(len(mat[L])):
                    for c in range(len(mat[L][r])):
                        if cur[L][r][c] != tuple(mat[L][r][c]):
                            mism.append((L, r, c, cur[L][r][c], mat[L][r][c]))
            return len(mism) == 0, mism
        return True, []

    # ---------- DKS（动态键程） ----------
    def get_dks(self, index):
        """读取某个高级按键动作的 4 段动作"""
        pkt = self.frame(CMD_GET_KEYBOARD_VALUE, SUB_CUSTOM_ID, SUB_DKS,
                         0, 0, SUB_FIELD_GET, index)
        res = self.transfer(pkt)
        if res is None:
            return None
        # 响应比请求多 reportId 一字节；索引回应在 res[7]，动作数据从 res[8] 起
        actions = []
        for i in range(4):
            base = 8 + 4 * i
            status = (res[base] << 8) | res[base + 1]
            actions.append({"bits": [(status >> b) & 1 for b in range(10)],
                            "keyCode": (res[base + 2], res[base + 3])})
        return actions

    def set_dks(self, index, actions):
        """写入某个高级按键动作；actions 为 4 段 {status:[10], keyCode:[cls,id]}"""
        pkt = self.frame(CMD_SET_KEYBOARD_VALUE, SUB_CUSTOM_ID, SUB_DKS,
                         0, 0, SUB_FIELD_SET, index)
        for i in range(4):
            act = actions[i] if i < len(actions) else None
            status = 0
            kc, ki = 0, 0
            if act:
                bits = act.get("status") or act.get("bits") or []
                for b, v in enumerate(bits[:10]):
                    if v:
                        status |= (1 << b)
                k = act.get("keyCode") or [0, 0]
                kc, ki = int(k[0]) & 0xFF, int(k[1]) & 0xFF
            base = 7 + 4 * i
            pkt[base], pkt[base + 1] = (status >> 8) & 0xFF, status & 0xFF
            pkt[base + 2], pkt[base + 3] = kc, ki
        return self.transfer(pkt)

    def set_all_dks(self, dks_actions, slots=20):
        """写入全部 DKS 槽位（未提供的槽位写空，即关闭）"""
        for i in range(slots):
            entry = dks_actions[i] if i < len(dks_actions) else None
            actions = []
            if entry and entry.get("keyCode"):
                key_codes = entry["keyCode"]
                statuses = entry.get("status") or []
                for k in range(4):
                    kc = key_codes[k] if k < len(key_codes) else [0, 0]
                    st = statuses[k] if k < len(statuses) else [0] * 10
                    actions.append({"status": [int(bool(v)) for v in st[:10]],
                                    "keyCode": [int(kc[0]) & 0xFF, int(kc[1]) & 0xFF]})
            else:
                actions = [{"status": [0] * 10, "keyCode": [0, 0]} for _ in range(4)]
            self.set_dks(i, actions)
            time.sleep(0.02)

    # ---------- 背光 / 键盘模式 ----------
    def set_light_config(self, cfg):
        """下发背光模式配置 {mode, speed, color:[r,g,b], brightness, colorful}"""
        pkt = self.frame(CMD_LIGHTING_SET_VALUE, SUB_LIGHT_INFO, cfg.get("mode", 0) & 0xFF)
        pkt[4] = cfg.get("speed", 0) & 0xFF
        r, g, b = (cfg.get("color") or [0, 0, 0])[:3]
        pkt[5], pkt[6], pkt[7] = r & 0xFF, g & 0xFF, b & 0xFF
        pkt[8] = cfg.get("brightness", 0) & 0xFF
        pkt[9] = cfg.get("colorful", 0) & 0xFF
        return self.transfer(pkt)

    def get_light_config(self):
        pkt = self.frame(CMD_LIGHTING_GET_VALUE, SUB_LIGHT_INFO)
        res = self.transfer(pkt)
        if res is None or len(res) < 10:
            return None
        # 响应：[rid, cmd, sub, mode, ?, speed, r, g, b, brightness, colorful]
        return {"mode": res[3], "speed": res[5], "color": (res[6], res[7], res[8]),
                "brightness": res[9], "colorful": res[10]}

    # ---------- 配置整体应用 ----------
    def apply_profile(self, cfg, with_led=True, with_keys=True,
                      with_dks=True, verify=True):
        """应用一套完整配置

        顺序参照 ATK Hub 自身：背光模式 → 行程 → RT → 灯效 → 键位 → DKS
        """
        dc = cfg.get("deviceConfig", cfg)
        steps = []

        if dc.get("lightConfig"):
            self.set_light_config(dc["lightConfig"])
            steps.append(("背光模式", "mode=%s 亮度=%s" % (dc["lightConfig"].get("mode"),
                                                    dc["lightConfig"].get("brightness")), True))
        if dc.get("keysTravel"):
            self.set_all_travel(dc["keysTravel"])
            steps.append(("按键行程", "%d 键" % sum(len(r) for r in dc["keysTravel"]), True))
        if dc.get("keysRt"):
            self.set_all_rt(dc["keysRt"])
            steps.append(("RT 键程", "%d 键" % sum(len(r) for r in dc["keysRt"]), True))
        if with_led and dc.get("customLight"):
            m = profile_to_led_matrix(dc)
            ok, bad = self.apply_custom_led_matrix(m, verify=verify)
            steps.append(("灯效", "%d 颗" % sum(len(r) for r in m), ok))
        if with_keys and cfg.get("keyActions"):
            mat = self.build_keymap(cfg["keyActions"])
            ok, mism = self.set_keymap(mat, verify=verify)
            steps.append(("键位映射", "%d 键/层" % (len(mat[0]) * len(mat[0][0])), ok))
        if with_dks:
            self.set_all_dks(dc.get("dksActions") or [])
            steps.append(("DKS", "%d 槽" % len([x for x in (dc.get("dksActions") or []) if x]), True))
        return steps


def profile_to_led_matrix(device_config):
    """把配置中的 customLight 转成 RGB 矩阵（忽略多层嵌套，取每组第一个颜色）"""
    matrix = []
    for row in device_config.get("customLight", []):
        line = []
        for cell in row:
            rgb = _first_rgb(cell) or (0, 0, 0)
            line.append(tuple(max(0, min(255, int(v))) for v in rgb))
        matrix.append(line)
    return matrix


def _first_rgb(node, depth=0):
    """在任意嵌套层级中找到第一个 RGB 三元组"""
    if depth > 6:
        return None
    if isinstance(node, (list, tuple)):
        vals = list(node)
        if len(vals) == 3 and all(isinstance(v, int) for v in vals) \
                and all(0 <= v <= 255 for v in vals):
            return tuple(vals)
        for child in vals:
            got = _first_rgb(child, depth + 1)
            if got:
                return got
    return None


def _cmd():
    """命令行：备份 / 应用 / 还原 灯效"""
    import argparse, json, os
    ap = argparse.ArgumentParser(description="Fuzzy 60 V2 灯效控制")
    ap.add_argument("--backup", metavar="FILE", help="备份当前灯效到文件")
    ap.add_argument("--restore", metavar="FILE", help="从备份文件还原灯效")
    ap.add_argument("--apply", metavar="NAME", help="应用 profile 名称对应的灯效")
    ap.add_argument("--read", action="store_true", help="只读取当前灯效")
    args = ap.parse_args()

    with Fuzzy60() as kb:
        print("设备:", kb.product, "| VIA v%s" % kb.get_protocol_version())
        if args.read:
            for i, line in enumerate(kb.get_custom_led_matrix()):
                print("row%2d: %s" % (i, " ".join("%02X%02X%02X" % c for c in line)))
            return
        if args.backup:
            m = kb.get_custom_led_matrix()
            with open(args.backup, "w", encoding="utf-8") as f:
                json.dump(m, f)
            print("已备份 %d 行到 %s" % (len(m), args.backup))
            return
        if args.restore:
            with open(args.restore, encoding="utf-8") as f:
                m = json.load(f)
            m = [ [tuple(cell) for cell in row] for row in m ]
            kb.apply_custom_led_matrix(m)
            print("已还原灯效")
            return
        if args.apply:
            import atk_profile
            profiles = dict(atk_profile.load_profiles())
            if args.apply not in profiles:
                print("找不到配置 %s；可选：%s" % (args.apply, "、".join(profiles)))
                return
            cfg = profiles[args.apply]
            if "deviceConfig" in cfg:
                cfg = cfg["deviceConfig"]
            m = profile_to_led_matrix(cfg)
            kb.apply_custom_led_matrix(m)
            print("已应用《%s》灯效：%d 行 x %d 列" % (args.apply, len(m), len(m[0]) if m else 0))
            return
        ap.print_help()


if __name__ == "__main__":
    _cmd()
