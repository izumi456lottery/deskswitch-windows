# -*- coding: utf-8 -*-
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

"""
从 ATK HUB 的 IndexedDB blob 中提取键盘配置（方案 / profile）

用法:
    python export_profiles.py            # 列出所有找到的配置
    python export_profiles.py --export   # 导出为 profiles/<名称>.json
"""
import os, re, json, argparse

BLOB_ROOT = os.path.join(os.path.expandvars("%APPDATA%"), "ATK-HUB",
                         "IndexedDB", "file__0.indexeddb.blob")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")


def raw_decode_all(text, keyword="{\"name\""):
    """从文本中找出所有以 {"name" 开头的 JSON 对象"""
    decoder = json.JSONDecoder()
    pos, n = 0, len(text)
    while True:
        start = text.find(keyword, pos)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, start)
            yield obj
            pos = end
        except ValueError:
            pos = start + 1


def scan_blobs():
    found = []
    for dirpath, dirnames, filenames in os.walk(BLOB_ROOT):
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except Exception:
                continue
            if b'"keyActions"' not in data and b'k\x00e\x00y\x00A' not in data:
                continue
            for enc in ("utf-16-le", "utf-8"):
                try:
                    text = data.decode(enc, "ignore")
                except Exception:
                    continue
                for obj in raw_decode_all(text):
                    if isinstance(obj, dict) and obj.get("name") and obj.get("data"):
                        try:
                            payload = json.loads(obj["data"]) if isinstance(obj["data"], str) else obj["data"]
                        except Exception:
                            continue
                        if isinstance(payload, dict) and "keyActions" in payload:
                            found.append((obj["name"], payload, path))
                            break
    # 去重（同一配置可能出现在多个 blob 中）
    uniq = {}
    for name, payload, path in found:
        key = name
        if key not in uniq or len(json.dumps(payload)) > len(json.dumps(uniq[key][1])):
            uniq[key] = (name, payload, path)
    return list(uniq.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", action="store_true")
    args = ap.parse_args()

    profiles = scan_blobs()
    print("找到 %d 套配置:" % len(profiles))
    for name, payload, path in profiles:
        keys = list(payload.keys())
        ka = payload.get("keyActions", [])
        print("  - %s   (%d 键位层, 字段: %s)" % (name, len(ka), ", ".join(sorted(keys))))
        print("    来源: %s" % os.path.relpath(path, BLOB_ROOT))

    if args.export and profiles:
        os.makedirs(OUT_DIR, exist_ok=True)
        for name, payload, path in profiles:
            safe = re.sub(r'[\\/:*?"<>|]', "_", name)
            out = os.path.join(OUT_DIR, "%s.json" % safe)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            print("导出 -> %s" % out)


if __name__ == "__main__":
    main()
