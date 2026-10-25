# -*- coding: utf-8 -*-
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

"""
读取 ATK HUB 保存在 IndexedDB 中的键盘配置（V8 序列化格式）

Chromium 的 IndexedDB blob 文件里存放的是 V8 ValueSerializer 序列化的对象，
结构近似： { name: <配置名>, data: <JSON 字符串> }
本模块负责把它解析出来。
"""
import os, json, re

BLOB_ROOT = os.path.join(os.path.expandvars("%APPDATA%"), "ATK-HUB",
                         "IndexedDB", "file__0.indexeddb.blob")

# V8 ValueSerializer 标签
TAG_BEGIN_OBJECT = 0x6F   # 'o'
TAG_END_OBJECT = 0x7B     # '{'
TAG_ONE_BYTE_STRING = 0x22
TAG_TWO_BYTE_STRING = 0x63
TAG_UTF8_STRING = 0x53
V8_HEADER = bytes([0xFF, 0x0F])  # 版本头结束标记


def _read_varint(buf, pos):
    """V8 变长整数（LEB128）"""
    result, shift = 0, 0
    while True:
        if pos >= len(buf):
            raise ValueError("varint 越界")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result, pos


def _read_value(buf, pos):
    """读取一个 V8 序列化值"""
    if pos >= len(buf):
        raise ValueError("读取越界")
    tag = buf[pos]
    if tag == TAG_BEGIN_OBJECT:
        pos += 1
        obj = {}
        while True:
            if pos >= len(buf):
                raise ValueError("对象未闭合")
            if buf[pos] == TAG_END_OBJECT:
                pos += 1
                break
            key, pos = _read_value(buf, pos)
            val, pos = _read_value(buf, pos)
            obj[key] = val
        return obj, pos
    if tag == TAG_ONE_BYTE_STRING:
        length, pos = _read_varint(buf, pos + 1)
        return buf[pos:pos + length].decode("latin1"), pos + length
    if tag == TAG_TWO_BYTE_STRING:
        length, pos = _read_varint(buf, pos + 1)
        return buf[pos:pos + length].decode("utf-16-le"), pos + length
    if tag == TAG_UTF8_STRING:
        length, pos = _read_varint(buf, pos + 1)
        return buf[pos:pos + length].decode("utf-8", "ignore"), pos + length
    # 标量类型（V8 serialization tag）
    if tag == 0x30:        # '0' null
        return None, pos + 1
    if tag == 0x54:        # 'T' true
        return True, pos + 1
    if tag == 0x46:        # 'F' false
        return False, pos + 1
    if tag == 0x5F:        # '_' undefined
        return None, pos + 1
    if tag == 0x49:        # 'I' int32（zigzag 编码）
        raw, pos = _read_varint(buf, pos + 1)
        return (raw >> 1) ^ -(raw & 1), pos
    if tag == 0x55:        # 'U' uint32
        return _read_varint(buf, pos + 1)[0], pos + 1
    if tag == 0x4E:        # 'N' double（8 字节）
        import struct as _s
        return _s.unpack("<d", buf[pos + 1:pos + 9])[0], pos + 9
    raise ValueError("未知标签 0x%02X @ %d" % (tag, pos))


def parse_v8_objects(data):
    """从一个 blob 数据中解析出所有 {name, data} 对象"""
    results = []
    header = data.find(V8_HEADER)
    start = header + 2 if header >= 0 else 0
    pos = start
    while pos < len(data):
        obj_pos = data.find(bytes([TAG_BEGIN_OBJECT]), pos)
        if obj_pos < 0:
            break
        try:
            obj, end = _read_value(data, obj_pos)
        except Exception:
            pos = obj_pos + 1
            continue
        if isinstance(obj, dict) and "name" in obj and "data" in obj:
            results.append(obj)
            pos = end
        else:
            pos = obj_pos + 1
    return results


def load_profiles(blob_root=BLOB_ROOT):
    """列出所有键盘配置，返回 [(名称, 配置dict), ...]"""
    found = {}
    if not os.path.isdir(blob_root):
        return []
    for dirpath, dirnames, filenames in os.walk(blob_root):
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "rb") as f:
                    data = f.read(400 * 1024 * 1024)
            except Exception:
                continue
            if (b"keyActions" not in data
                    and "keyActions".encode("utf-16-le") not in data):
                continue
            for obj in parse_v8_objects(data):
                payload = obj.get("data")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        continue
                if not isinstance(payload, dict) or "keyActions" not in payload:
                    continue
                name = obj.get("name") or "未命名"
                prev = found.get(name)
                if prev is None or len(json.dumps(payload)) > len(json.dumps(prev)):
                    found[name] = payload
    return sorted(found.items(), key=lambda kv: kv[0])


def export_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")


def save_all(out_dir=None):
    """把全部配置落盘为 JSON，返回 [(名称, 文件路径)]"""
    out_dir = out_dir or export_dir()
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    for name, payload in load_profiles():
        safe = re.sub(r'[\\/:*?"<>|]', "_", name)
        path = os.path.join(out_dir, "%s.json" % safe)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        saved.append((name, path))
    return saved


if __name__ == "__main__":
    profiles = load_profiles()
    print("找到 %d 套配置：" % len(profiles))
    for name, payload in profiles:
        print("  -", name, "| 字段:", ", ".join(sorted(payload.keys())))
    for name, path in save_all():
        print("已导出:", path)
