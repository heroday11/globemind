from __future__ import annotations

import re
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Iterable, List
from xml.sax.saxutils import escape


_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CORE_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_DCTERMS_NS = "http://purl.org/dc/terms/"
_DCTYPE_NS = "http://purl.org/dc/dcmitype/"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"


def build_docx_bytes(markdown: str, *, title: str = "GlobeMind Report", creator: str = "GlobeMind") -> bytes:
    """Build a compact DOCX document from Markdown-like report text."""
    body = _render_document_body(markdown or "", title=title)
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    files = {
        "[Content_Types].xml": _content_types_xml(),
        "_rels/.rels": _root_rels_xml(),
        "docProps/core.xml": _core_props_xml(title=title, creator=creator, created=created),
        "docProps/app.xml": _app_props_xml(),
        "word/_rels/document.xml.rels": _document_rels_xml(),
        "word/styles.xml": _styles_xml(),
        "word/document.xml": body,
    }
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content.encode("utf-8"))
    return out.getvalue()


def _content_types_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""


def _root_rels_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{_REL_NS}">
  <Relationship Id="rId1" Type="{_DOC_REL_NS}/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="{_DOC_REL_NS}/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="{_DOC_REL_NS}/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""


def _document_rels_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{_REL_NS}">
  <Relationship Id="rId1" Type="{_DOC_REL_NS}/styles" Target="styles.xml"/>
</Relationships>"""


def _core_props_xml(*, title: str, creator: str, created: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="{_CORE_NS}" xmlns:dc="{_DC_NS}" xmlns:dcterms="{_DCTERMS_NS}" xmlns:dcmitype="{_DCTYPE_NS}" xmlns:xsi="{_XSI_NS}">
  <dc:title>{_x(title)}</dc:title>
  <dc:creator>{_x(creator)}</dc:creator>
  <cp:lastModifiedBy>{_x(creator)}</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>
</cp:coreProperties>"""


def _app_props_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>GlobeMind</Application>
</Properties>"""


def _styles_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{_WORD_NS}">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Aptos" w:eastAsia="Microsoft YaHei" w:hAnsi="Aptos"/><w:sz w:val="21"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:before="180" w:after="240"/></w:pPr>
    <w:rPr><w:b/><w:rFonts w:ascii="Aptos Display" w:eastAsia="Microsoft YaHei" w:hAnsi="Aptos Display"/><w:sz w:val="36"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:outlineLvl w:val="0"/><w:spacing w:before="280" w:after="120"/></w:pPr>
    <w:rPr><w:b/><w:color w:val="1F2937"/><w:sz w:val="30"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:outlineLvl w:val="1"/><w:spacing w:before="220" w:after="100"/></w:pPr>
    <w:rPr><w:b/><w:color w:val="334155"/><w:sz w:val="26"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading3">
    <w:name w:val="heading 3"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:outlineLvl w:val="2"/><w:spacing w:before="180" w:after="80"/></w:pPr>
    <w:rPr><w:b/><w:color w:val="475569"/><w:sz w:val="23"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="ListParagraph">
    <w:name w:val="List Paragraph"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:ind w:left="420" w:hanging="220"/></w:pPr>
  </w:style>
</w:styles>"""


def _render_document_body(markdown: str, *, title: str) -> str:
    blocks = list(_parse_markdown_blocks(markdown))
    paragraphs: List[str] = []
    used_title = False
    for block in blocks:
        kind = block[0]
        if kind == "heading":
            level, text = block[1], block[2]
            if level == 1 and not used_title:
                paragraphs.append(_paragraph(text, style="Title"))
                used_title = True
            else:
                paragraphs.append(_paragraph(text, style=f"Heading{min(max(level, 1), 3)}"))
        elif kind == "list":
            ordered, text = block[1], block[2]
            prefix = "" if ordered else "- "
            paragraphs.append(_paragraph(f"{prefix}{text}", style="ListParagraph"))
        elif kind == "table":
            paragraphs.append(_table(block[1]))
        elif kind == "paragraph":
            paragraphs.append(_paragraph(block[1]))
    if not used_title and title:
        paragraphs.insert(0, _paragraph(title, style="Title"))
    if not paragraphs:
        paragraphs.append(_paragraph(title or "GlobeMind Report", style="Title"))
    sect = (
        '<w:sectPr>'
        '<w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1200" w:bottom="1440" w:left="1200" w:header="720" w:footer="720" w:gutter="0"/>'
        '</w:sectPr>'
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{_WORD_NS}">
  <w:body>
    {''.join(paragraphs)}
    {sect}
  </w:body>
</w:document>"""


def _parse_markdown_blocks(markdown: str) -> Iterable[tuple]:
    lines = [line.rstrip() for line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    i = 0
    para: List[str] = []

    def flush_para() -> Iterable[tuple]:
        nonlocal para
        if para:
            text = " ".join(part.strip() for part in para if part.strip()).strip()
            para = []
            if text:
                yield ("paragraph", text)

    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if not line:
            yield from flush_para()
            i += 1
            continue
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", line):
            yield from flush_para()
            i += 1
            continue
        if _is_table_start(lines, i):
            yield from flush_para()
            rows: List[List[str]] = []
            rows.append(_split_table_row(lines[i]))
            i += 2
            while i < len(lines) and "|" in lines[i].strip() and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            yield ("table", rows)
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            yield from flush_para()
            yield ("heading", len(heading.group(1)), _strip_inline_markdown(heading.group(2)))
            i += 1
            continue
        unordered = re.match(r"^[-*+]\s+(.+)$", line)
        ordered = re.match(r"^(\d+[.)])\s+(.+)$", line)
        if unordered or ordered:
            yield from flush_para()
            if ordered:
                yield ("list", True, f"{ordered.group(1)} {ordered.group(2).strip()}")
            else:
                yield ("list", False, unordered.group(1).strip())
            i += 1
            continue
        para.append(line)
        i += 1
    yield from flush_para()


def _is_table_start(lines: list[str], i: int) -> bool:
    if i + 1 >= len(lines):
        return False
    first = lines[i].strip()
    second = lines[i + 1].strip()
    if "|" not in first or "|" not in second:
        return False
    cells = [c.strip() for c in second.strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def _split_table_row(line: str) -> List[str]:
    return [_strip_inline_markdown(cell.strip()) for cell in line.strip().strip("|").split("|")]


def _table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rendered_rows = []
    for ridx, row in enumerate(rows):
        cells = []
        for idx in range(width):
            text = row[idx] if idx < len(row) else ""
            shade = '<w:shd w:fill="F1F5F9"/>' if ridx == 0 else ""
            cells.append(
                '<w:tc>'
                f'<w:tcPr><w:tcW w:w="{max(1200, int(9000 / max(width, 1)))}" w:type="dxa"/>{shade}</w:tcPr>'
                f'{_paragraph(text, bold=ridx == 0)}'
                '</w:tc>'
            )
        rendered_rows.append(f'<w:tr>{"".join(cells)}</w:tr>')
    return '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders><w:top w:val="single" w:sz="4" w:color="CBD5E1"/><w:left w:val="single" w:sz="4" w:color="CBD5E1"/><w:bottom w:val="single" w:sz="4" w:color="CBD5E1"/><w:right w:val="single" w:sz="4" w:color="CBD5E1"/><w:insideH w:val="single" w:sz="4" w:color="CBD5E1"/><w:insideV w:val="single" w:sz="4" w:color="CBD5E1"/></w:tblBorders></w:tblPr>' + "".join(rendered_rows) + "</w:tbl>"


def _paragraph(text: str, *, style: str | None = None, bold: bool = False) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}{''.join(_inline_runs(text, bold=bold))}</w:p>"


def _inline_runs(text: str, *, bold: bool = False) -> Iterable[str]:
    text = _strip_links(text or "")
    parts = re.split(r"(\*\*[^*]+\*\*)", text)
    for part in parts:
        if not part:
            continue
        is_bold = bold or (part.startswith("**") and part.endswith("**") and len(part) > 4)
        clean = part[2:-2] if is_bold and part.startswith("**") else part
        if not clean:
            continue
        rpr = "<w:rPr><w:b/></w:rPr>" if is_bold else ""
        space = ' xml:space="preserve"' if clean[:1].isspace() or clean[-1:].isspace() else ""
        yield f"<w:r>{rpr}<w:t{space}>{_x(clean)}</w:t></w:r>"


def _strip_links(text: str) -> str:
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)


def _strip_inline_markdown(text: str) -> str:
    text = _strip_links(text or "")
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = text.replace("***", "").replace("**", "").replace("__", "")
    return text.strip()


def _x(value: str) -> str:
    return escape(str(value or ""), {'"': "&quot;"})
