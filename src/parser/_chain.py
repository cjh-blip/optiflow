"""LINE + ARC 首尾链拼接器（纯 Python，不依赖 shapely）。

设计要点：
1. LINE 取 2 端点；ARC 按弦高离散为若干弦线段。
2. 每条边 2 端点 quantize 到 join_tolerance_mm 网格，q0 < q1 使边"无向"，避免浮点差漏连。
3. endpoint → edges 索引 + DFS 串环；环长度 ≥ 3 且闭合 → 输出 polygon（绘图单位的 mm，未转米）。
4. polygon 去重：重心相近 + 面积相近 → 保留顶点多者。
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple

logger = logging.getLogger(__name__)

Point2 = Tuple[float, float]


# ---------- 基础：ARC 离散 ----------
def arc_to_points(center, radius, start_angle_deg: float, end_angle_deg: float,
                  chord_height_mm: float = 0.5, units: str = "mm") -> List[Point2]:
    """把 ARC 按弦高采样成有序 polyline（不含终点重复添加；调用者相邻段共享端点）。"""
    r = float(radius)
    if r <= 0:
        return []
    # 角度步长：步长角度 α 满足 弦高 ch = r(1 - cos(α/2)) ⇒ α = 2*acos(1 - ch/r)
    ch = max(float(chord_height_mm), 1e-6)
    if r <= ch:
        alpha_rad = math.pi
    else:
        ratio = 1 - ch / r
        ratio = max(-1.0, min(1.0, ratio))
        alpha_rad = 2.0 * math.acos(ratio)
    # 计算总角（带方向，可能 > 2π 但 ARC 一般 ≤2π）
    start = math.radians(start_angle_deg)
    end = math.radians(end_angle_deg)
    # ARC 按 DXF 约定是 start → end（CCW 方向）
    delta = end - start
    # 规范化 delta 到 [-2π, 2π]
    two_pi = 2.0 * math.pi
    if abs(delta) > two_pi:
        delta = math.copysign(two_pi, delta)
    # 若 delta 非常小（接近闭合圆），按整圆处理（一般 ARC 不产生整圆但保险）
    if abs(delta) < 1e-8:
        delta = two_pi

    steps = max(1, int(math.ceil(abs(delta) / alpha_rad)))
    # 保证 n_steps 步正好覆盖 delta
    step_rad = delta / steps
    pts: List[Point2] = []
    cx = float(center[0])
    cy = float(center[1])
    for i in range(steps + 1):
        a = start + i * step_rad
        x = cx + r * math.cos(a)
        y = cy + r * math.sin(a)
        pts.append((x, y))
    return pts


# ---------- quantize 端点避免浮点差 ----------
def _quantize(p: Point2, tol_mm: float) -> Tuple[int, int]:
    t = max(float(tol_mm), 1e-9)
    return (int(round(p[0] / t)), int(round(p[1] / t)))


# ---------- 主接口：从 DXF modelspace 抽取 (polygon_mm, handles_note) 列表 ----------
def extract_rings_from_line_arc(
    doc,
    edge_layers: Set[str],
    join_tolerance_mm: float = 0.5,
    auto_close_tolerance_mm: float = 1.0,
    split_at_t_junctions: bool = True,
) -> List[Tuple[List[Point2], str]]:
    """遍历 LINE + ARC，按端点拼出若干闭合环（绘图单位 mm）。

    返回：[(polygon_mm_points, handles_note_str), ...]
    handles_note 用于去重/ID：拼环时用到的 handles 数量摘要（短 hash）。

    预处理：在 T 形交叉处分割 LINE —— 当一条 LINE 的端点落在另一条 LINE 的
    中间（而非端点）时，在交叉点把后者分成两段。这解决了"贴墙桌子"问题：
    桌子垂直边的端点落在墙线上，需要把墙线在交叉处断开才能拼成闭合环。

    `split_at_t_junctions=False` 关掉上面这步（十字交叉分割仍保留），得到「墙线环」：
    一条完整墙线不再被贴墙家具的端点切碎，环搜索无法在共享端点处拐进家具轮廓，
    于是拼出的房间环是真实墙围轮廓。代价是靠 T 形接头分隔的子房间拼不出来，
    所以这只是**额外一轮候选**（见 src/parser/dxf.py::extract_rooms 的墙线环 pass），
    不替代默认 pass。
    """
    # ---- 阶段 1：收集原始 LINE 和 ARC 段 ----
    raw_lines: List[Tuple[Point2, Point2, str]] = []  # (p0, p1, handle)
    arc_segs: List[Tuple[Point2, Point2, str]] = []
    for e in doc.modelspace():
        dtype = e.dxftype()
        layer = (e.dxf.layer or "").upper()
        if layer not in edge_layers:
            continue
        h = getattr(e.dxf, "handle", "") or ""
        if dtype == "LINE":
            try:
                s = e.dxf.start
                en = e.dxf.end
                raw_lines.append(((s.x, s.y), (en.x, en.y), h))
            except Exception:
                continue
        elif dtype == "ARC":
            try:
                pts = arc_to_points(e.dxf.center, e.dxf.radius,
                                    e.dxf.start_angle, e.dxf.end_angle,
                                    chord_height_mm=max(join_tolerance_mm * 0.5, 0.1))
                for i in range(len(pts) - 1):
                    arc_segs.append((pts[i], pts[i + 1], h))
            except Exception:
                continue

    # ---- 阶段 2：T 形交叉分割 LINE ----
    tol = max(float(join_tolerance_mm), 1e-9)
    # 收集所有 LINE 端点（用于检测 T 形交叉）
    all_endpoints: List[Point2] = []
    for p0, p1, _ in raw_lines:
        all_endpoints.append(p0)
        all_endpoints.append(p1)

    split_segs: List[Tuple[Point2, Point2, str]] = []
    for p0, p1, h in raw_lines:
        # 找落在这条 LINE 上的其他端点
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        seg_len_sq = dx * dx + dy * dy
        seg_len = math.sqrt(seg_len_sq)
        if seg_len_sq < 1e-18:
            continue
        if not split_at_t_junctions:
            split_segs.append((p0, p1, h))
            continue
        # 参数 t 沿 p0→p1 方向
        split_ts: List[float] = []
        for ep in all_endpoints:
            # 点到线段的参数 t
            t = ((ep[0] - p0[0]) * dx + (ep[1] - p0[1]) * dy) / seg_len_sq
            # 用距离（mm）比较，而非参数 t（t∈[0,1] 与 tol 混用是 bug）
            if t * seg_len <= tol or (1.0 - t) * seg_len <= tol:
                continue  # 端点附近不分割
            # 点到线段的距离
            px = p0[0] + t * dx
            py = p0[1] + t * dy
            dist = math.hypot(ep[0] - px, ep[1] - py)
            if dist <= tol:
                split_ts.append(t)
        if not split_ts:
            split_segs.append((p0, p1, h))
        else:
            split_ts.sort()
            # 去重（tol 内的 t 合并：参数差 × seg_len = 距离差）
            unique_ts: List[float] = []
            for t in split_ts:
                if not unique_ts or (t - unique_ts[-1]) * seg_len > tol:
                    unique_ts.append(t)
            # 分割
            prev_p = p0
            for t in unique_ts:
                cur_p = (p0[0] + t * dx, p0[1] + t * dy)
                split_segs.append((prev_p, cur_p, h))
                prev_p = cur_p
            split_segs.append((prev_p, p1, h))

    # ---- 阶段 2b：十字交叉分割（两条 LINE 在中间交叉，非端点）----
    # 解决水平/垂直分隔线在中间交叉但交叉点不是任何LINE端点的问题
    def _seg_cross(a0, a1, b0, b1):
        """计算线段(a0,a1)与(b0,b1)的交叉参数(t1,t2)，均在(0,1)中间才返回交叉点。"""
        dax = a1[0] - a0[0]
        day = a1[1] - a0[1]
        dbx = b1[0] - b0[0]
        dby = b1[1] - b0[1]
        denom = dax * dby - dbx * day
        if abs(denom) < 1e-12:
            return None  # 平行
        t1 = ((b0[0] - a0[0]) * dby - (b0[1] - a0[1]) * dbx) / denom
        t2 = ((b0[0] - a0[0]) * day - (b0[1] - a0[1]) * dax) / denom
        tol_t = tol / max(math.hypot(dax, day), math.hypot(dbx, dby), 1e-9)
        if tol_t < t1 < 1.0 - tol_t and tol_t < t2 < 1.0 - tol_t:
            cx = a0[0] + t1 * dax
            cy = a0[1] + t1 * day
            return (cx, cy, t1, t2)
        return None

    # 收集所有需要分割的交叉点：{seg_index: [(cross_point, t_on_seg), ...]}
    seg_splits: Dict[int, List[Tuple[Point2, float]]] = {}
    for i in range(len(split_segs)):
        ai0, ai1, hi = split_segs[i]
        # bbox 过滤
        axmin = min(ai0[0], ai1[0])
        axmax = max(ai0[0], ai1[0])
        aymin = min(ai0[1], ai1[1])
        aymax = max(ai0[1], ai1[1])
        for j in range(i + 1, len(split_segs)):
            bj0, bj1, hj = split_segs[j]
            bxmin = min(bj0[0], bj1[0])
            bxmax = max(bj0[0], bj1[0])
            bymin = min(bj0[1], bj1[1])
            bymax = max(bj0[1], bj1[1])
            if axmax < bxmin - tol or bxmax < axmin - tol:
                continue
            if aymax < bymin - tol or bymax < aymin - tol:
                continue
            cross = _seg_cross(ai0, ai1, bj0, bj1)
            if cross is None:
                continue
            cx, cy, t1, t2 = cross
            seg_splits.setdefault(i, []).append(((cx, cy), t1))
            seg_splits.setdefault(j, []).append(((cx, cy), t2))

    if seg_splits:
        new_split_segs: List[Tuple[Point2, Point2, str]] = []
        for i, (p0, p1, h) in enumerate(split_segs):
            if i not in seg_splits:
                new_split_segs.append((p0, p1, h))
                continue
            dx = p1[0] - p0[0]
            dy = p1[1] - p0[1]
            seg_len = math.hypot(dx, dy)
            ts = sorted(set(t for _, t in seg_splits[i]))
            prev_p = p0
            for t in ts:
                cur_p = (p0[0] + t * dx, p0[1] + t * dy)
                new_split_segs.append((prev_p, cur_p, h))
                prev_p = cur_p
            new_split_segs.append((prev_p, p1, h))
        split_segs = new_split_segs

    # ---- 阶段 3：构建 edges（q0/q1 相同的边去重，避免 2 边退化环）----
    edges: List[Dict[str, Any]] = []
    seen_edge_keys: Set[Tuple[Tuple[int, int], Tuple[int, int]]] = set()
    all_segs = split_segs + arc_segs
    for (p0, p1, handle) in all_segs:
        if abs(p0[0] - p1[0]) < 1e-9 and abs(p0[1] - p1[1]) < 1e-9:
            continue  # 零长度边丢弃
        q0 = _quantize(p0, join_tolerance_mm)
        q1 = _quantize(p1, join_tolerance_mm)
        if q0 == q1:
            continue  # quantize 后重合，丢弃
        if q0 > q1:
            q0, q1 = q1, q0
            p0, p1 = p1, p0
        edge_key = (q0, q1)
        if edge_key in seen_edge_keys:
            continue  # 已有相同 q0/q1 的边，跳过（避免退化 2 边环）
        seen_edge_keys.add(edge_key)
        edges.append({
            "q0": q0, "q1": q1, "p0": p0, "p1": p1,
            "handle": handle,
        })

    if not edges:
        return []

    # endpoint (quantized) → edge 索引列表（用于找邻居）
    index: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for i, edge in enumerate(edges):
        index[edge["q0"]].append(i)
        index[edge["q1"]].append(i)

    rings: List[Tuple[List[Point2], str]] = []
    seen_ring_hashes: Set[str] = set()  # 按边 idx 集合去重

    def _find_ring(start_idx: int, max_depth: int = 500) -> List[int] | None:
        """从 start_idx 出发，DFS + 回溯找一条闭合环。
        边可以跨环共享（墙线既是房间外墙，也是贴墙桌子的边）。
        同一个环内不能重复用同一条边。"""
        e0 = edges[start_idx]
        for (start_q, target_q) in [(e0["q1"], e0["q0"]), (e0["q0"], e0["q1"])]:
            path = [start_idx]
            local = {start_idx}

            def dfs(cur_q):
                if len(path) > max_depth:
                    return False
                neighbors = index.get(cur_q, [])
                # 第一轮：优先尝试能直接闭合的边（找最小环）
                for ei in neighbors:
                    if ei in local:
                        continue
                    ne = edges[ei]
                    next_q = ne["q1"] if ne["q0"] == cur_q else ne["q0"]
                    if next_q == target_q and len(path) >= 2:
                        path.append(ei)
                        return True
                # 第二轮：递归探索不能直接闭合的边
                # 按到 target_q 的距离排序，优先走更近的边（倾向于找小环）
                candidates = []
                for ei in neighbors:
                    if ei in local:
                        continue
                    ne = edges[ei]
                    next_q = ne["q1"] if ne["q0"] == cur_q else ne["q0"]
                    if next_q == target_q:
                        continue  # 已在第一轮处理
                    dist = abs(next_q[0] - target_q[0]) + abs(next_q[1] - target_q[1])
                    candidates.append((dist, ei))
                candidates.sort(key=lambda x: x[0])
                for _, ei in candidates:
                    ne = edges[ei]
                    next_q = ne["q1"] if ne["q0"] == cur_q else ne["q0"]
                    local.add(ei)
                    path.append(ei)
                    if dfs(next_q):
                        return True
                    path.pop()
                    local.discard(ei)
                return False

            if dfs(start_q):
                return list(path)
        return None

    for start_idx in range(len(edges)):
        ring_path = _find_ring(start_idx)
        if ring_path is None:
            continue
        # 按边 idx 集合去重（同一组边正反方向算同一个环）
        ring_key = "|".join(str(i) for i in sorted(ring_path))
        if ring_key in seen_ring_hashes:
            continue
        seen_ring_hashes.add(ring_key)

        # 组装 polygon 顶点序列：按 ring_path 顺序，每条边给出 1 个顶点
        path_edges = ring_path
        first = edges[path_edges[0]]
        # 若路径长度 >= 2：决定首边方向（使 first 的"终点"能接第二条边的"起点"之一）
        if len(path_edges) >= 2:
            e2 = edges[path_edges[1]]
            qp0 = first["q0"]
            qp1 = first["q1"]
            if e2["q0"] == qp1 or e2["q1"] == qp1:
                oriented = (first["p0"], first["p1"])
            elif e2["q0"] == qp0 or e2["q1"] == qp0:
                oriented = (first["p1"], first["p0"])
            else:
                oriented = (first["p0"], first["p1"])
            poly = [oriented[0], oriented[1]]
            last_q = first["q1"] if oriented == (first["p0"], first["p1"]) else first["q0"]
            for ei in path_edges[1:]:
                e = edges[ei]
                if e["q0"] == last_q:
                    next_p = e["p1"]
                    last_q = e["q1"]
                else:
                    next_p = e["p0"]
                    last_q = e["q0"]
                poly.append(next_p)
        else:
            poly = [first["p0"], first["p1"]]

        # 闭合判定：DFS 找到的环已经闭合（target_q == start_q），但 poly 首末点可能因
        # quantize 精度有微小 gap → auto-close 补首点
        gap_raw = math.hypot(poly[-1][0] - poly[0][0], poly[-1][1] - poly[0][1])
        if gap_raw > max(auto_close_tolerance_mm, join_tolerance_mm * 2):
            # 退化：DFS 闭合但顶点组装后 gap 过大（理论上不应发生，保险）
            logger.debug("DFS 闭合但顶点 gap=%.2f mm 过大，丢弃", gap_raw)
            continue
        if gap_raw > 1e-9:
            poly.append(poly[0])  # auto-close 补首点
        # 顶点数（不含重复首末）>= 3 → 合法环
        effective = len(poly) - 1 if poly[0] == poly[-1] else len(poly)
        if effective < 3:
            continue
        rings.append((poly, _handles_note(path_edges, edges)))
    return rings


def _handles_note(path_edges, edges) -> str:
    """用 handles 的数量 + 首末 handle 各 2 字符做短摘要，避免 ID 太长。"""
    handles = [edges[i]["handle"] for i in path_edges if edges[i]["handle"]]
    total = len(path_edges)
    if not handles:
        return f"E{total}"
    head = "".join(c for c in handles[0][:2] if c.isalnum()).zfill(2) or "xx"
    tail = "".join(c for c in handles[-1][-2:] if c.isalnum()).zfill(2) or "xx"
    return f"E{total}_{head}{tail}"


# ---------- polygon 工具 ----------
#: 「锯齿边」阈值（米）：房间环上短于此长度的边视为家具轮廓混入的锯齿。
#: 与任务书验收标准「顶点最小边长 > 0.2m」一致。
SAWTOOTH_EDGE_M = 0.2


def count_short_edges(poly, max_len: float = SAWTOOTH_EDGE_M) -> int:
    """环上长度 < max_len 的边数（忽略闭合重复点与零长边）。

    墙线是整段长边，贴墙家具的凸台/凹槽会在墙上切出一串几厘米的短边，
    所以这个计数就是「这个环混进了多少家具轮廓」的度量。
    """
    ring = list(poly)
    if len(ring) > 2 and abs(ring[0][0] - ring[-1][0]) < 1e-9 and abs(ring[0][1] - ring[-1][1]) < 1e-9:
        ring.pop()
    if len(ring) < 3:
        return 0
    n = 0
    for i in range(len(ring)):
        x1, y1 = ring[i][:2]
        x2, y2 = ring[(i + 1) % len(ring)][:2]
        d = math.hypot(x2 - x1, y2 - y1)
        if 1e-9 < d < max_len:
            n += 1
    return n


def ring_preference_key(poly) -> Tuple[int, int]:
    """重复房间候选的择优键（越小越优先）：先比锯齿边少，再比顶点多。

    同一组 LINE 能拼出多个面积相近的环（环搜索在共享端点处可以拐进家具轮廓，
    也可以沿墙直走）。锯齿边少的那个才是真实墙围轮廓；顶点数只在锯齿数相同时
    用来保留信息更全的环（原 dedup_rooms 的行为）。
    """
    ring = list(poly)
    if len(ring) > 2 and abs(ring[0][0] - ring[-1][0]) < 1e-9 and abs(ring[0][1] - ring[-1][1]) < 1e-9:
        ring.pop()
    return (count_short_edges(poly), -len(ring))


# ---------- 房间环后处理：共线合并 + 小凹槽/凸台剔除 ----------
#: 共线判定容差（米）：顶点到「前后顶点连线」的垂距 ≤ 此值 → 该顶点是墙上的多余断点
COLLINEAR_TOL_M = 1e-4
#: 凹槽/凸台面积上限（m²）：小于此面积的绕行视为贴墙家具轮廓，剔除。
#: 与 src/validator 规则 2 的 MIN_SANE_AREA（0.5 m²）一致 —— 比一个「最小合理空间」还小的
#: 绕行不可能是房间自身的形状特征。
MAX_NOTCH_AREA_M2 = 0.5
#: 单个凹槽最多允许几个中间顶点（防止把整面墙当成一个绕行吃掉）
MAX_NOTCH_VERTICES = 8


def _split_ring(poly) -> Tuple[List[List[float]], bool]:
    """拆成（开放顶点列表, 原来是否闭合）。顶点统一成 [x, y] 的 list（JSON 友好）。"""
    ring = [[float(p[0]), float(p[1])] for p in poly]
    closed = (len(ring) > 2
              and abs(ring[0][0] - ring[-1][0]) < 1e-9
              and abs(ring[0][1] - ring[-1][1]) < 1e-9)
    if closed:
        ring.pop()
    return ring, closed


def _join_ring(ring: List[List[float]], closed: bool) -> List[List[float]]:
    out = [list(p) for p in ring]
    if closed and out:
        out.append(list(out[0]))
    return out


def _perp_dist(p, a, b) -> float:
    """点 p 到直线 ab 的垂距；ab 退化时返回 p 到 a 的距离。"""
    abx = b[0] - a[0]
    aby = b[1] - a[1]
    den = math.hypot(abx, aby)
    if den < 1e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs(abx * (p[1] - a[1]) - aby * (p[0] - a[0])) / den


def simplify_collinear(poly, tol_m: float = COLLINEAR_TOL_M):
    """删掉落在「前后两顶点连线」上的中间顶点（含零长边）。

    一面墙被 T 形/十字分割切成 N 段后，环上会留下 N-1 个共线断点；它们在 DIALux 里
    就是多余墙段。只删「严格落在线段内部」的点：伸出线段之外的尖刺（spike）保留，
    因为删掉会改变面积。
    """
    ring, closed = _split_ring(poly)
    if len(ring) < 4:
        return _join_ring(ring, closed)
    tol = max(float(tol_m), 0.0)
    changed = True
    while changed and len(ring) > 3:
        changed = False
        i = 0
        while i < len(ring) and len(ring) > 3:
            prev = ring[i - 1]
            cur = ring[i]
            nxt = ring[(i + 1) % len(ring)]
            # 零长边：cur 与 prev 重合 → 删 cur
            if math.hypot(cur[0] - prev[0], cur[1] - prev[1]) <= tol:
                del ring[i]
                changed = True
                continue
            seg = math.hypot(nxt[0] - prev[0], nxt[1] - prev[1])
            if seg <= tol:
                i += 1  # prev/nxt 重合 → cur 是尖刺，保留
                continue
            if _perp_dist(cur, prev, nxt) > tol:
                i += 1
                continue
            # 共线：只有落在 prev→nxt 线段内部才可删（否则是改变面积的尖刺）
            t = ((cur[0] - prev[0]) * (nxt[0] - prev[0])
                 + (cur[1] - prev[1]) * (nxt[1] - prev[1])) / (seg * seg)
            if -1e-9 <= t <= 1.0 + 1e-9:
                del ring[i]
                changed = True
            else:
                i += 1
    return _join_ring(ring, closed)


def _signed_area(ring) -> float:
    s = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i][:2]
        x2, y2 = ring[(i + 1) % len(ring)][:2]
        s += x1 * y2 - x2 * y1
    return s * 0.5


def remove_small_notches(poly, max_area_m2: float = MAX_NOTCH_AREA_M2,
                         max_vertices: int = MAX_NOTCH_VERTICES,
                         tol_m: float = 1e-3):
    """填平「从一条直墙拐进房间内部、又回到同一条直线」的小面积凹槽（贴墙家具轮廓）。

    判据（全部满足才剔除）：
    1. before → vi … vj → after 沿同一方向落在同一条直线上 —— 墙在此处本来是直的；
    2. 中间顶点里至少有一个偏离这条墙线 —— 确实绕出去了（否则只是共线断点，交给
       `simplify_collinear`）；
    3. 绕行围出的面积 < max_area_m2；
    4. **填平后房间面积变大** —— 只吃「往房间里凹」的绕行。柜子画在房间内，
       轮廓混进房间环必然表现为凹槽；若某段绕行填平后面积反而变小，说明被当成
       墙的那条线才是家具边，此时删掉它等于把家具认成墙，绝不能做；
    5. 中间顶点数 ≤ max_vertices，且剔除后环仍有 ≥ 3 个顶点。

    这样能吃掉柜子在墙上切出的凹槽，同时保住房间自身的转角台阶
    （台阶两端落在**不同**墙线上，判据 1 不成立）与大于阈值的真实壁龛。
    返回 (新环, 被剔除的绕行列表)；绕行记录形如 {"area": .., "vertices": [...]}。
    """
    ring, closed = _split_ring(poly)
    removed: List[Dict[str, Any]] = []
    if len(ring) < 5 or max_area_m2 <= 0:
        return _join_ring(ring, closed), removed
    tol = max(float(tol_m), 0.0)

    def _straight_wall(before, vi, vj, after) -> bool:
        """before→vi 定义墙方向；要求 vj、after 依次沿该方向落在同一条直线上。"""
        dx = vi[0] - before[0]
        dy = vi[1] - before[1]
        d = math.hypot(dx, dy)
        if d < 1e-12:
            return False
        ux, uy = dx / d, dy / d

        def forward_on_line(p, origin) -> bool:
            px = p[0] - origin[0]
            py = p[1] - origin[1]
            along = px * ux + py * uy
            perp = abs(px * uy - py * ux)
            return along > tol and perp <= tol

        return forward_on_line(vj, vi) and forward_on_line(after, vj)

    progress = True
    while progress and len(ring) >= 5:
        progress = False
        n = len(ring)
        ring_signed = _signed_area(ring)
        if abs(ring_signed) < 1e-12:
            break
        best = None  # (area, i, k)
        for i in range(n):
            before = ring[(i - 1) % n]
            vi = ring[i]
            for k in range(2, min(max_vertices + 1, n - 2) + 1):
                j = (i + k) % n
                vj = ring[j]
                after = ring[(j + 1) % n]
                if not _straight_wall(before, vi, vj, after):
                    continue
                # 判据 2：中间至少一个顶点离开墙线
                mids = [ring[(i + t) % n] for t in range(1, k)]
                if all(_perp_dist(m, vi, vj) <= tol for m in mids):
                    continue
                # 判据 3/4：绕行面积够小，且填平后房间变大
                # A_new = A_old - detour_signed ⇒ 面积变大 ⟺ detour 与环反向
                detour_signed = _signed_area([vi] + mids + [vj])
                if abs(detour_signed) >= max_area_m2:
                    continue
                if detour_signed * ring_signed >= 0:
                    continue
                if best is None or abs(detour_signed) < best[0]:
                    best = (abs(detour_signed), i, k)
        if best is None:
            break
        area, i, k = best
        n = len(ring)
        mids_idx = [(i + t) % n for t in range(1, k)]
        if n - len(mids_idx) < 3:
            break
        removed.append({
            "area": round(area, 4),
            "vertices": [list(ring[t]) for t in mids_idx],
        })
        for t in sorted(mids_idx, reverse=True):
            del ring[t]
        progress = True
    return _join_ring(ring, closed), removed


def smooth_room_ring(poly, max_notch_area_m2: float = MAX_NOTCH_AREA_M2,
                     collinear_tol_m: float = COLLINEAR_TOL_M):
    """房间环后处理：剔除小凹槽/凸台 → 合并共线断点。返回 (新环, 剔除记录)。

    只对**房间**环用（家具环必须原样保留：家具恰恰就是那些凹槽）。
    """
    ring, removed = remove_small_notches(poly, max_area_m2=max_notch_area_m2)
    ring = simplify_collinear(ring, tol_m=collinear_tol_m)
    return ring, removed


def polygon_area(poly) -> float:
    """鞋带公式（绝对面积）。poly: [(x,y)...]；米制输出 m²。"""
    n = len(poly)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = poly[i][:2]
        x2, y2 = poly[(i + 1) % n][:2]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def polygon_centroid(poly) -> Point2:
    n = len(poly)
    if n == 0:
        return (0.0, 0.0)
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (sum(xs) / n, sum(ys) / n)


# ---------- rooms 去重（按重心 + 面积） ----------
def dedup_rooms(rooms, rel_tol_centroid: float = 1e-3, rel_tol_area: float = 5e-2,
                prefer_layers: Set[str] | None = None):
    """若两 polygon 重心相近（相对"外接盒"尺度 rel_tol_centroid）且面积差 < rel_tol_area：合并。

    择优顺序（`ring_preference_key`）：
    1. `prefer_layers` 命中的环优先（墙线环 pass 的产物，见 dxf.WALL_RING_LAYER）；
    2. 锯齿边（< 0.2m）少者优先 —— 家具轮廓混进房间环就表现为一串短边；
    3. 顶点多者优先（信息更全，原行为）。
    """
    if len(rooms) <= 1:
        return rooms
    prefer = {layer.upper() for layer in (prefer_layers or set())}
    # 计算每个 room 的几何属性
    info = []
    for r in rooms:
        area = polygon_area(r.polygon)
        cx, cy = polygon_centroid(r.polygon)
        saw, neg_nv = ring_preference_key(r.polygon)
        info.append({
            "room": r, "area": area, "cx": cx, "cy": cy,
            # 越小越优先：先看是否墙线环，再看锯齿数，最后看顶点数（取负 → 多者优先）
            "key": (0 if (r.layer or "").upper() in prefer else 1, saw, neg_nv),
        })
    keep: List[bool] = [True] * len(rooms)
    for i in range(len(info)):
        if not keep[i]:
            continue
        a = info[i]
        for j in range(i + 1, len(info)):
            if not keep[j]:
                continue
            b = info[j]
            # 面积 0（退化）：优先丢弃
            if a["area"] < 1e-9 and b["area"] < 1e-9:
                continue
            area_max = max(a["area"], b["area"])
            if area_max < 1e-9:
                continue
            if abs(a["area"] - b["area"]) / area_max > rel_tol_area:
                continue
            # 重心距离 vs 几何尺度：用 sqrt(area) 作特征尺度
            scale = max(math.sqrt(area_max), 1e-3)
            dcen = math.hypot(a["cx"] - b["cx"], a["cy"] - b["cy"])
            if dcen / scale > rel_tol_centroid:
                continue
            # 重复：保留 key 更小者（墙线环 > 锯齿少 > 顶点多）
            if a["key"] <= b["key"]:
                keep[j] = False
            else:
                keep[i] = False
                break  # i 不再保留，跳出内层
    return [info[i]["room"] for i in range(len(info)) if keep[i]]
