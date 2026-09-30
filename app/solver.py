"""频点编排求解器。

规则
----
1. 两台话筒位于同一房间、或所在房间存在互扰关系，且启用时段
   [start, end) 有重叠时构成"冲突对"，所配频点差不得小于双方保护
   间隔的较大值。
2. 锁定话筒必须保留锁定频点：锁定频点必须在其可用频点列表内；
   锁定项之间若冲突则直接拒绝编排。
3. 优化目标（字典序）：
       先最大化获配的高优先级话筒数，
       再最大化获配总数。

算法
----
每台话筒权重 = N+1（高优先级）或 1（普通）。高优先级单台权重大于所有
普通话筒权重之和，因此最大化权重和与上述字典序目标等价。

冲突图按连通分量分解（只有同房间/互扰且时间重叠的话筒之间才有边），
各分量独立求解后合并。分量内部：锁定话筒在根节点固定并前向收窄邻居
值域；其余话筒用记忆化分支限界搜索，值域以位掩码表示，配合 MRV 选
变量、最小约束值（LCV）排序、空值域强制放弃与乐观上界剪枝。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

# 单个分量允许探索的搜索状态上限。典型剧场规模（≤30 台、若干冲突连通
# 分量）远在此限内即可精确求解；超过上限时返回当前最优可行方案并标记
# truncated（频点/时间/房间把冲突图切成多个小分量时尤其宽松）。
NODE_LIMIT = 300_000

# 剪枝哨兵：小于任何真实权重（真实权重为非负二元组）
_SENTINEL = (-1, -1)


class SolveError(Exception):
    """编排前置条件不满足（非法输入、锁定互冲等）。"""

    def __init__(self, message: str, devices: Optional[List[str]] = None):
        super().__init__(message)
        self.message = message
        # 相关设备名，前端可高亮
        self.devices = devices or []


def _overlap(a: dict, b: dict) -> bool:
    """半开区间 [start, end) 是否重叠。"""
    return a["start_time"] < b["end_time"] and b["start_time"] < a["end_time"]


def solve(mics: List[dict],
          interfering_rooms: Set[Tuple[int, int]]
          ) -> Tuple[Dict[int, int], int, bool]:
    """求解频点分配。

    参数
    ----
    mics: 话筒字典列表，字段
        id, name, room_id, priority('high'/'normal'),
        start_time, end_time, guard_khz, locked_freq,
        freqs(可用频点 int 列表)
    interfering_rooms: 互扰房间 id 的无序对集合，同房间始终视为互扰。

    返回 (assignments, state_count, truncated)
        assignments: {microphone_id: freq_khz}，未获配设备不在字典中
        truncated: 是否因达到搜索状态上限而提前停止（仍返回当前最优
                   可行方案，但不保证全局最优）
    """
    n = len(mics)

    # ---- 1. 基础合法性校验 ----
    bad_time = [m["name"] for m in mics
                if not isinstance(m["start_time"], int)
                or not isinstance(m["end_time"], int)
                or isinstance(m["start_time"], bool)
                or isinstance(m["end_time"], bool)
                or m["start_time"] >= m["end_time"]]
    if bad_time:
        raise SolveError(
            "以下话筒的启用时段非法（开始时间必须早于结束时间）："
            + "、".join(bad_time),
            bad_time,
        )

    # ---- 2. 冲突对与所需最小频点间隔 ----
    def rooms_conflict(x: dict, y: dict) -> bool:
        if x["room_id"] == y["room_id"]:
            return True
        pair = (min(x["room_id"], y["room_id"]),
                max(x["room_id"], y["room_id"]))
        return pair in interfering_rooms

    adj: Dict[int, Dict[int, int]] = {m["id"]: {} for m in mics}
    for i in range(n):
        a = mics[i]
        for j in range(i + 1, n):
            b = mics[j]
            if _overlap(a, b) and rooms_conflict(a, b):
                gap = max(a["guard_khz"], b["guard_khz"])
                adj[a["id"]][b["id"]] = gap
                adj[b["id"]][a["id"]] = gap

    # ---- 3. 锁定频点校验 ----
    bad_lock = [m["name"] for m in mics
                if m["locked_freq"] is not None
                and m["locked_freq"] not in m["freqs"]]
    if bad_lock:
        raise SolveError(
            "以下话筒的锁定频点不在其可用频点列表中：" + "、".join(bad_lock),
            bad_lock,
        )

    lock_pairs: Set[Tuple[str, str]] = set()
    for i in range(n):
        a = mics[i]
        if a["locked_freq"] is None:
            continue
        for j in range(i + 1, n):
            b = mics[j]
            if b["locked_freq"] is None:
                continue
            gap = adj[a["id"]].get(b["id"])
            if gap is not None and abs(a["locked_freq"] - b["locked_freq"]) < gap:
                lock_pairs.add(tuple(sorted((a["name"], b["name"]))))
    if lock_pairs:
        pairs_txt = "；".join(f"{x} 与 {y}" for x, y in sorted(lock_pairs))
        involved = sorted({name for p in lock_pairs for name in p})
        raise SolveError(
            f"锁定频点互冲（{pairs_txt}）：频点差小于保护间隔要求，"
            "请调整锁定频点或保护间隔后再编排。",
            involved,
        )

    # ---- 4. 权重、值域、锁定 ----
    high_w = n + 1
    weight: Dict[int, int] = {
        m["id"]: high_w if m["priority"] == "high" else 1 for m in mics
    }
    fixed: Dict[int, int] = {
        m["id"]: m["locked_freq"] for m in mics if m["locked_freq"] is not None
    }
    domain: Dict[int, List[int]] = {
        m["id"]: [m["locked_freq"]] if m["locked_freq"] is not None
        else sorted(set(m["freqs"]))
        for m in mics
    }

    # ---- 5. 冲突图连通分量 ----
    components: List[List[int]] = []
    seen: Set[int] = set()
    for m in mics:
        start = m["id"]
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        comp: List[int] = []
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        components.append(comp)

    # ---- 6. 各分量求解并合并 ----
    result: Dict[int, int] = {}
    state_count = 0
    truncated = False
    for comp in components:
        sub, states, trunc = _solve_component(
            comp, adj, domain, fixed, weight)
        result.update(sub)
        state_count += states
        truncated = truncated or trunc

    # 锁定话筒必须出现在结果中
    for mid, f in fixed.items():
        if result.get(mid) != f:
            name = next(m["name"] for m in mics if m["id"] == mid)
            raise SolveError(
                f"锁定话筒 {name}（频点 {f} kHz）无法在约束下保留，"
                "请调整相关设置。",
                [name],
            )

    return result, state_count, truncated




def _solve_component(comp, adj, domain, fixed, weight):
    """单个连通分量：在"话筒-频点候选"冲突图上求最大权独立集。

    每个候选 = (话筒 u, 频点 f) 的一个位。冲突关系：
      * 同一话筒的任意两个候选（一台话筒至多配一个频点）；
      * 相邻话筒取值差小于保护间隔的两个候选。
    锁定话筒的候选必选：先加入答案并从图中删除其全部冲突点。
    候选点权为元组（高优先级 (1,0)，普通 (0,1)），字典序比较实现
    "先最大化高优先级获配数、再最大化获配总数"。

    搜索按话筒分支：每步选一台话筒，依次尝试它当前剩余的每个频点
    （取点后删除其冲突点与该话筒整组候选），再尝试"不配该话筒"
    （删除整组候选）。集合运算全部用整数位掩码，配合 MRV、LCV、
    乐观上界与贪心下界剪枝。

    返回 (assignment: {mic_id: freq}, state_count, truncated)。
    """
    compset = set(comp)

    # ---- 候选点 ----
    cand_mic: List[int] = []
    cand_freq: List[int] = []
    per_mic: Dict[int, Tuple[int, int]] = {}
    group_mask: Dict[int, int] = {}
    forced: Dict[int, int] = {}
    for u in sorted(comp):
        start = len(cand_mic)
        if u in fixed:
            cand_mic.append(u)
            cand_freq.append(fixed[u])
            forced[u] = start
        else:
            for f in domain[u]:
                cand_mic.append(u)
                cand_freq.append(f)
        end = len(cand_mic)
        per_mic[u] = (start, end)
        group_mask[u] = (((1 << (end - start)) - 1) << start)
    V = len(cand_mic)

    # ---- 冲突邻接（位掩码）----
    conflict = [0] * V
    for u in comp:  # 同话筒互斥
        block = group_mask[u]
        s, e = per_mic[u]
        for p in range(s, e):
            conflict[p] |= block ^ (1 << p)
    for ai in range(V):  # 相邻话筒频点互斥
        u = cand_mic[ai]
        fa = cand_freq[ai]
        for v, gap in adj[u].items():
            if v not in compset or v < u:
                continue
            s, e = per_mic[v]
            for bi in range(s, e):
                if abs(fa - cand_freq[bi]) < gap:
                    conflict[ai] |= 1 << bi
                    conflict[bi] |= 1 << ai

    wgt: List[Tuple[int, int]] = [
        (1, 0) if weight[cand_mic[p]] > len(comp) else (0, 1)
        for p in range(V)
    ]
    # 话筒权（每台唯一）
    mic_w = {u: ((1, 0) if weight[u] > len(comp) else (0, 1))
             for u in comp}

    # ---- 必选锁定候选 ----
    chosen: Dict[int, int] = {}
    available = (1 << V) - 1
    for u, p in forced.items():
        chosen[u] = cand_freq[p]
        available &= ~(conflict[p] | group_mask[u])

    states = 0
    limit_hit = [False]
    popcount = int.bit_count

    # 话筒位（决策变量按 comp 顺序编号）
    var_us = [u for u in comp if u not in forced]
    micbit = {u: 1 << t for t, u in enumerate(var_us)}
    high_mics = 0
    for t, u in enumerate(var_us):
        if mic_w[u][0]:
            high_mics |= 1 << t

    def greedy_set(cand):
        """cand 上的贪心独立集：按高优、候选少、邻居多顺序逐台取可行候选。"""
        gm = 0
        present = set()
        m = cand
        while m:
            b = m & -m
            present.add(cand_mic[b.bit_length() - 1])
            m ^= b
        order = sorted(present,
                       key=lambda x: (0 if mic_w[x][0] else 1,
                                      popcount(cand & group_mask[x]),
                                      -len(adj[x])))
        gw = (0, 0)
        for u in order:
            block = cand & group_mask[u]
            mm = block
            picked = -1
            while mm:
                b = mm & -mm
                p = b.bit_length() - 1
                mm ^= b
                if not (conflict[p] & gm):
                    picked = p
                    break
            if picked >= 0:
                gm |= 1 << picked
                gw = (gw[0] + mic_w[u][0], gw[1] + mic_w[u][1])
        return gm, gw

    gmask, gw = greedy_set(available)
    best_w = [gw]
    best_mask = [gmask]
    memo: Dict[int, Tuple[int, Tuple[int, int]]] = {}

    # 高优话筒的全部候选点（团覆盖上界分类用）
    high_point_set = 0
    for u in var_us:
        if mic_w[u][0]:
            high_point_set |= group_mask[u]

    def clique_cover_points(sub):
        """候选点冲突图上的贪心团覆盖数（最大独立集大小的合法上界）。

        把剩余候选点划分为若干团：每个团内任意两点都冲突，
        独立集从每个团中至多取一点，故独立集大小 <= 团数。
        """
        groups = 0
        remain = sub
        while remain:
            # 贪心地扩展一个团：新点必须与团内所有已选点相邻
            clique = 0
            m = remain
            while m:
                b = m & -m
                p = b.bit_length() - 1
                m ^= b
                if clique == 0 or (conflict[p] & clique) == clique:
                    clique |= b
            remain &= ~clique
            groups += 1
        return groups

    def tight_bound(cand):
        """候选点图团覆盖上界，返回 (高优界, 总数界)。

        计算 O(点数^2)，仅在候选点规模较小时调用才划算。
        """
        total_cover = clique_cover_points(cand)
        high_cover = clique_cover_points(cand & high_point_set)
        return (high_cover, total_cover)

    def mwis(cand, present, need):
        """cand 内的最大权独立集；present 为仍有候选的话筒位；need 为阈值。"""
        nonlocal states
        if present == 0:
            return (0, (0, 0)) if (0, 0) >= need else (0, _SENTINEL)

        cached = memo.get(cand)
        if cached is not None:
            return cached if cached[1] >= need else (0, _SENTINEL)

        states += 1
        if states > NODE_LIMIT:
            limit_hit[0] = True
            return 0, _SENTINEL

        # 廉价合法上界：在场高优话筒数 / 在场话筒总数
        ub = (popcount(present & high_mics), popcount(present))
        if ub < need:
            return 0, _SENTINEL
        # 计数界剪不动时，在规模较小的节点用候选点团覆盖收紧
        if ub >= need and need > (0, 0) and popcount(cand) <= 64:
            ub = tight_bound(cand)
            if ub < need:
                return 0, _SENTINEL

        # MRV：候选数最少的在场话筒
        u = -1
        u_key = None
        pm = present
        while pm:
            b = pm & -pm
            t = b.bit_length() - 1
            pm ^= b
            x = var_us[t]
            cnt = popcount(cand & group_mask[x])
            key = (cnt, 0 if mic_w[x][0] else 1, -len(adj[x]), x)
            if u_key is None or key < u_key:
                u_key, u = key, x

        block = cand & group_mask[u]

        # LCV
        cands = []
        m = block
        while m:
            b = m & -m
            p = b.bit_length() - 1
            m ^= b
            cands.append((popcount(conflict[p] & cand), -wgt[p][0], p))
        cands.sort()

        # 选 u 之后，剩余候选 rest；在场集合 = 候选未清空的话筒
        def present_after(rest, present_base, chosen_u):
            out = present_base & ~micbit[chosen_u]
            scan = out
            while scan:
                b = scan & -scan
                t = b.bit_length() - 1
                scan ^= b
                x = var_us[t]
                if not (rest & group_mask[x]):
                    out ^= b
            return out

        best_m = 0
        best_res = (0, 0)
        found = False
        for _h, _hp, p in cands:
            rest = cand & ~(conflict[p] | group_mask[u])
            rest_present = present_after(rest, present, u)
            sub_need = (need[0] - mic_w[u][0], need[1] - mic_w[u][1])
            sub_m, sub_w = mwis(rest, rest_present, sub_need)
            if sub_w is not _SENTINEL:
                take_m = (1 << p) | sub_m
                take_w = (mic_w[u][0] + sub_w[0], mic_w[u][1] + sub_w[1])
                if not found or take_w > best_res:
                    best_m, best_res, found = take_m, take_w, True
                    if take_w >= ub:
                        break
            if limit_hit[0]:
                break

        if not limit_hit[0]:
            skip_cand = cand & ~group_mask[u]
            skip_present = present_after(skip_cand, present, u)
            skip_m, skip_w = mwis(skip_cand, skip_present, need)
            if skip_w is not _SENTINEL and (not found or skip_w > best_res):
                best_m, best_res, found = skip_m, skip_w, True

        if not found:
            return 0, _SENTINEL

        if best_res > best_w[0]:
            best_w[0] = best_res
            best_mask[0] = best_m
        memo[cand] = (best_m, best_res)
        return best_m, best_res

    init_present = 0
    for t, u in enumerate(var_us):
        if available & group_mask[u]:
            init_present |= 1 << t
    sel, selw = mwis(available, init_present, gw)

    if limit_hit[0] or selw is _SENTINEL:
        final_sel = best_mask[0]
        truncated = True
    else:
        final_sel = sel
        truncated = False

    assignment = dict(chosen)
    m = final_sel
    while m:
        b = m & -m
        p = b.bit_length() - 1
        m ^= b
        assignment[cand_mic[p]] = cand_freq[p]
    return assignment, states, truncated
