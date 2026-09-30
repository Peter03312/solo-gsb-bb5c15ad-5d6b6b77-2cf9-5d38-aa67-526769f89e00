-- 剧场无线话筒频点排演 数据库结构
-- 时间为整数，采用半开区间 [start_time, end_time)，建议单位为分钟（00:00 起算）
PRAGMA foreign_keys = ON;

-- 房间
CREATE TABLE IF NOT EXISTS rooms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 房间互扰关系（对称：存一次，查询时两个方向都算）
CREATE TABLE IF NOT EXISTS interferences (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_a_id   INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    room_b_id   INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (room_a_id, room_b_id),
    CHECK (room_a_id <> room_b_id)
);

-- 话筒
CREATE TABLE IF NOT EXISTS microphones (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    room_id      INTEGER NOT NULL REFERENCES rooms(id) ON DELETE RESTRICT,
    priority     TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('high', 'normal')),
    start_time   INTEGER NOT NULL,
    end_time     INTEGER NOT NULL,
    guard_khz    INTEGER NOT NULL CHECK (guard_khz >= 0),
    locked_freq  INTEGER,                 -- 锁定频点；NULL 表示不锁定
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (start_time < end_time)
);

-- 每台话筒的可用频点（整数 kHz）
CREATE TABLE IF NOT EXISTS microphone_frequencies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    microphone_id   INTEGER NOT NULL REFERENCES microphones(id) ON DELETE CASCADE,
    freq_khz        INTEGER NOT NULL,
    UNIQUE (microphone_id, freq_khz)
);

-- 编排方案
CREATE TABLE IF NOT EXISTS schemes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    is_active    INTEGER NOT NULL DEFAULT 1,  -- 输入变更后旧方案置 0（过期）
    total_count  INTEGER NOT NULL,
    high_count   INTEGER NOT NULL,
    normal_count INTEGER NOT NULL,
    assigned     INTEGER NOT NULL,
    high_assigned   INTEGER NOT NULL,
    normal_assigned INTEGER NOT NULL,
    note         TEXT
);

-- 方案中的获配明细（未获配设备不在此表，由方案创建时的全量设备对照得出）
CREATE TABLE IF NOT EXISTS assignments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_id    INTEGER NOT NULL REFERENCES schemes(id) ON DELETE CASCADE,
    microphone_id INTEGER,                    -- 设备删除后保留行，仅名称丢失引用
    mic_name     TEXT NOT NULL,               -- 快照名称
    freq_khz     INTEGER NOT NULL,
    was_locked   INTEGER NOT NULL DEFAULT 0
);
