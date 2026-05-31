from __future__ import annotations

import re
import textwrap


def _pdf_escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def _latin1(text: str) -> str:
    replacements = {
        "\u2014": "-",
        "\u2013": "-",
        "\u2022": "-",
        "\u2192": "->",
        "\u00b7": "-",
        "\u2713": "OK",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", "replace").decode("latin-1")


def _strip_markdown(line: str) -> tuple[str, int, bool]:
    raw = line.rstrip()
    level = 0
    if raw.startswith("# "):
        level, raw = 1, raw[2:]
    elif raw.startswith("## "):
        level, raw = 2, raw[3:]
    elif raw.startswith("### "):
        level, raw = 3, raw[4:]
    raw = re.sub(r"\*\*(.*?)\*\*", r"\1", raw)
    raw = re.sub(r"\*(.*?)\*", r"\1", raw)
    raw = raw.replace("|", " | ")
    return _latin1(raw), level, raw.startswith("- ")


def markdown_to_pdf_bytes(markdown: str, title: str = "CreditIQ Report") -> bytes:
    """
    Small dependency-free PDF renderer for Lambda.

    It intentionally favors a stable, readable corporate report over visual
    richness: headings, wrapped paragraphs, bullets and tables are preserved as
    text. This avoids native PDF dependencies in the agent runtime.
    """
    width, height = 595, 842  # A4 points
    left, top, bottom = 54, 790, 52
    y = top
    page_lines: list[list[tuple[str, int, bool]]] = [[]]

    def add_line(text: str, level: int = 0, bullet: bool = False, gap: int = 0) -> None:
        nonlocal y
        size = 15 if level == 1 else 12 if level == 2 else 10
        leading = 19 if level == 1 else 16 if level == 2 else 13
        if gap:
            y -= gap
        if y < bottom + leading:
            page_lines.append([])
            y = top
        page_lines[-1].append((text, size, bullet))
        y -= leading

    for source_line in markdown.splitlines():
        if source_line.startswith("<!--") or source_line.startswith("{") or source_line.startswith("}"):
            continue
        if source_line.strip() == "-->":
            continue
        text, level, bullet = _strip_markdown(source_line)
        if not text.strip():
            y -= 5
            continue
        if text.strip() == "---":
            add_line("_" * 78, 0, False, gap=2)
            continue
        wrap_width = 58 if level == 1 else 72 if level == 2 else 96
        indent = "  " if bullet else ""
        for idx, chunk in enumerate(textwrap.wrap(text, width=wrap_width) or [""]):
            add_line((indent if idx else "") + chunk, level if idx == 0 else 0, bullet and idx == 0, gap=3 if level else 0)

    objects: list[bytes] = []

    def add_object(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_regular = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    font_bold = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    page_ids: list[int] = []

    for page_no, lines in enumerate(page_lines, start=1):
        commands = [
            "BT",
            f"/F2 9 Tf {left} 812 Td ({_pdf_escape(_latin1(title))}) Tj",
            "ET",
            f"0.12 0.27 0.39 RG 48 802 m 547 802 l S",
        ]
        cursor_y = top
        for text, size, bullet in lines:
            font = "F2" if size >= 12 else "F1"
            prefix = "- " if bullet and not text.lstrip().startswith("-") else ""
            commands.extend([
                "BT",
                f"/{font} {size} Tf",
                f"{left} {cursor_y} Td",
                f"({_pdf_escape(prefix + text)}) Tj",
                "ET",
            ])
            cursor_y -= 19 if size == 15 else 16 if size == 12 else 13
        commands.extend([
            "BT",
            f"/F1 8 Tf {left} 30 Td (CreditIQ - Pagina {page_no}) Tj",
            "ET",
        ])
        stream = "\n".join(commands).encode("latin-1", "replace")
        content_id = add_object(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        page_id = add_object(
            (
                f"<< /Type /Page /Parent 0 0 R /MediaBox [0 0 {width} {height}] "
                f"/Resources << /Font << /F1 {font_regular} 0 R /F2 {font_bold} 0 R >> >> "
                f"/Contents {content_id} 0 R >>"
            ).encode()
        )
        page_ids.append(page_id)

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    pages_id = add_object(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())

    for pid in page_ids:
        objects[pid - 1] = objects[pid - 1].replace(b"/Parent 0 0 R", f"/Parent {pages_id} 0 R".encode())

    catalog_id = add_object(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{i} 0 obj\n".encode())
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf.extend(f"{off:010d} 00000 n \n".encode())
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(pdf)
