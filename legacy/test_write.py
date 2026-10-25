# -*- coding: utf-8 -*-
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

"""诊断灯效写入：逐帧验证 + 不同延时策略"""
import time
import atk_kb as K

with K.Fuzzy60() as kb:
    color = (0x11, 0x22, 0x33)
    # 1) 写 row4 的第二段（col 8..13），立即读回
    print("--- 单段写入 row4 col8..13 ---")
    kb.set_custom_led(4, 8, [color] * 6)
    time.sleep(0.15)
    print("写后读:", " ".join("%02X%02X%02X" % c for c in (kb.get_custom_led(4, 8, 6) or [])))

    # 2) 不 save，直接读取是否保留
    print("--- 保存后读 ---")
    kb.save_lamplight()
    time.sleep(0.2)
    print("保存后:", " ".join("%02X%02X%02X" % c for c in (kb.get_custom_led(4, 8, 6) or [])))

    # 3) 逐行全量写，帧间加延时
    print("--- 逐行全量下发（帧间 30ms）---")
    target = [(0x40, 0x80, 0xC0)] * 14
    for r in range(5):
        kb.set_custom_led(r, 0, target[:8])
        time.sleep(0.03)
        kb.set_custom_led(r, 8, target[8:])
        time.sleep(0.03)
    time.sleep(0.2)
    kb.save_lamplight()
    time.sleep(0.3)
    m = kb.get_custom_led_matrix()
    for i, line in enumerate(m):
        print("row%d: %s" % (i, " ".join("%02X%02X%02X" % c for c in line)))
