"""数据库初始化：建表 + 可选演示数据。

命令行：
    python -m app.initdb          # 建表；库内无房间时按 SEED_DEMO 决定是否写演示数据
"""
import os
import sqlite3

from .db import get_db
from flask import current_app

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

# (id, 名称)
SEED_ROOMS = [
    (1, "主舞台"),
    (2, "侧舞台"),
    (3, "乐池"),
]

# (room_a, room_b)
SEED_INTERFERENCE = [(1, 2), (2, 3)]

# id, 名称, 房间, 优先级, 开始, 结束, 保护间隔, 锁定频点, [可用频点]
SEED_MICS = [
    (1, "主持1号", 1, "high", 1080, 1320, 400, 600000,
     [600000, 602000, 604000, 606000]),
    (2, "主持2号", 1, "high", 1080, 1320, 300, None,
     [600000, 602000, 604000, 606000]),
    (3, "主演A", 1, "high", 1140, 1260, 200, None,
     [600000, 602000, 604000, 606000]),
    (4, "主演B", 1, "normal", 1200, 1300, 500, None,
     [600000, 602000, 604000, 606000]),
    (5, "群演1号", 1, "normal", 1080, 1140, 200, None,
     [600000, 602000, 604000, 606000]),
    (6, "乐池指挥", 3, "high", 1080, 1320, 300, 606000,
     [602000, 604000, 606000]),
    (7, "侧台联络", 2, "normal", 1080, 1320, 300, None,
     [600000, 602000]),  # 与锁定的主持1号/乐池指挥冲突，可用频点不足
    (8, "侧台道具", 2, "normal", 1140, 1260, 200, None,
     [600000, 602000, 604000, 606000]),
]


def init_schema(db):
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        db.executescript(fh.read())


def seed_demo(db):
    for rid, name in SEED_ROOMS:
        db.execute("INSERT INTO rooms (id, name) VALUES (?, ?)", (rid, name))
    for a, b in SEED_INTERFERENCE:
        db.execute(
            "INSERT INTO interferences (room_a_id, room_b_id) VALUES (?, ?)",
            (a, b))
    for mid, name, room, pri, st, en, guard, locked, freqs in SEED_MICS:
        db.execute(
            """INSERT INTO microphones
               (id, name, room_id, priority, start_time, end_time,
                guard_khz, locked_freq)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (mid, name, room, pri, st, en, guard, locked))
        for f in freqs:
            db.execute(
                "INSERT INTO microphone_frequencies (microphone_id, freq_khz)"
                " VALUES (?, ?)", (mid, f))


def main():
    """无应用上下文时的独立初始化（容器 entrypoint 调用）。"""
    path = os.environ.get("FREQ_DB_PATH",
                          os.path.join(os.getcwd(), "data", "freq.db"))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    init_schema(conn)

    empty = conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0
    if empty and os.environ.get("SEED_DEMO", "1") == "1":
        seed_demo(conn)
    conn.commit()
    conn.close()
    print(f"[initdb] ready: {path} (seeded={empty})")


if __name__ == "__main__":
    main()
