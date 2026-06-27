from __future__ import annotations

from pathlib import Path


def write_report_pdf(text: str, output_path: Path) -> Path:
    """将月报正文写入 PDF（支持中文 Unicode）。"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise RuntimeError("请安装 PDF 依赖：pip install -e '.[pdf]'") from exc

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    c = canvas.Canvas(str(output_path), pagesize=A4)
    width, height = A4
    x = 20 * mm
    y = height - 20 * mm
    line_height = 6 * mm
    c.setFont("STSong-Light", 10)

    for line in text.splitlines():
        if y < 20 * mm:
            c.showPage()
            c.setFont("STSong-Light", 10)
            y = height - 20 * mm
        c.drawString(x, y, line[:120])
        y -= line_height

    c.save()
    return output_path
