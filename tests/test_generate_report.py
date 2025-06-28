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
