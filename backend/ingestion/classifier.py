"""Sheet-type classifier.

Goal: given a `SheetSnapshot` (and optionally a screenshot path), decide
which parser should handle it. Returns a `ClassificationResult` with
type + confidence + reasoning trail (the trail is shown in the debug UI
so users / developers can understand why a sheet was routed somewhere).

Strategy stack (cheap → expensive):
    1. Sheet-name regex hints (extremely fast, very high signal for the
       common case of 表紙 / 変更履歴 / 履歴).
    2. Structural rules from the SheetSnapshot:
         - cells very few + few rows + has shapes  -> cover candidate
         - header row + N homogeneous rows         -> table
         - has embedded images                     -> screen
         - lots of indent levels + colors          -> memo
    3. VL fallback: ask the VL model to look at the sheet screenshot
       when confidence is below `vl_fallback_threshold`.

Adding new types later is a matter of appending to `SheetType` and
adding rules; no other module needs to change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from loguru import logger

from config import settings
from ingestion.excel_utils import SheetSnapshot, dump_markdown
from llm.ollama_client import ollama


class SheetType(str, Enum):
    COVER = "cover"
    HISTORY = "history"
    TABLE = "table"
    MEMO = "memo"
    SCREEN = "screen"
    INDEX = "index"        # table-of-contents / screen list
    UNKNOWN = "unknown"


# Per-language name hints. Anything matching → strong signal.
NAME_HINTS: list[tuple[re.Pattern, SheetType]] = [
    (re.compile(r"表紙|表 紙|cover|封面|首頁|首页", re.I),                       SheetType.COVER),
    (re.compile(r"変更履歴|変更 履歴|履歴|履历|変更履历|change[_ ]?log|history|更新履歴", re.I), SheetType.HISTORY),
    (re.compile(r"対象画面|画面一覧|画面リスト|screen[_ ]?list|index|目次|目录", re.I),  SheetType.INDEX),
    (re.compile(r"画面|screen|UI|ui|view|レイアウト", re.I),                       SheetType.SCREEN),
    (re.compile(r"テーブル|table|データ辞書|data[_ ]?dict|カラム|column|レコード", re.I), SheetType.TABLE),
    (re.compile(r"仕様|spec|メモ|memo|備考|note|処理|ルール|rule", re.I),          SheetType.MEMO),
]


@dataclass
class ClassificationResult:
    sheet_type: SheetType
    confidence: float                  # 0.0 ~ 1.0
    reasons: list[str] = field(default_factory=list)
    used_vl: bool = False


# ---------------------------------------------------------------------------
# Structural heuristics
# ---------------------------------------------------------------------------

def _looks_like_cover(snap: SheetSnapshot) -> tuple[bool, list[str]]:
    """Cover sheets usually have very few cells (sometimes 0 because the
    title lives in a shape / text box) and very few rows.
    """
    reasons: list[str] = []
    rows_with_data = len({c.row for c in snap.cells})
    if len(snap.cells) <= 6 and rows_with_data <= 6:
        reasons.append(f"only {len(snap.cells)} cell(s) across {rows_with_data} row(s)")
        return True, reasons
    return False, reasons


def _looks_like_history(snap: SheetSnapshot) -> tuple[bool, list[str]]:
    """Change-history sheets have a small number of rows where each row
    starts with a date-like token in the left-most column.
    """
    rows: dict[int, list] = {}
    for c in snap.cells:
        rows.setdefault(c.row, []).append(c)
    if not rows:
        return False, []

    date_pat = re.compile(r"\d{4}[/-]\d{1,2}([/-]\d{1,2})?")
    date_rows = 0
    for row in rows.values():
        leftmost = min(row, key=lambda x: x.col)
        if date_pat.search(leftmost.text):
            date_rows += 1
    ratio = date_rows / max(len(rows), 1)
    if ratio >= 0.5 and len(rows) <= 50:
        return True, [f"{date_rows}/{len(rows)} rows start with a date"]
    return False, []


def _looks_like_table(snap: SheetSnapshot) -> tuple[bool, list[str], dict]:
    """A regular tabular sheet has:
       - one row near the top with multiple non-empty cells (header)
       - subsequent rows with similar column occupancy
       - low color/format variability
    """
    rows: dict[int, list] = {}
    for c in snap.cells:
        rows.setdefault(c.row, []).append(c)
    sorted_rows = sorted(rows.items())
    if len(sorted_rows) < 4:
        return False, [], {}

    # find the row most likely to be the header (≥3 non-empty cols, often bold or filled)
    header_row = None
    for row_idx, cells in sorted_rows[:8]:
        if len(cells) >= 3 and (any(c.bold for c in cells) or any(c.fill_color for c in cells)):
            header_row = (row_idx, cells)
            break
    if header_row is None and sorted_rows:
        # fallback: first row with ≥3 cells
        for row_idx, cells in sorted_rows:
            if len(cells) >= 3:
                header_row = (row_idx, cells)
                break
    if header_row is None:
        return False, [], {}

    header_idx, header_cells = header_row
    header_cols = {c.col for c in header_cells}

    body_rows = [r for r in sorted_rows if r[0] > header_idx]
    if len(body_rows) < 3:
        return False, [], {}

    # How many body rows share most of the header's columns?
    homogeneous = 0
    for _, cells in body_rows:
        body_cols = {c.col for c in cells}
        overlap = len(body_cols & header_cols)
        if overlap >= max(2, len(header_cols) - 1):
            homogeneous += 1
    ratio = homogeneous / len(body_rows)
    if ratio >= 0.6:
        return True, [
            f"header @ row {header_idx} with {len(header_cells)} cells",
            f"{homogeneous}/{len(body_rows)} body rows share its column shape ({ratio:.0%})",
        ], {"header_row": header_idx, "header_cols": sorted(header_cols)}
    return False, [], {}


def _looks_like_memo(snap: SheetSnapshot) -> tuple[bool, list[str]]:
    """Memo / spec sheets show:
       - multiple indent levels (≥3)
       - color variety (≥2 distinct font colors) OR many rows of prose
    """
    rows_with_data = len({c.row for c in snap.cells})
    levels = {c.indent_level for c in snap.cells}
    distinct_colors = {c.font_color for c in snap.cells if c.font_color}
    reasons: list[str] = []
    score = 0
    if len(levels) >= 3:
        score += 1
        reasons.append(f"{len(levels)} indent levels")
    if len(distinct_colors) >= 2:
        score += 1
        reasons.append(f"{len(distinct_colors)} text colors")
    if rows_with_data >= 15:
        score += 1
        reasons.append(f"{rows_with_data} content rows")
    if score >= 2:
        return True, reasons
    return False, reasons


# ---------------------------------------------------------------------------
# VL fallback
# ---------------------------------------------------------------------------

_VL_PROMPT = """这是 Excel 工作表的截图。请判断它属于以下哪一类，并给出原因。

可选类别：
- cover  : 封面页 (表紙)，含项目名/版本/作者等元数据
- history: 变更履历 (履歴)，逐条变更记录
- table  : 标准表格 (有表头 + 多行同构数据，例如数据字典)
- memo   : 规格说明 / 备忘 (带缩进段落、规则列表，可能有彩色文字标记)
- screen : 画面规格书 (含 UI 截图或带标注的画面布局)
- index  : 索引页 (目录、画面一覧等)
- unknown: 其他

只输出一个 JSON 对象，不要附加任何其他文字，格式：
{"type": "<one of above>", "confidence": 0.0~1.0, "reason": "简短理由"}"""


async def _vl_classify(screenshot_path: Path) -> tuple[SheetType, float, str]:
    raw = await ollama.vision(_VL_PROMPT, [screenshot_path])
    # strip code fences if any
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*", "", txt).rstrip("`").strip()
    import json
    try:
        obj = json.loads(txt)
        t = SheetType(obj.get("type", "unknown"))
        c = float(obj.get("confidence", 0.5))
        r = str(obj.get("reason", ""))
        return t, c, r
    except Exception as e:
        logger.warning(f"VL classifier returned unparseable output: {raw!r} ({e})")
        return SheetType.UNKNOWN, 0.0, f"vl-parse-error: {e}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def classify_sheet(
    snap: SheetSnapshot,
    screenshot_path: Path | None = None,
    vl_fallback_threshold: float = 0.55,
) -> ClassificationResult:
    """Run the rule stack, optionally falling back to VL.

    Caller decides whether to render and pass a screenshot. If
    `screenshot_path` is None, VL fallback is skipped — confidence
    just stays low and the sheet falls through to the GenericFallback
    parser downstream.
    """
    reasons: list[str] = []

    # ---- 1. Name hint ----
    name_type: SheetType | None = None
    for pat, t in NAME_HINTS:
        if pat.search(snap.name):
            name_type = t
            reasons.append(f"name matches /{pat.pattern}/ → {t.value}")
            break

    # ---- 2. Structural rules ----
    # Strong signals (high confidence on hit) ordered by specificity.
    if snap.has_images:
        # Screen sheets nearly always have at least one embedded image.
        reasons.append("has embedded image(s) → screen candidate")
        # Combine with name hint if it agrees
        if name_type == SheetType.SCREEN:
            return ClassificationResult(SheetType.SCREEN, 0.95, reasons)
        # If name says cover/history but has images, name still wins for the
        # rare case (cover sometimes contains a logo image).
        if name_type in (SheetType.COVER, SheetType.HISTORY, SheetType.INDEX):
            return ClassificationResult(name_type, 0.88, reasons)
        return ClassificationResult(SheetType.SCREEN, 0.80, reasons)

    is_cover, r = _looks_like_cover(snap)
    if is_cover:
        reasons += r
        conf = 0.90 if name_type == SheetType.COVER else 0.70
        return ClassificationResult(SheetType.COVER, conf, reasons)

    is_hist, r = _looks_like_history(snap)
    if is_hist:
        reasons += r
        conf = 0.93 if name_type == SheetType.HISTORY else 0.75
        return ClassificationResult(SheetType.HISTORY, conf, reasons)

    is_table, r, _ = _looks_like_table(snap)
    if is_table:
        reasons += r
        conf = 0.92 if name_type == SheetType.TABLE else 0.78
        return ClassificationResult(SheetType.TABLE, conf, reasons)

    is_memo, r = _looks_like_memo(snap)
    if is_memo:
        reasons += r
        conf = 0.85 if name_type == SheetType.MEMO else 0.65
        return ClassificationResult(SheetType.MEMO, conf, reasons)

    # ---- 3. Name-only fallback (when nothing structural fires) ----
    if name_type is not None:
        return ClassificationResult(name_type, 0.55, reasons + ["only name hint matched"])

    # ---- 4. VL fallback ----
    if screenshot_path is not None:
        try:
            t, conf, reason = await _vl_classify(screenshot_path)
            reasons.append(f"VL says {t.value} ({conf:.2f}): {reason}")
            return ClassificationResult(t, conf, reasons, used_vl=True)
        except Exception as e:
            logger.warning(f"VL classification failed: {e}")
            reasons.append(f"vl-error: {e}")

    return ClassificationResult(SheetType.UNKNOWN, 0.0, reasons + ["no rule matched"])
