import json
from pathlib import Path
import types

import generate_report

class DummyCanvas:
    def __init__(self):
        self.strings = []
    def setFillColor(self, *args, **kwargs):
        pass
    def rect(self, *args, **kwargs):
        pass
    def setFont(self, *args, **kwargs):
        pass
    def drawString(self, *args):
        self.strings.append(args[-1])
    def drawCentredString(self, *args):
        self.strings.append(args[-1])
    def stringWidth(self, text, *args):
        return len(text)
    def setStrokeColor(self, *args, **kwargs):
        pass
    def line(self, *args, **kwargs):
        pass
    def saveState(self):
        pass
    def translate(self, *args, **kwargs):
        pass
    def rotate(self, *args, **kwargs):
        pass
    def restoreState(self):
        pass
    def setLineWidth(self, *args, **kwargs):
        pass

def test_draw_global_summary_single_machine(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    layout = {
        "machines": {
            "machines": [{"id": 1, "name": "M1"}],
            "next_machine_id": 2,
        }
    }
    (data_dir / "floor_machine_layout.json").write_text(json.dumps(layout))

    monkeypatch.setattr(generate_report, "__file__", str(tmp_path / "dummy.py"))
    monkeypatch.setattr(generate_report.renderPDF, "draw", lambda *a, **k: None)

    canvas = DummyCanvas()
    generate_report.draw_global_summary(canvas, str(tmp_path), 0, 0, 100, 100)

    assert "Machines:" in canvas.strings
    assert "1" in canvas.strings


def test_draw_machine_sections_runtime_line(tmp_path, monkeypatch):
    machine_dir = tmp_path / "1"
    machine_dir.mkdir()
    csv_file = machine_dir / "last_24h_metrics.csv"
    csv_file.write_text(
        "timestamp,accepts,rejects,running,stopped\n"
        "2020-01-01 00:00:00,1,0,65,5\n"
    )

    monkeypatch.setattr(generate_report.renderPDF, "draw", lambda *a, **k: None)
    canvas = DummyCanvas()
    generate_report.draw_machine_sections(
        canvas, str(tmp_path), "1", 0, 200, 100, 200
    )

    assert any("Run Time:" in s for s in canvas.strings)


def test_draw_header_uses_meipass_font(tmp_path, monkeypatch):
    font_src = Path(__file__).resolve().parents[1] / "Audiowide-Regular.ttf"
    target = tmp_path / "Audiowide-Regular.ttf"
    target.write_bytes(font_src.read_bytes())

    monkeypatch.setattr(generate_report.sys, "frozen", True, raising=False)
    monkeypatch.setattr(generate_report.sys, "_MEIPASS", str(tmp_path), raising=False)

    captured = {}

    def fake_TTFont(name, path):
        captured["path"] = path
        return types.SimpleNamespace(name=name)

    def fake_register(font):
        captured["registered"] = font.name

    monkeypatch.setattr(generate_report, "TTFont", fake_TTFont)
    monkeypatch.setattr(generate_report.pdfmetrics, "registerFont", fake_register)

    canvas = DummyCanvas()
    generate_report.draw_header(canvas, 100, 100)

    assert captured["path"] == str(target)
    assert captured["registered"] == "Audiowide"


def test_draw_header_uses_executable_dir_font(tmp_path, monkeypatch):
    """Font is loaded from the executable directory when frozen."""
    exec_dir = tmp_path / "exec"
    asset_dir = exec_dir / "assets"
    asset_dir.mkdir(parents=True)

    font_src = Path(__file__).resolve().parents[1] / "Audiowide-Regular.ttf"
    target = asset_dir / "Audiowide-Regular.ttf"
    target.write_bytes(font_src.read_bytes())

    monkeypatch.setattr(generate_report.sys, "frozen", True, raising=False)
    monkeypatch.setattr(generate_report.sys, "_MEIPASS", str(tmp_path / "notused"), raising=False)
    monkeypatch.setattr(generate_report.sys, "executable", str(exec_dir / "app.exe"), raising=False)

    captured = {}

    def fake_TTFont(name, path):
        captured["path"] = path
        return types.SimpleNamespace(name=name)

    def fake_register(font):
        captured["registered"] = font.name

    monkeypatch.setattr(generate_report, "TTFont", fake_TTFont)
    monkeypatch.setattr(generate_report.pdfmetrics, "registerFont", fake_register)

    canvas = DummyCanvas()
    generate_report.draw_header(canvas, 100, 100)

    assert captured["path"] == str(target)
    assert captured["registered"] == "Audiowide"


def test_draw_header_uses_internal_assets_font(tmp_path, monkeypatch):
    """Font is loaded from <exec_dir>/_internal/assets when frozen."""
    exec_dir = tmp_path / "exec"
    internal_assets = exec_dir / "_internal" / "assets"
    internal_assets.mkdir(parents=True)

    font_src = Path(__file__).resolve().parents[1] / "Audiowide-Regular.ttf"
    target = internal_assets / "Audiowide-Regular.ttf"
    target.write_bytes(font_src.read_bytes())

    monkeypatch.setattr(generate_report.sys, "frozen", True, raising=False)
    monkeypatch.setattr(generate_report.sys, "_MEIPASS", str(tmp_path / "notused"), raising=False)
    monkeypatch.setattr(generate_report.sys, "executable", str(exec_dir / "app.exe"), raising=False)

    captured = {}

    def fake_TTFont(name, path):
        captured["path"] = path
        return types.SimpleNamespace(name=name)

    def fake_register(font):
        captured["registered"] = font.name

    monkeypatch.setattr(generate_report, "TTFont", fake_TTFont)
    monkeypatch.setattr(generate_report.pdfmetrics, "registerFont", fake_register)

    canvas = DummyCanvas()
    generate_report.draw_header(canvas, 100, 100)

    assert captured["path"] == str(target)
    assert captured["registered"] == "Audiowide"
