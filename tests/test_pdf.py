import pytest

pytest.importorskip("reportlab")

from pulse.report.pdf import write_report_pdf


def test_write_report_pdf(tmp_path):
    path = write_report_pdf("测试月报\n第二行", tmp_path / "report.pdf")
    assert path.exists()
    assert path.stat().st_size > 0
