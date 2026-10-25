# -*- coding: utf-8 -*-
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

"""确认到底能写到第几列：给每行写各不相同的一串颜色"""
import time
import atk_kb as K

with K.Fuzzy60() as kb:
    for r in range(5):
        colors = [(0x10 + i * 0x10, 0x20 + r * 0x10, 0x90) for i in range(14)]
        kb.set_custom_led(r, 0, colors[:8])
        time.sleep(0.05)
        kb.set_custom_led(r, 8, colors[8:])
        time.sleep(0.05)
    time.sleep(0.3)
    kb.save_lamplight()
    time.sleep(0.3)
    m = kb.get_custom_led_matrix()
    for i, line in enumerate(m):
        print("row%d(%d): %s" % (i, len(line), " ".join("%02X%02X%02X" % c for c in line)))
