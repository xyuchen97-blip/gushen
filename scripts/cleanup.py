#!/usr/bin/env python3
"""
Cleanup — removes generated files older than 7 days to prevent bloat.
"""
import os, time
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
CLEAN_DIRS = [
    SKILL_DIR / "data" / "reports",       # daily report output
    SKILL_DIR / "data" / "cache",         # temporary cache
]
MAX_AGE_DAYS = 7

def cleanup():
    now = time.time()
    max_age = MAX_AGE_DAYS * 86400
    total_removed = 0
    total_freed = 0

    for directory in CLEAN_DIRS:
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
            continue
        
        for item in directory.iterdir():
            if item.is_file():
                age = now - item.stat().st_mtime
                if age > max_age:
                    size = item.stat().st_size
                    try:
                        item.unlink()
                        total_removed += 1
                        total_freed += size
                    except Exception:
                        pass
        
        # Clean empty dirs
        for item in list(directory.iterdir()):
            if item.is_dir() and not any(item.iterdir()):
                try:
                    item.rmdir()
                except Exception:
                    pass

    if total_removed > 0:
        print(f"🧹 清理完毕：删除 {total_removed} 个文件，释放 {total_freed/1024:.0f} KB")
    else:
        print("✨ 无需清理，所有文件均在7天内。")
    return total_removed

if __name__ == "__main__":
    cleanup()
