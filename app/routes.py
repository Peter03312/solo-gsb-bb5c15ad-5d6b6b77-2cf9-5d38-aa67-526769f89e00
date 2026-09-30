"""HTTP API：房间 / 互扰 / 话筒 / 编排方案。

任何输入的增删改成功后，都会把现存方案标记为过期（is_active=0）。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from .db import get_db
from .solver import SolveError, solve
from .state import get_state

bp = Blueprint("api", __name__)


# ------------------------------------------------------------------
# 工具
# ------------------------------------------------------------------
def bad(message: str, devices=None, status=400):
    return jsonify({"ok": False, "error": message,
                    "devices": devices or []}), status


def expire_schemes(db):
    """输入变更：旧方案全部标为过期。"""
    db.execute("UPDATE schemes SET is_active = 0 WHERE is_active = 1")


def require_json():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, bad("请求体必须是 JSON 对象")
    return data, None


def parse_int(value, field, devices=None, allow_none=False):
    if value is None and allow_none:
        return None, None
    if isinstance(value, bool):
        return None, bad(f"{field} 必须是整数", devices)
    if not isinstance(value, int):
        return None, bad(f"{field} 必须是整数", devices)
    return value, None


def _load_mics_for_solver(db):
    rooms = db.execute("SELECT id, name FROM rooms").fetchall()
    room_ids = {r["id"] for r in rooms}

    interfering = set()
    for r in db.execute("SELECT room_a_id, room_b_id FROM interferences"):
        interfering.add((min(r["room_a_id"], r["room_b_id"]),
                         max(r["room_a_id"], r["room_b_id"])))

    mics = []
    for row in db.execute("SELECT * FROM microphones ORDER BY id").fetchall():
        freqs = [r["freq_khz"] for r in db.execute(
            "SELECT freq_khz FROM microphone_frequencies WHERE microphone_id = ?"
            " ORDER BY freq_khz", (row["id"],))]
        if row["room_id"] not in room_ids:
            raise SolveError(
                f"话筒 {row['name']} 所属房间不存在，请重新选择房间",
                [row["name"]])
        mics.append({
            "id": row["id"],
            "name": row["name"],
            "room_id": row["room_id"],
            "priority": row["priority"],
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "guard_khz": row["guard_khz"],
            "locked_freq": row["locked_freq"],
            "freqs": freqs,
        })
    return mics, interfering


# ------------------------------------------------------------------
# 状态
# ------------------------------------------------------------------
@bp.get("/api/state")
def api_state():
    return jsonify({"ok": True, **get_state()})


# ------------------------------------------------------------------
# 房间
# ------------------------------------------------------------------
@bp.post("/api/rooms")
def create_room():
    data, err = require_json()
    if err:
        return err
    name = (data.get("name") or "").strip()
    if not name:
        return bad("房间名称不能为空")
    db = get_db()
    try:
        cur = db.execute("INSERT INTO rooms (name) VALUES (?)", (name,))
    except Exception:
        return bad(f"房间 {name} 已存在")
    expire_schemes(db)
    db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid, **get_state()})


@bp.put("/api/rooms/<int:rid>")
def update_room(rid):
    data, err = require_json()
    if err:
        return err
    name = (data.get("name") or "").strip()
    if not name:
        return bad("房间名称不能为空")
    db = get_db()
    if not db.execute("SELECT 1 FROM rooms WHERE id = ?", (rid,)).fetchone():
        return bad("房间不存在", status=404)
    try:
        db.execute("UPDATE rooms SET name = ? WHERE id = ?", (name, rid))
    except Exception:
        return bad(f"房间 {name} 名称已被占用")
    expire_schemes(db)
    db.commit()
    return jsonify({"ok": True, **get_state()})


@bp.delete("/api/rooms/<int:rid>")
def delete_room(rid):
    db = get_db()
    using = db.execute(
        "SELECT name FROM microphones WHERE room_id = ? LIMIT 1", (rid,)
    ).fetchone()
    if using:
        return bad(f"房间下仍有话筒（如 {using['name']}），请先删除或迁移这些话筒",
                   [using["name"]])
    db.execute("DELETE FROM rooms WHERE id = ?", (rid,))
    expire_schemes(db)
    db.commit()
    return jsonify({"ok": True, **get_state()})


# ------------------------------------------------------------------
# 互扰关系
# ------------------------------------------------------------------
@bp.post("/api/interferences")
def create_interference():
    data, err = require_json()
    if err:
        return err
    a, e = parse_int(data.get("room_a_id"), "room_a_id")
    if e:
        return e
    b, e = parse_int(data.get("room_b_id"), "room_b_id")
    if e:
        return e
    if a == b:
        return bad("互扰关系必须选择两个不同的房间")
    db = get_db()
    ids = {r["id"] for r in db.execute("SELECT id FROM rooms").fetchall()}
    if a not in ids or b not in ids:
        return bad("房间不存在，请刷新后重试")
    try:
        db.execute(
            "INSERT INTO interferences (room_a_id, room_b_id) VALUES (?, ?)",
            (min(a, b), max(a, b)))
    except Exception:
        return bad("这两个房间的互扰关系已存在")
    expire_schemes(db)
    db.commit()
    return jsonify({"ok": True, **get_state()})


@bp.delete("/api/interferences/<int:iid>")
def delete_interference(iid):
    db = get_db()
    db.execute("DELETE FROM interferences WHERE id = ?", (iid,))
    expire_schemes(db)
    db.commit()
    return jsonify({"ok": True, **get_state()})


# ------------------------------------------------------------------
# 话筒
# ------------------------------------------------------------------
def _validate_mic_payload(data):
    """校验并规整话筒录入数据，返回 (fields, freqs) 或抛出 ValueError。"""
    name = (str(data.get("name") or "")).strip()
    if not name:
        raise ValueError("话筒名称不能为空")

    room_id = data.get("room_id")
    if not isinstance(room_id, int) or isinstance(room_id, bool):
        raise ValueError("必须选择所属房间")

    priority = data.get("priority", "normal")
    if priority not in ("high", "normal"):
        raise ValueError("优先级只能是 high 或 normal")

    start, end = data.get("start_time"), data.get("end_time")
    if isinstance(start, bool) or isinstance(end, bool) or \
            not isinstance(start, int) or not isinstance(end, int):
        raise ValueError("启用时段的开始/结束时间必须是整数（建议为分钟数）")
    if start >= end:
        raise ValueError(f"话筒 {name} 的启用时段非法：开始时间必须早于结束时间")

    guard = data.get("guard_khz")
    if isinstance(guard, bool) or not isinstance(guard, int) or guard < 0:
        raise ValueError("保护间隔必须是非负整数（kHz）")

    raw_freqs = data.get("freqs", [])
    if not isinstance(raw_freqs, list) or not raw_freqs:
        raise ValueError(f"话筒 {name} 至少需要一个可用频点")
    freqs = []
    for x in raw_freqs:
        if isinstance(x, bool) or not isinstance(x, int):
            raise ValueError(f"话筒 {name} 的可用频点必须全部为整数（kHz）")
        freqs.append(x)
    freqs = sorted(set(freqs))

    locked = data.get("locked_freq")
    if locked is not None:
        if isinstance(locked, bool) or not isinstance(locked, int):
            raise ValueError("锁定频点必须是整数（kHz）")
        if locked not in freqs:
            raise ValueError(f"话筒 {name} 的锁定频点 {locked} kHz 不在其可用频点列表中")

    return {
        "name": name,
        "room_id": room_id,
        "priority": priority,
        "start_time": start,
        "end_time": end,
        "guard_khz": guard,
        "locked_freq": locked,
    }, freqs


def _persist_mic(db, mic_id, fields, freqs):
    if mic_id is None:
        cur = db.execute(
            """INSERT INTO microphones
               (name, room_id, priority, start_time, end_time, guard_khz, locked_freq)
               VALUES (:name, :room_id, :priority, :start_time, :end_time,
                       :guard_khz, :locked_freq)""",
            fields)
        mic_id = cur.lastrowid
    else:
        db.execute(
            """UPDATE microphones SET
                 name=:name, room_id=:room_id, priority=:priority,
                 start_time=:start_time, end_time=:end_time,
                 guard_khz=:guard_khz, locked_freq=:locked_freq
               WHERE id = :id""",
            {**fields, "id": mic_id})
        db.execute("DELETE FROM microphone_frequencies WHERE microphone_id = ?",
                   (mic_id,))
    db.executemany(
        "INSERT INTO microphone_frequencies (microphone_id, freq_khz) VALUES (?, ?)",
        [(mic_id, f) for f in freqs])
    return mic_id


@bp.post("/api/microphones")
def create_microphone():
    data, err = require_json()
    if err:
        return err
    try:
        fields, freqs = _validate_mic_payload(data)
    except ValueError as exc:
        name = (str(data.get("name") or "")).strip()
        return bad(str(exc), [name] if name else None)
    db = get_db()
    if not db.execute("SELECT 1 FROM rooms WHERE id = ?",
                      (fields["room_id"],)).fetchone():
        return bad("所属房间不存在")
    try:
        mic_id = _persist_mic(db, None, fields, freqs)
    except Exception:
        return bad(f"话筒名称 {fields['name']} 已存在")
    expire_schemes(db)
    db.commit()
    return jsonify({"ok": True, "id": mic_id, **get_state()})


@bp.put("/api/microphones/<int:mid>")
def update_microphone(mid):
    data, err = require_json()
    if err:
        return err
    db = get_db()
    if not db.execute("SELECT 1 FROM microphones WHERE id = ?", (mid,)).fetchone():
        return bad("话筒不存在", status=404)
    try:
        fields, freqs = _validate_mic_payload(data)
    except ValueError as exc:
        return bad(str(exc), [str(data.get("name") or "")])
    if not db.execute("SELECT 1 FROM rooms WHERE id = ?",
                      (fields["room_id"],)).fetchone():
        return bad("所属房间不存在")
    try:
        _persist_mic(db, mid, fields, freqs)
    except Exception as exc:
        return bad(f"保存失败：{exc}")
    expire_schemes(db)
    db.commit()
    return jsonify({"ok": True, **get_state()})


@bp.delete("/api/microphones/<int:mid>")
def delete_microphone(mid):
    db = get_db()
    db.execute("DELETE FROM microphones WHERE id = ?", (mid,))
    expire_schemes(db)
    db.commit()
    return jsonify({"ok": True, **get_state()})


# ------------------------------------------------------------------
# 编排
# ------------------------------------------------------------------
@bp.post("/api/solve")
def run_solve():
    db = get_db()
    try:
        mics, interfering = _load_mics_for_solver(db)
    except SolveError as exc:
        return bad(exc.message, exc.devices)

    if not mics:
        return bad("还没有任何话筒，请先录入话筒后再编排")

    try:
        assignment, node_count, truncated = solve(mics, interfering)
    except SolveError as exc:
        return bad(exc.message, exc.devices)

    total = len(mics)
    high_total = sum(1 for m in mics if m["priority"] == "high")
    normal_total = total - high_total
    assigned_ids = set(assignment.keys())
    high_assigned = sum(1 for m in mics
                        if m["priority"] == "high" and m["id"] in assigned_ids)
    normal_assigned = sum(1 for m in mics
                          if m["priority"] == "normal" and m["id"] in assigned_ids)

    # 新方案前先把旧方案标过期
    expire_schemes(db)
    note = None
    if truncated:
        note = "搜索规模较大达到计算上限，当前方案为可保证可行的最优近似结果"
    cur = db.execute(
        """INSERT INTO schemes
           (total_count, high_count, normal_count, assigned,
            high_assigned, normal_assigned, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (total, high_total, normal_total, len(assigned_ids),
         high_assigned, normal_assigned, note))
    scheme_id = cur.lastrowid

    for m in mics:
        if m["id"] in assignment:
            db.execute(
                """INSERT INTO assignments
                   (scheme_id, microphone_id, mic_name, freq_khz, was_locked)
                   VALUES (?, ?, ?, ?, ?)""",
                (scheme_id, m["id"], m["name"], assignment[m["id"]],
                 1 if m["locked_freq"] is not None else 0))
    db.commit()

    return jsonify({"ok": True, "node_count": node_count,
                    "truncated": truncated, **get_state()})


@bp.get("/api/schemes/<int:sid>")
def get_scheme(sid):
    db = get_db()
    scheme = db.execute("SELECT * FROM schemes WHERE id = ?", (sid,)).fetchone()
    if scheme is None:
        return bad("方案不存在", status=404)
    d = dict(scheme)
    d["assignments"] = [dict(a) for a in db.execute(
        "SELECT * FROM assignments WHERE scheme_id = ? ORDER BY id", (sid,))]
    return jsonify({"ok": True, "scheme": d})


@bp.post("/api/reset")
def reset_data():
    """清空全部数据并重新写入演示数据。"""
    db = get_db()
    db.executescript(
        """DELETE FROM assignments;
           DELETE FROM schemes;
           DELETE FROM microphone_frequencies;
           DELETE FROM microphones;
           DELETE FROM interferences;
           DELETE FROM rooms;
           DELETE FROM sqlite_sequence
            WHERE name IN ('rooms','interferences','microphones',
                           'microphone_frequencies','schemes','assignments');"""
    )
    db.commit()
    from .initdb import seed_demo
    seed_demo(db)
    db.commit()
    return jsonify({"ok": True, **get_state()})
