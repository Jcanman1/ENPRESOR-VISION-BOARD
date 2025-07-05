import json
from pathlib import Path
import types
import pytest

generate_report = pytest.importorskip("generate_report")

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


def test_calculate_total_capacity_from_csv_rates():
    series = generate_report.pd.Series([60, 120, None])
    stats = generate_report.calculate_total_capacity_from_csv_rates(series)
    assert stats["total_capacity_lbs"] == pytest.approx(3.0)
    assert stats["max_rate_lbs_per_hr"] == 120


def test_calculate_total_objects_from_csv_rates():
    series = generate_report.pd.Series([10, 20])
    stats = generate_report.calculate_total_objects_from_csv_rates(series, log_interval_minutes=2)
    assert stats["total_objects"] == 60
    assert stats["average_rate_obj_per_min"] == 15


def test_draw_global_summary_totals(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    machine_dir = tmp_path / "1"
    machine_dir.mkdir()

    csv = machine_dir / "last_24h_metrics.csv"
    csv.write_text(
        "timestamp,capacity,accepts,rejects,objects_per_min,counter_1\n"
        "2020-01-01 00:00:00,60,30,30,10,1\n"
        "2020-01-01 00:01:00,60,30,30,10,1\n"
    )

    layout = {"machines": {"machines": [{"id": 1, "name": "M1"}], "next_machine_id": 2}}
    (data_dir / "floor_machine_layout.json").write_text(json.dumps(layout))

    monkeypatch.setattr(generate_report, "__file__", str(tmp_path / "dummy.py"))
    monkeypatch.setattr(generate_report.renderPDF, "draw", lambda *a, **k: None)

    canvas = DummyCanvas()
    generate_report.draw_global_summary(canvas, str(tmp_path), 0, 0, 100, 100)

    assert "2 lbs" in canvas.strings
    assert "1 lbs" in canvas.strings
    assert "20" in canvas.strings

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



def test_draw_machine_sections_totals_match_calculation(tmp_path, monkeypatch):

    machine_dir = tmp_path / "1"
    machine_dir.mkdir()
    csv_file = machine_dir / "last_24h_metrics.csv"
    csv_file.write_text(

        "timestamp,accepts,rejects,running,stopped\n"
        "2020-01-01 00:00:00,30,10,50,10\n"
        "2020-01-01 00:01:00,30,10,50,10\n"

    )

    monkeypatch.setattr(generate_report.renderPDF, "draw", lambda *a, **k: None)
    canvas = DummyCanvas()
    generate_report.draw_machine_sections(canvas, str(tmp_path), "1", 0, 200, 100, 200)


    df = generate_report.pd.read_csv(csv_file)
    a_stats = generate_report.calculate_total_capacity_from_csv_rates(df["accepts"])
    r_stats = generate_report.calculate_total_capacity_from_csv_rates(df["rejects"])

    assert f"{int(a_stats['total_capacity_lbs']):,} lbs" in canvas.strings
    assert f"{int(r_stats['total_capacity_lbs']):,} lbs" in canvas.strings


def test_global_summary_totals_sum_machines(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    layout = {
        "machines": {
            "machines": [
                {"id": 1, "name": "M1"},
                {"id": 2, "name": "M2"},
            ],
            "next_machine_id": 3,
        }
    }
    (data_dir / "floor_machine_layout.json").write_text(json.dumps(layout))

    for m_id in [1, 2]:
        mdir = tmp_path / str(m_id)
        mdir.mkdir()
        csv = mdir / "last_24h_metrics.csv"
        csv.write_text(
            "timestamp,capacity,accepts,rejects\n"
            "2020-01-01 00:00:00,60,30,10\n"
            "2020-01-01 00:01:00,60,30,10\n"
        )

    monkeypatch.setattr(generate_report, "__file__", str(tmp_path / "dummy.py"))
    monkeypatch.setattr(generate_report.renderPDF, "draw", lambda *a, **k: None)

    canvas = DummyCanvas()
    generate_report.draw_global_summary(canvas, str(tmp_path), 0, 0, 100, 100)

    total_accepts = total_rejects = total_capacity = 0
    for m_id in [1, 2]:
        df = generate_report.pd.read_csv(tmp_path / str(m_id) / "last_24h_metrics.csv")
        stats = generate_report.calculate_total_capacity_from_csv_rates(df["capacity"])
        total_capacity += stats["total_capacity_lbs"]
        stats = generate_report.calculate_total_capacity_from_csv_rates(df["accepts"])
        total_accepts += stats["total_capacity_lbs"]
        stats = generate_report.calculate_total_capacity_from_csv_rates(df["rejects"])
        total_rejects += stats["total_capacity_lbs"]

    assert f"{int(total_capacity):,} lbs" in canvas.strings
    assert f"{int(total_accepts):,} lbs" in canvas.strings
    assert f"{int(total_rejects):,} lbs" in canvas.strings



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


def test_build_report_passes_options(monkeypatch):
    captured = {}

    def fake_draw(pdf_path, export_dir, machines=None, include_global=True):
        captured["args"] = (pdf_path, export_dir, machines, include_global)

    monkeypatch.setattr(generate_report, "draw_layout_standard", fake_draw)
    generate_report.build_report({}, "out.pdf", export_dir="exp", machines=["1"], include_global=False)

    assert captured["args"] == ("out.pdf", "exp", ["1"], False)


def test_build_report_uses_optimized(monkeypatch):
    called = {}

    def fake_draw(pdf_path, export_dir, machines=None, include_global=True):
        called["optimized"] = True

    monkeypatch.setattr(generate_report, "draw_layout_optimized", fake_draw)
    generate_report.build_report({}, "out.pdf", use_optimized=True, export_dir="exp")

    assert called.get("optimized")


def _extract_total(strings, label):
    """Return the first integer value found after a given label."""
    found = False
    for s in strings:
        if found:
            clean = s.replace(",", "")
            if clean.isdigit():
                return int(clean)
        if s == label:
            found = True
    raise ValueError(f"label {label!r} not found")


def test_objects_per_min_totals_match(tmp_path, monkeypatch):
    """Global object count should equal the sum of machine totals."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    m1 = data_dir / "1"
    m1.mkdir()
    (m1 / "last_24h_metrics.csv").write_text(
        "timestamp,objects_per_min\n"
        "2020-01-01 00:00:00,5\n"
        "2020-01-01 00:01:00,10\n"
    )

    m2 = data_dir / "2"
    m2.mkdir()
    (m2 / "last_24h_metrics.csv").write_text(
        "timestamp,objects_per_min\n"
        "2020-01-01 00:00:00,2\n"
        "2020-01-01 00:01:00,3\n"
    )

    layout = {
        "machines": {
            "machines": [{"id": 1}, {"id": 2}],
            "next_machine_id": 3,
        }
    }
    (data_dir / "floor_machine_layout.json").write_text(json.dumps(layout))

    monkeypatch.setattr(generate_report, "__file__", str(tmp_path / "dummy.py"))
    monkeypatch.setattr(generate_report.renderPDF, "draw", lambda *a, **k: None)

    canvas_g = DummyCanvas()
    generate_report.draw_global_summary(canvas_g, str(data_dir), 0, 0, 100, 100)

    canvas_m1 = DummyCanvas()
    generate_report.draw_machine_sections(
        canvas_m1, str(data_dir), "1", 0, 200, 100, 200
    )

    canvas_m2 = DummyCanvas()
    generate_report.draw_machine_sections(
        canvas_m2, str(data_dir), "2", 0, 200, 100, 200
    )

    total1 = _extract_total(canvas_m1.strings, "Objects Processed:")
    total2 = _extract_total(canvas_m2.strings, "Objects Processed:")
    global_total = _extract_total(canvas_g.strings, "Total Objects Processed:")

    assert global_total == total1 + total2

    series1 = generate_report.pd.read_csv(m1 / "last_24h_metrics.csv")[
        "objects_per_min"
    ]
    series2 = generate_report.pd.read_csv(m2 / "last_24h_metrics.csv")[
        "objects_per_min"
    ]

    stats1 = generate_report.calculate_total_objects_from_csv_rates(series1)
    stats2 = generate_report.calculate_total_objects_from_csv_rates(series2)

    assert total1 == stats1["total_objects"]
    assert total2 == stats2["total_objects"]
