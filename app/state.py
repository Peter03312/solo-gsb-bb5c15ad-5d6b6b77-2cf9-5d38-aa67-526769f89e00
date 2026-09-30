"""页面状态序列化：房间、互扰、话筒与当前/历史方案。"""
from __future__ import annotations

from .db import get_db


def _mic_rows(db):
    return db.execute(
        """SELECT m.*, r.name AS room_name
             FROM microphones m
             JOIN rooms r ON r.id = m.room_id
            ORDER BY m.id"""
    ).fetchall()


def _freq_map(db):
    fmap = {}
    for row in db.execute(
        "SELECT microphone_id, freq_khz FROM microphone_frequencies ORDER BY freq_khz"
    ):
        fmap.setdefault(row["microphone_id"], []).append(row["freq_khz"])
    return fmap


def get_state() -> dict:
    db = get_db()

    rooms = [dict(r) for r in db.execute(
        "SELECT id, name FROM rooms ORDER BY id").fetchall()]

    interferences = [dict(r) for r in db.execute(
        """SELECT i.id, i.room_a_id, i.room_b_id,
                  a.name AS room_a_name, b.name AS room_b_name
             FROM interferences i
             JOIN rooms a ON a.id = i.room_a_id
             JOIN rooms b ON b.id = i.room_b_id
            ORDER BY i.id""").fetchall()]

    fmap = _freq_map(db)
    mics = []
    for row in _mic_rows(db):
        d = dict(row)
        d["freqs"] = fmap.get(d["id"], [])
        mics.append(d)

    active = db.execute(
        "SELECT * FROM schemes WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()

    active_scheme = None
    if active is not None:
        assignments = [dict(a) for a in db.execute(
            "SELECT * FROM assignments WHERE scheme_id = ? ORDER BY id",
            (active["id"],)).fetchall()]
        active_scheme = dict(active)
        active_scheme["assignments"] = assignments

    history = [dict(r) for r in db.execute(
        """SELECT id, created_at, is_active, total_count, high_count,
                  normal_count, assigned, high_assigned, normal_assigned
             FROM schemes ORDER BY id DESC LIMIT 20""").fetchall()]

    return {
        "rooms": rooms,
        "interferences": interferences,
        "microphones": mics,
        "active_scheme": active_scheme,
        "history": history,
    }
