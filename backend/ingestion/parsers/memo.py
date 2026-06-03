"""Memo / spec-prose parser.

These sheets use Excel as a notepad: indented paragraphs, mixed
languages, and font colors that encode "which JIRA / customization
this rule belongs to". The cover parser (if it ran first) provides a
`color_to_jira` map; we attach the resulting tags as chunk metadata.

Strategy:
    - Build a list of (row_idx, indent, line_text, run_colors, is_header)
      where `is_header` heuristically marks structural titles
      (top-of-section lines often have indent <= 1 and bold/large font,
      or are followed by deeper-indent content).
    - Split into "sections": a header line + all deeper-indent lines
      until the next header at <= same indent.
    - For long sections, further split when text exceeds ~800 chars.
    - Each section is one chunk with its full hierarchy_path.
"""

from __future__ import annotations

from dataclasses import dataclass

from ingestion.excel_utils import CellInfo, SheetSnapshot
from ingestion.parsers.base import (
    BaseSheetParser,
    ParsedChunk,
    ParserContext,
    ParserOutput,
)


@dataclass
class _Line:
    row: int
    indent: int
    text: str
    colors: list[str]
    bold: bool
    is_header: bool = False


def _build_lines(snap: SheetSnapshot) -> list[_Line]:
    """One _Line per spreadsheet row; columns on the same row are joined."""
    rows: dict[int, list[CellInfo]] = {}
    for c in snap.cells:
        rows.setdefault(c.row, []).append(c)

    out: list[_Line] = []
    for row_idx in sorted(rows):
        cells = sorted(rows[row_idx], key=lambda x: x.col)
        text = " ".join(c.text for c in cells).strip()
        if not text:
            continue
        colors = sorted({col for c in cells for col in ([c.font_color] + [r.color for r in c.runs]) if col})
        indent = min(c.indent_level for c in cells)
        bold = any(c.bold for c in cells)
        out.append(_Line(row=row_idx, indent=indent, text=text, colors=colors, bold=bold))
    return out


def _mark_headers(lines: list[_Line]) -> None:
    """Mark lines that look like section headers.

    A line is a header when at least one of:
        - it is at the minimum indent and bold
        - the next non-empty line has strictly deeper indent
        - it is at indent 0 or 1 and short (< 40 chars)
    """
    if not lines:
        return
    min_indent = min(l.indent for l in lines)
    for i, l in enumerate(lines):
        deeper_next = False
        for nxt in lines[i + 1:]:
            if nxt.indent > l.indent:
                deeper_next = True
            break
        if l.indent <= min_indent + 1 and (l.bold or len(l.text) < 40) and deeper_next:
            l.is_header = True
        elif l.indent == min_indent and l.bold:
            l.is_header = True


def _build_sections(lines: list[_Line]) -> list[tuple[list[_Line], list[_Line]]]:
    """Return list of (header_chain, body_lines).

    header_chain is the list of ancestor headers at the time the section starts
    (used to build the hierarchy_path). body_lines are the content lines.
    A section ends at the next header with indent <= its own.
    """
    sections: list[tuple[list[_Line], list[_Line]]] = []
    stack: list[_Line] = []
    body: list[_Line] = []
    current_header: _Line | None = None

    def flush():
        nonlocal body, current_header
        if current_header is not None or body:
            sections.append((list(stack), body))
        body = []

    for l in lines:
        if l.is_header:
            # Close previous section
            flush()
            # Adjust stack
            while stack and stack[-1].indent >= l.indent:
                stack.pop()
            stack.append(l)
            current_header = l
        else:
            body.append(l)
    flush()
    return sections


_CHUNK_CHAR_LIMIT = 1200


def _split_long(text_lines: list[_Line], header_chain: list[_Line]) -> list[list[_Line]]:
    """If a section's body is too long, split into sub-chunks at indent boundaries."""
    if sum(len(l.text) for l in text_lines) <= _CHUNK_CHAR_LIMIT:
        return [text_lines]
    out: list[list[_Line]] = []
    cur: list[_Line] = []
    cur_len = 0
    for l in text_lines:
        if cur and cur_len + len(l.text) > _CHUNK_CHAR_LIMIT and l.indent <= (cur[-1].indent if cur else 0):
            out.append(cur)
            cur = []
            cur_len = 0
        cur.append(l)
        cur_len += len(l.text)
    if cur:
        out.append(cur)
    return out


def _render_markdown(header_chain: list[_Line], body: list[_Line]) -> str:
    lines: list[str] = []
    for i, h in enumerate(header_chain):
        prefix = "#" * min(i + 2, 6)
        lines.append(f"{prefix} {h.text}")
    base_indent = min((l.indent for l in body), default=0) if body else 0
    for l in body:
        indent = "  " * max(0, l.indent - base_indent)
        lines.append(f"{indent}- {l.text}")
    return "\n".join(lines)


def _collect_colors(body: list[_Line]) -> list[str]:
    s: set[str] = set()
    for l in body:
        s.update(l.colors)
    return sorted(s)


class MemoParser(BaseSheetParser):
    name = "memo"

    async def parse(self, ctx: ParserContext) -> ParserOutput:
        snap = ctx.snapshot
        lines = _build_lines(snap)
        _mark_headers(lines)
        sections = _build_sections(lines)

        chunks: list[ParsedChunk] = []
        order = 0
        for header_chain, body in sections:
            if not body and not header_chain:
                continue
            for sub_body in _split_long(body, header_chain):
                path_parts = [h.text for h in header_chain]
                full_path = " > ".join([snap.name] + path_parts)
                md = _render_markdown(header_chain, sub_body)
                text = " ".join(l.text for l in sub_body)
                if not text and header_chain:
                    text = " > ".join(path_parts)

                colors = _collect_colors(sub_body)
                jira_tags = sorted({
                    ctx.color_to_jira[col] for col in colors if col in ctx.color_to_jira
                })
                # Inherit document-level JIRAs as fallback
                if not jira_tags:
                    jira_tags = ctx.document_metadata.get("jira_codes", [])

                chunks.append(ParsedChunk(
                    text=text,
                    markdown=md,
                    metadata={
                        "kind": "memo_section",
                        "sheet": snap.name,
                        "header_chain": path_parts,
                        "colors": colors,
                        "jira_tags": jira_tags,
                    },
                    hierarchy_path=full_path,
                    order=order,
                ))
                order += 1
        return ParserOutput(chunks=chunks)
