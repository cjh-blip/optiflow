"""灯具库：统一记录与候选筛选（Task 1）。

数据源是 ieslibrary 清单的生成物（38 条本地 IES 索引），字段契约见
`lighting_design/灯具库索引_OPPLE_NVC_20261028.md`。

索引本身**不含光通量**（只有 file/luminaire/lamp/lumcat/manufacturer/hash/download_url/source），
所以 `flux_lm` 允许为 None：拿不到就不编，按功率排序并把 `gap` 标成 None，与判据表
「未取证不得进入结论」同一条纪律。

筛选口径：色温、功率上限是**硬筛**（给了就只留满足的）；光通量差距只做排序，不做门槛。
硬筛后为空时回退到全库按最接近给候选，并给每项标 `gap` 与 `note`——用户看得到「差在哪」，
而不是一个空白列表。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 型号名里的功率：必须带 W 单位。"L1563" 这类编号没有 W，不会被当成功率。
_POWER_RE = re.compile(r"(\d+(?:\.\d+)?)\s*W\b", re.IGNORECASE)
#: 型号码里的色温：四位、首位 2-9，覆盖 2500–9999K。"L1222"/"L1563" 首位是 1，天然排除。
_CCT_RE = re.compile(r"\b(2[5-9]\d{2}|[3-9]\d{3})\b")


@dataclass(frozen=True)
class LuminaireRecord:
    """一款灯具的库内记录。`flux_lm` 允许缺（索引不提供）。"""

    id: str
    file: str
    manufacturer: str
    lumcat: str
    power_w: Optional[float]
    cct_k: Optional[int]
    flux_lm: Optional[float]
    download_url: str
    source: str


def parse_attributes(luminaire: str) -> Tuple[Optional[float], Optional[int]]:
    """从型号名解析 (单灯功率 W, 色温 K)；解析不出给 None。

    功率取**最后一个**带 W 的数（型号前缀里的 L1563、L1222 是编号不是功率）；
    色温取**最后一个**四位色温值（型号通常以色温结尾，如 `...-75W-4000`）。
    """
    text = str(luminaire or "")
    powers = _POWER_RE.findall(text)
    power: Optional[float] = float(powers[-1]) if powers else None
    ccts = _CCT_RE.findall(text)
    cct: Optional[int] = int(ccts[-1]) if ccts else None
    return power, cct


def load_index(path: str | Path) -> List[LuminaireRecord]:
    """读灯具库索引 JSON（list）。缺字段按缺省处理，不臆造数值。"""
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"灯具库索引应为列表，实际是 {type(raw).__name__}：{p}")
    out: List[LuminaireRecord] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("luminaire") or "")
        power, cct = parse_attributes(name)
        flux = item.get("flux_lm")
        out.append(
            LuminaireRecord(
                id=str(item.get("hash") or item.get("file") or name),
                file=str(item.get("file") or ""),
                manufacturer=str(item.get("manufacturer") or ""),
                lumcat=str(item.get("lumcat") or ""),
                power_w=float(power) if power is not None else None,
                cct_k=cct,
                flux_lm=float(flux) if flux is not None else None,
                download_url=str(item.get("download_url") or ""),
                source=str(item.get("source") or ""),
            )
        )
    return out


def _row(rec: LuminaireRecord, gap: Optional[float],
         note: Optional[str] = None) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": rec.id,
        "file": rec.file,
        "manufacturer": rec.manufacturer,
        "lumcat": rec.lumcat,
        "power_w": rec.power_w,
        "cct_k": rec.cct_k,
        "flux_lm": rec.flux_lm,
        "download_url": rec.download_url,
        "source": rec.source,
        "gap": gap,
    }
    if note:
        row["note"] = note
    return row


def _rank(records: Sequence[LuminaireRecord],
          target_flux: float) -> List[Tuple[LuminaireRecord, Optional[float]]]:
    """按与目标流明的距离升序；没有流明值的排最后（按功率升序，功率也缺则更后）。"""
    have = [r for r in records if r.flux_lm is not None]
    miss = [r for r in records if r.flux_lm is None]
    have.sort(key=lambda r: abs(float(r.flux_lm) - target_flux))
    miss.sort(key=lambda r: (r.power_w is None, r.power_w or 0.0))
    ranked: List[Tuple[LuminaireRecord, Optional[float]]] = [
        (r, round(abs(float(r.flux_lm) - target_flux), 1)) for r in have
    ]
    ranked.extend((r, None) for r in miss)
    return ranked


def select_candidates(
    records: Sequence[LuminaireRecord],
    *,
    per_lamp_flux_lm: float,
    cct_k: Optional[int] = None,
    max_power_w: Optional[float] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """按「单灯目标光通量」挑候选。

    色温与功率上限是硬筛；光通量只排序不设门槛。硬筛后为空 → 回退全库最接近，
    并给每项标 `note` 说明「为什么这些是勉强凑上来的」（Review Focus ①）。
    """
    target = float(per_lamp_flux_lm)
    if target <= 0:
        raise ValueError(f"单灯目标光通量必须为正（收到 {per_lamp_flux_lm!r}）")
    if limit <= 0:
        raise ValueError(f"候选数量上限必须为正（收到 {limit!r}）")
    if not records:
        raise ValueError("灯具库为空：请先导入灯具库索引再选灯")

    pool = list(records)
    if cct_k is not None:
        pool = [r for r in pool if r.cct_k == cct_k]
    if max_power_w is not None:
        cap = float(max_power_w)
        pool = [r for r in pool if r.power_w is None or r.power_w <= cap]

    if pool:
        return [_row(r, gap) for r, gap in _rank(pool, target)[:limit]]

    # 硬筛后一个不剩：给出全库最接近的，并说明筛选条件把候选筛空了
    why = []
    if cct_k is not None:
        why.append(f"色温 {cct_k}K")
    if max_power_w is not None:
        why.append(f"功率≤{max_power_w}W")
    note = ("满足" + "、".join(why) + " 的灯具一款都没有；以下是全库最接近的候选")
    return [_row(r, gap, note) for r, gap in _rank(list(records), target)[:limit]]
