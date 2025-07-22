"""Functions for building PDF production reports from CSV metrics."""

import os
import sys
import json
import datetime
import pandas as pd
import df_processor
import logging
import argparse
from datetime import timedelta
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics import renderPDF
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
import base64
from PIL import Image
import io
import math  # for label angle calculations
# Default fonts used throughout the report
FONT_DEFAULT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
# Default font size for numeric values displayed in the counts sections
COUNT_VALUE_FONT_SIZE = 18
# Font size for numeric values in the sensitivity grid's last column
SENSITIVITY_VALUE_FONT_SIZE = 10
LAB_OBJECT_SCALE_FACTOR = 1.042

# Weight conversion for lab metrics: 1 lbs per 1800 pieces

LAB_WEIGHT_MULTIPLIER = 1 / 1800

from i18n import tr

# Colors used for bar charts and sensitivity section borders
BAR_COLORS = [
    colors.red,
    colors.blue,
    colors.green,
    colors.orange,
    colors.purple,
    colors.brown,
    colors.pink,
    colors.gray,
    colors.cyan,
    colors.magenta,
    colors.yellow,
    colors.black,
]


def _lookup_setting(data: dict, dotted_key: str, default="N/A"):
    """Return a nested setting value using dotted notation.

    Parameters
    ----------
    data : dict
        Dictionary of settings loaded from JSON.
    dotted_key : str
        Dot separated path to the desired value.
    default : any, optional
        Value returned when the key path does not exist.
    """

    if not isinstance(data, dict):
        return default

    # First check for a flat key using dotted notation
    if dotted_key in data:
        return data[dotted_key]

    cur = data
    for part in dotted_key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur

def _minutes_to_hm(minutes: float) -> str:
    """Return an "H:MM" string from a minute count."""
    try:
        m = int(round(float(minutes)))
    except Exception:
        m = 0
    hours, mins = divmod(m, 60)
    return f"{hours}:{mins:02d}"


def calculate_total_capacity_from_csv_rates(
    csv_rate_entries,
    log_interval_minutes=1,
    timestamps=None,
    is_lab_mode=False,
    values_in_kg=False,
):
    """
    Convert lbs/hr rates into total lbs and related stats.
    
    Enhanced to handle both regular and lab mode data correctly.
    
    Parameters
    ----------
    csv_rate_entries : iterable
        Sequence of lbs/hr rate samples.
    log_interval_minutes : int, optional
        Interval between samples in minutes. Only used for regular mode.
    timestamps : iterable, optional
        Timestamp data for lab mode calculations.
    is_lab_mode : bool, optional
        Whether to use irregular interval calculation for lab mode.
    values_in_kg : bool, optional
        Set to ``True`` if the provided rates are in kilograms per hour.
        When enabled the values are converted to pounds per hour before
        totals are computed.
    """
    def _calc(entries):
        if is_lab_mode and timestamps is not None:
            return _calculate_capacity_lab_mode(
                timestamps, entries, values_in_kg=values_in_kg
            )

        rates = []
        for r in entries:
            try:
                val = float(r)
            except (TypeError, ValueError):
                continue
            if not pd.isna(val):
                rates.append(val)

        if not rates:
            return {
                "total_capacity_lbs": 0,
                "average_rate_lbs_per_hr": 0,
                "max_rate_lbs_per_hr": 0,
                "min_rate_lbs_per_hr": 0,
            }

        if values_in_kg:
            rates = [r * 2.205 for r in rates]

        total_lbs = sum(val * (log_interval_minutes / 60.0) for val in rates)
        avg_rate = sum(rates) / len(rates)

        return {
            "total_capacity_lbs": total_lbs,
            "average_rate_lbs_per_hr": avg_rate,
            "max_rate_lbs_per_hr": max(rates),
            "min_rate_lbs_per_hr": min(rates),
        }

    return df_processor.process_with_cleanup(csv_rate_entries, _calc)

def _calculate_capacity_lab_mode(timestamps, rates, *, values_in_kg=False):
    """Calculate capacity totals using actual time intervals for lab mode data."""
    # Convert to list to avoid pandas negative index issues
    timestamps = list(timestamps)
    rates = list(rates)

    if len(timestamps) < 2 or len(rates) < 2:
        return {
            "total_capacity_lbs": 0,
            "average_rate_lbs_per_hr": 0,
            "max_rate_lbs_per_hr": 0,
            "min_rate_lbs_per_hr": 0,
        }
    
    total_lbs = 0
    valid_rates = []
    
    for i in range(len(timestamps) - 1):
        try:
            current_rate = float(rates[i])
            if pd.isna(current_rate):
                continue

            if values_in_kg:
                current_rate *= 2.205
                
            # Parse timestamps
            current_time = pd.to_datetime(timestamps[i])
            next_time = pd.to_datetime(timestamps[i + 1])
            
            # Calculate actual time interval in hours
            time_diff_hours = (next_time - current_time).total_seconds() / 3600
            
            # Add production for this interval
            total_lbs += current_rate * time_diff_hours
            valid_rates.append(current_rate)
            
        except (ValueError, TypeError):
            continue
    
    # Add the last rate for statistics
    try:
        last_rate = float(rates[-1])
        if not pd.isna(last_rate):
            if values_in_kg:
                last_rate *= 2.205
            valid_rates.append(last_rate)
    except (ValueError, TypeError, IndexError):
        pass
    
    if not valid_rates:
        return {
            "total_capacity_lbs": 0,
            "average_rate_lbs_per_hr": 0,
            "max_rate_lbs_per_hr": 0,
            "min_rate_lbs_per_hr": 0,
        }
    
    return {
        "total_capacity_lbs": total_lbs,
        "average_rate_lbs_per_hr": sum(valid_rates) / len(valid_rates),
        "max_rate_lbs_per_hr": max(valid_rates),
        "min_rate_lbs_per_hr": min(valid_rates),
    }

def _calculate_objects_lab_mode(timestamps, rates):
    """Calculate object totals using actual time intervals for lab mode data."""
    # Convert to list to avoid pandas negative index issues
    timestamps = list(timestamps)
    rates = list(rates)

    if len(timestamps) < 2 or len(rates) < 2:
        return {
            "total_objects": 0,
            "average_rate_obj_per_min": 0,
            "max_rate_obj_per_min": 0,
            "min_rate_obj_per_min": 0,
        }
    
    total_objects = 0
    valid_rates = []
    
    for i in range(len(timestamps) - 1):
        try:
            current_rate = float(rates[i])
            if pd.isna(current_rate):
                continue
                
            # Parse timestamps
            current_time = pd.to_datetime(timestamps[i])
            next_time = pd.to_datetime(timestamps[i + 1])
            
            # Calculate actual time interval in minutes
            time_diff_minutes = (next_time - current_time).total_seconds() / 60

            # Add production for this interval with scaling factor
            total_objects += (
                current_rate * time_diff_minutes * LAB_OBJECT_SCALE_FACTOR
            )
            valid_rates.append(current_rate)
            
        except (ValueError, TypeError):
            continue
    
    # Add the last rate for statistics
    try:
        last_rate = float(rates[-1])
        if not pd.isna(last_rate):
            valid_rates.append(last_rate)
    except (ValueError, TypeError, IndexError):
        pass
    
    if not valid_rates:
        return {
            "total_objects": 0,
            "average_rate_obj_per_min": 0,
            "max_rate_obj_per_min": 0,
            "min_rate_obj_per_min": 0,
        }
    
    return {
        "total_objects": total_objects,
        "average_rate_obj_per_min": sum(valid_rates) / len(valid_rates),
        "max_rate_obj_per_min": max(valid_rates),
        "min_rate_obj_per_min": min(valid_rates),
    }


def calculate_total_objects_from_csv_rates(csv_rate_entries, log_interval_minutes=1,
                                         timestamps=None, is_lab_mode=False):
    """
    Convert objects/min rates into total objects.
    
    Enhanced to handle both regular and lab mode data correctly.
    """
    def _calc(entries):
        if is_lab_mode and timestamps is not None:
            return _calculate_objects_lab_mode(timestamps, entries)

        rates = []
        for r in entries:
            try:
                val = float(r)
            except (TypeError, ValueError):
                continue
            if not pd.isna(val):
                rates.append(val)

        if not rates:
            return {
                "total_objects": 0,
                "average_rate_obj_per_min": 0,
                "max_rate_obj_per_min": 0,
                "min_rate_obj_per_min": 0,
            }

        total_objs = sum(val * log_interval_minutes for val in rates)
        avg_rate = sum(rates) / len(rates)

        return {
            "total_objects": total_objs,
            "average_rate_obj_per_min": avg_rate,
            "max_rate_obj_per_min": max(rates),
            "min_rate_obj_per_min": min(rates),
        }

    return df_processor.process_with_cleanup(csv_rate_entries, _calc)


def last_value_scaled(series, scale=1.0):
    """Return the last numeric value in ``series`` multiplied by ``scale``.

    Any non-numeric values are ignored. If ``series`` is empty or contains no
    valid numeric entries ``0`` is returned."""

    try:
        cleaned = pd.to_numeric(series, errors="coerce").dropna()
        if cleaned.empty:
            return 0
        return float(cleaned.iloc[-1]) * scale
    except Exception:
        return 0

from hourly_data_saving import EXPORT_DIR as METRIC_EXPORT_DIR, get_historical_data

log_level = os.environ.get("LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.WARNING),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
if not logging.getLogger().handlers:
    logging.getLogger().addHandler(logging.StreamHandler())



def draw_header(
    c,
    width,
    height,
    page_number=None,
    *,
    lang="en",
    is_lab_mode: bool = False,
    lab_test_name: str | None = None,
):
    """Draw the header section on each page with optional page number.

    When ``is_lab_mode`` is ``True`` and ``lab_test_name`` is provided the test
    name is drawn below the date stamp.
    """
    global FONT_DEFAULT, FONT_BOLD
    # Determine directories to search for the font
    if getattr(sys, "frozen", False):
        # When frozen with PyInstaller, resources may be located next to the
        # executable or in the temporary _MEIPASS directory.  Search both.
        base_dir = getattr(sys, "_MEIPASS", "")
        exec_dir = os.path.dirname(getattr(sys, "executable", ""))
        search_dirs = [
            base_dir,
            os.path.join(base_dir, "assets"),
            exec_dir,
            os.path.join(exec_dir, "assets"),
            os.path.join(exec_dir, "_internal"),
            os.path.join(exec_dir, "_internal", "assets"),
        ]
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        search_dirs = [
            base_dir,
            os.path.join(base_dir, "assets"),
            "/usr/share/fonts/truetype",
            "/usr/share/fonts/opentype",
            "/usr/share/fonts/truetype/noto",
            "/usr/share/fonts/opentype/noto",
        ]
    
    # Check what font files actually exist in the search directories
    for d in search_dirs:
        logger.debug(f"Checking directory: {d}")
        try:
            files_in_dir = [
                f
                for f in os.listdir(d)
                if f.lower().endswith(('.ttf', '.otf', '.ttc'))
            ]
            logger.debug(f"Font files found in {d}: {files_in_dir}")
        except Exception as e:
            logger.debug(f"Error listing {d}: {e}")
    
    # Try different possible filenames for Audiowide font
    possible_font_files = [
        'Audiowide-Regular.ttf',  # This is the actual Google Fonts filename
        'Audiowide.ttf',
        'audiowide-regular.ttf',
        'audiowide.ttf'
    ]

    # Possible filenames for the Japanese default font
    possible_jp_fonts = [
        'NotoSansJP-Regular.otf',
        'NotoSansJP-Regular.ttf',
        'NotoSansJP.otf',
        'NotoSansJP.ttf',
        'NotoSansCJK-Regular.ttc',
    ]
    
    font_enpresor = FONT_BOLD  # Default fallback
    chosen_path = None
    jp_font_path = None

    for d in search_dirs:
        for font_filename in possible_font_files:
            font_path = os.path.join(d, font_filename)
            logger.debug(f"Trying font file: {font_path}")

            if os.path.isfile(font_path):
                try:
                    pdfmetrics.registerFont(TTFont('Audiowide', font_path))
                    font_enpresor = 'Audiowide'
                    chosen_path = font_path
                    logger.info(f"Audiowide font loaded from: {font_path}")
                    break
                except Exception as e:
                    logger.debug(f"\u274C Error registering font from {font_path}: {e}")
        if chosen_path:
            break

    # Search for Japanese font
    for d in search_dirs:
        for font_filename in possible_jp_fonts:
            jp_path = os.path.join(d, font_filename)
            logger.debug(f"Trying JP font file: {jp_path}")
            if os.path.isfile(jp_path):
                try:
                    if jp_path.lower().endswith('.ttc'):
                        pdfmetrics.registerFont(
                            TTFont('NotoSansJP', jp_path, subfontIndex=0)
                        )
                    else:
                        pdfmetrics.registerFont(TTFont('NotoSansJP', jp_path))
                    jp_font_path = jp_path
                    logger.info(f"Japanese font loaded from: {jp_path}")
                    break
                except Exception as e:
                    logger.debug(f"\u274C Error registering font from {jp_path}: {e}")
        if jp_font_path:
            break

    if not chosen_path:
        logger.debug("\u26A0\ufe0f  No Audiowide font file found.")
        logger.debug("The file you downloaded might be named 'Audiowide-Regular.ttf'")
        logger.debug("Either rename it to 'Audiowide.ttf' or ensure 'Audiowide-Regular.ttf' is in one of:")
        for d in search_dirs:
            logger.debug(d)

    # Update global default fonts depending on language
    if lang == "ja" and jp_font_path:
        FONT_DEFAULT = "NotoSansJP"
        FONT_BOLD = "NotoSansJP"
    else:
        FONT_DEFAULT = "Helvetica"
        FONT_BOLD = "Helvetica-Bold"

    # Document title
    title_size = 24
    x_center = width / 2
    satake = "Satake "
    enpresor = "ENPRESOR"
    data_rep = tr("data_report", lang)
    font_default = FONT_BOLD
    
    # Calculate widths for centering
    w_sat = c.stringWidth(satake, font_default, title_size)
    w_enp = c.stringWidth(enpresor, font_enpresor, title_size)
    w_dat = c.stringWidth(data_rep, font_default, title_size)
    start_x = x_center - (w_sat + w_enp + w_dat) / 2
    y_title = height - 50
    
    # Draw "Satake " in black
    c.setFont(font_default, title_size)
    c.setFillColor(colors.black)
    c.drawString(start_x, y_title, satake)
    
    # Draw "Enpresor" in red with Audiowide font (if available)
    c.setFont(font_enpresor, title_size)
    c.setFillColor(colors.red)
    c.drawString(start_x + w_sat, y_title, enpresor)
    logger.debug(f"Drawing 'Enpresor' with font: {font_enpresor}")
    
    # Draw " Data Report" in black
    c.setFont(font_default, title_size)
    c.setFillColor(colors.black)
    c.drawString(start_x + w_sat + w_enp, y_title, data_rep)

    # Date stamp
    date_str = datetime.datetime.now().strftime('%m/%d/%Y')
    c.setFont(FONT_DEFAULT, 10)
    c.setFillColor(colors.black)
    c.drawCentredString(x_center, height - 70, date_str)

    if is_lab_mode and lab_test_name:
        c.setFont(FONT_BOLD, 12)
        c.drawCentredString(x_center, height - 85, lab_test_name)
    
    # Add page number in bottom right corner
    if page_number is not None:
        margin = 40  # Same margin as used in layout
        c.setFont(FONT_DEFAULT, 10)
        c.setFillColor(colors.black)
        page_text = tr("page_label", lang).format(page=page_number)
        # Position: right margin minus text width, bottom margin
        text_width = c.stringWidth(page_text, FONT_DEFAULT, 10)
        c.drawString(width - margin - text_width, margin - 10, page_text)
    
    return height - 100  # Return the Y position where content can start



def draw_global_summary(
    c,
    csv_parent_dir,
    x0,
    y0,
    total_w,
    available_height,
    *,
    is_lab_mode=False,
    lang="en",
    values_in_kg=False,
):
    """Draw the global summary sections (totals, pie, trend, counts)"""
    machines = sorted(
        [d for d in os.listdir(csv_parent_dir)
         if os.path.isdir(os.path.join(csv_parent_dir, d)) and d.isdigit()]
    )

    # Attempt to load machine count from the saved layout (All Machines view)
    layout_machine_count = None
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        layout_path = os.path.join(script_dir, "data", "floor_machine_layout.json")
        if os.path.isfile(layout_path):
            with open(layout_path) as f:
                data = json.load(f)
            layout_machine_count = len(
                data.get("machines", {}).get("machines", [])
            )
    except Exception as exc:
        logger.warning(f"Unable to read layout file: {exc}")

    machine_count = layout_machine_count if layout_machine_count is not None else len(machines)
    
    # Calculate section heights
    h1 = available_height * 0.1  # Totals
    h2 = available_height * 0.35  # Pie and trend
    h4 = available_height * 0.15  # Counts
    spacing_gap = 10
    
    current_y = y0 + available_height
    
    # Section dimensions
    w_left = total_w * 0.4
    w_right = total_w * 0.6
    
    # Aggregate global data
    total_capacity = total_accepts = total_rejects = 0
    total_objects = total_removed = 0
    for m in machines:
        fp = os.path.join(csv_parent_dir, m, 'last_24h_metrics.csv')
        if os.path.isfile(fp):
            df = df_processor.safe_read_csv(fp)
            settings_data = load_machine_settings(csv_parent_dir, m)
            if 'capacity' in df.columns:
                stats = calculate_total_capacity_from_csv_rates(
                    df['capacity'],
                    timestamps=df['timestamp'] if is_lab_mode else None,
                    is_lab_mode=is_lab_mode,
                    values_in_kg=values_in_kg,
                )
                total_capacity += stats['total_capacity_lbs']

            ac = next((c for c in df.columns if c.lower() == 'accepts'), None)
            rj = next((c for c in df.columns if c.lower() == 'rejects'), None)

            if is_lab_mode:
                machine_objects = 0
                if 'objects_60M' in df.columns:
                    machine_objects = last_value_scaled(df['objects_60M'], 60)
                elif 'objects_per_min' in df.columns:
                    machine_objects = last_value_scaled(df['objects_per_min'], 60)
                elif ac or rj:
                    ac_tot = last_value_scaled(df[ac], 60) if ac else 0
                    rj_tot = last_value_scaled(df[rj], 60) if rj else 0
                    machine_objects = ac_tot + rj_tot

                machine_removed = 0
                for i in range(1, 13):
                    col = next((c for c in df.columns if c.lower() == f'counter_{i}'), None)
                    if not col:
                        continue

                    assigned_val = _lookup_setting(
                        settings_data,
                        f"Settings.ColorSort.Primary{i}.IsAssigned",
                        True,
                    )
                    if not _bool_from_setting(assigned_val):
                        continue

                    machine_removed += last_value_scaled(df[col], 60)

                total_objects += machine_objects
                total_removed += machine_removed

                clean_objs = machine_objects - machine_removed
                total_accepts += clean_objs * LAB_WEIGHT_MULTIPLIER
                total_rejects += machine_removed * LAB_WEIGHT_MULTIPLIER
            else:
                if ac:
                    stats = calculate_total_capacity_from_csv_rates(
                        df[ac],
                        timestamps=df['timestamp'] if is_lab_mode else None,
                        is_lab_mode=is_lab_mode,
                        values_in_kg=values_in_kg,
                    )
                    total_accepts += stats['total_capacity_lbs']
                if rj:
                    stats = calculate_total_capacity_from_csv_rates(
                        df[rj],
                        timestamps=df['timestamp'] if is_lab_mode else None,
                        is_lab_mode=is_lab_mode,
                        values_in_kg=values_in_kg,
                    )
                    total_rejects += stats['total_capacity_lbs']

                machine_objects = 0
                if 'objects_60M' in df.columns and is_lab_mode:
                    obj_stats = calculate_total_objects_from_csv_rates(
                        df['objects_60M'],
                        timestamps=df['timestamp'],
                        is_lab_mode=True,
                    )
                    machine_objects = obj_stats['total_objects']
                elif 'objects_per_min' in df.columns:
                    obj_stats = calculate_total_objects_from_csv_rates(
                        df['objects_per_min'],
                        timestamps=df['timestamp'] if is_lab_mode else None,
                        is_lab_mode=is_lab_mode,
                    )
                    machine_objects = obj_stats['total_objects']
                elif is_lab_mode:
                    ac_tot = rj_tot = 0
                    if ac:
                        a_stats = calculate_total_objects_from_csv_rates(
                            df[ac],
                            timestamps=df['timestamp'],
                            is_lab_mode=True,
                        )
                        ac_tot = a_stats['total_objects']
                    if rj:
                        r_stats = calculate_total_objects_from_csv_rates(
                            df[rj],
                            timestamps=df['timestamp'],
                            is_lab_mode=True,
                        )
                        rj_tot = r_stats['total_objects']
                    machine_objects = ac_tot + rj_tot

                machine_removed = 0
                for i in range(1, 13):
                    col = next((c for c in df.columns if c.lower() == f'counter_{i}'), None)
                    if col:
                        c_stats = calculate_total_objects_from_csv_rates(
                            df[col],
                            timestamps=df['timestamp'] if is_lab_mode else None,
                            is_lab_mode=is_lab_mode,
                        )
                        machine_removed += c_stats['total_objects']

                total_objects += machine_objects
                total_removed += machine_removed











    # Section 1: Totals
    y_sec1 = current_y - h1
    c.setFillColor(colors.HexColor('#1f77b4'))
    c.rect(x0, y_sec1, total_w, h1, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont(FONT_BOLD, 10)
    c.drawString(x0+10, y_sec1+h1-14, tr('24hr_totals', lang))
    col_w = total_w / 4
    labels = [
        tr('machines_label', lang),
        tr('processed_label', lang),
        tr('accepted_label', lang),
        tr('rejected_label', lang),
    ]
    if is_lab_mode:
        accepts_fmt = f"{total_accepts:,.2f} lbs"
        rejects_fmt = f"{total_rejects:,.2f} lbs"
    else:
        accepts_fmt = f"{int(total_accepts):,} lbs"
        rejects_fmt = f"{int(total_rejects):,} lbs"

    values = [
        f"{machine_count}",
        f"{int(total_capacity):,} lbs",
        accepts_fmt,
        rejects_fmt,
    ]
    c.setFont(FONT_BOLD, 12)
    for i,label in enumerate(labels):
        lw = c.stringWidth(label, FONT_BOLD, 12)
        c.drawString(x0 + col_w*i + (col_w - lw)/2, y_sec1 + h1/2 - 4, label)
    c.setFont(FONT_BOLD, 14)
    for i,val in enumerate(values):
        vw = c.stringWidth(val, FONT_BOLD, 14)
        c.drawString(x0 + col_w*i + (col_w - vw)/2, y_sec1 + h1/2 - 22, val)
    c.setStrokeColor(colors.black)
    c.rect(x0, y_sec1, total_w, h1)

    # Section 2: Pie global accepts/rejects
    y_sec2 = y_sec1 - h2 - spacing_gap
    c.setStrokeColor(colors.black)
    c.rect(x0, y_sec2, w_left, h2)
    c.setFont(FONT_BOLD,12); c.setFillColor(colors.black)
    c.drawCentredString(x0+w_left/2, y_sec2+h2-15, tr('total_accepts_rejects_title', lang))
    
    # Draw pie chart logic (keeping your existing pie chart code)
    pad=10; lh=20
    aw,ah=w_left-2*pad,h2-2*pad-lh
    psz=min(aw,ah)*0.85*1.1
    px,py=x0+pad+(aw-psz)/2,y_sec2+pad+lh+(ah-psz)/2-ah*0.1
    
    # Draw pie
    d=Drawing(psz,psz); pie=Pie()
    pie.x=pie.y=0; pie.width=pie.height=psz
    pie.startAngle = -30
    pie.direction = 'clockwise'
    pie.data=[total_accepts,total_rejects]
    pie.slices[0].fillColor=colors.green; pie.slices[1].fillColor=colors.red
    pie.sideLabels = False
    d.add(pie)
    
    c.saveState()
    c.translate(px + psz/2, py + psz/2)
    c.rotate(-30)
    renderPDF.draw(d, c, -psz/2, -psz/2)
    c.restoreState()
    
    # Manual labels with percentages
    total = total_accepts + total_rejects
    if total > 0:
        values = [total_accepts, total_rejects]
        percentages = [(val/total)*100 for val in values]
        angles = [180 + -59 + (360*(total_rejects/total)*100/2/100), -59 + (360*(total_rejects/total)*100/2/100)]    
        print("global angles",angles, total_rejects, total)
        labels_tr = [tr('accepts', lang), tr('rejects', lang)]
        for i, (label, pct, angle) in enumerate(zip(labels_tr, percentages, angles)):
            angle_rad = math.radians(angle)
            radius = psz/2 * 0.9
            cx = px + psz/2 + math.cos(angle_rad) * radius
            cy = py + psz/2 + math.sin(angle_rad) * radius
            line_len = 20
            ex = cx + math.cos(angle_rad) * line_len
            ey = cy + math.sin(angle_rad) * line_len
            
            c.setStrokeColor(colors.black)
            c.setLineWidth(1)
            c.line(cx, cy, ex, ey)
            
            c.setFont(FONT_BOLD, 8)
            c.setFillColor(colors.black)
            label_text = f"{label}"
            pct_text = f"{pct:.1f}%"
            
            if math.cos(angle_rad) >= 0:
                c.drawString(ex + 3, ey + 2, label_text)
                c.setFont(FONT_DEFAULT, 7)
                c.drawString(ex + 3, ey - 8, pct_text)
            else:
                label_width = c.stringWidth(label_text, FONT_BOLD, 8)
                pct_width = c.stringWidth(pct_text, FONT_DEFAULT, 7)
                c.drawString(ex - 3 - label_width, ey + 2, label_text)
                c.setFont(FONT_DEFAULT, 7)
                c.drawString(ex - 3 - pct_width, ey - 8, pct_text)

    # Section 3: Trend graph
    c.rect(x0+w_left, y_sec2, w_right, h2)
    c.setFont(FONT_BOLD,12); c.setFillColor(colors.black)
    c.drawCentredString(x0+w_left+w_right/2, y_sec2+h2-15, tr('production_rates_title', lang))
    
    # Your existing trend graph code here
    all_t, mx, series = [], 0, []
    
    for m in machines:
        fp = os.path.join(csv_parent_dir, m, 'last_24h_metrics.csv')
        if os.path.isfile(fp):
            try:
                df = df_processor.safe_read_csv(fp, parse_dates=['timestamp'])
                if 'capacity' in df.columns and 'timestamp' in df.columns and not df.empty:
                    valid_data = df.dropna(subset=['timestamp', 'capacity'])
                    if not valid_data.empty:
                        t = valid_data['timestamp']
                        capacity_vals = valid_data['capacity']
                        if not all_t:
                            base_time = t.min()
                        else:
                            base_time = min(min(all_t), t.min())
                        pts = [((ts - base_time).total_seconds() / 3600.0, float(v)) 
                               for ts, v in zip(t, capacity_vals)]
                        series.append((m, pts))
                        all_t.extend(t)
                        mx = max(mx, capacity_vals.max())
            except Exception as e:
                logger.error(f"Error processing trend data for machine {m}: {e}")
    
    # Draw the trend graph
    tp=10; bw, bh=w_right-2*tp, h2-2*tp
    tw,th=bw*0.68*1.1,bh*0.68*1.1; sl=w_right*0.05
    gx=x0+w_left+tp+(bw-tw)/2-sl; gy=y_sec2+tp+(bh-th)/2
    
    if series:
        dln=Drawing(tw,th); lp=LinePlot()
        lp.x=lp.y=0; lp.width=tw; lp.height=th; 
        lp.data=[pts for _,pts in series]
        
        cols=[colors.blue,colors.red,colors.green,colors.orange,colors.purple]
        for i in range(len(series)): 
            if i < len(cols):
                lp.lines[i].strokeColor=cols[i]; 
                lp.lines[i].strokeWidth=1.5
        
        xs=[x for _,pts in series for x,_ in pts]
        if xs:
            lp.xValueAxis.valueMin,lp.xValueAxis.valueMax=min(xs),max(xs)
            step=(max(xs)-min(xs))/6 if max(xs) > min(xs) else 1
            lp.xValueAxis.valueSteps=[min(xs)+j*step for j in range(7)]
            
            if all_t:
                base_time = min(all_t)
                lp.xValueAxis.labelTextFormat=lambda v:(base_time+timedelta(hours=v)).strftime('%H:%M')
                lp.xValueAxis.labels.angle,lp.xValueAxis.labels.boxAnchor=45,'n'
        
        lp.yValueAxis.valueMin,lp.yValueAxis.valueMax=0,mx*1.1 if mx else 1
        lp.yValueAxis.valueSteps=None
        dln.add(lp); renderPDF.draw(dln,c,gx,gy)
        
        # Draw legend
        lx,ly=gx+tw+10,y_sec2+h2-30
        for idx,(m,_) in enumerate(series):
            if idx < len(cols):
                c.setStrokeColor(cols[idx]); c.setLineWidth(2)
                yL=ly-idx*15; c.line(lx,yL,lx+10,yL)
                c.setFont(FONT_DEFAULT,8); c.setFillColor(colors.black)
                c.drawString(lx+15,yL-3,f"{tr('machine_label', lang)} {m}")
    else:
        c.setFont(FONT_DEFAULT,12); c.setFillColor(colors.gray)
        c.drawCentredString(x0+w_left+w_right/2, y_sec2+h2/2, tr('no_data_available', lang))

    # Section 4: Counts
    y_sec4 = y_sec2 - h4 - spacing_gap
    c.rect(x0, y_sec4, total_w, h4)
    c.setFillColor(colors.HexColor('#1f77b4')); c.rect(x0, y_sec4, total_w, h4, fill=1, stroke=0)
    
    total_objs = total_objects
    total_rem = total_removed
    
    c.setFillColor(colors.white); c.setFont(FONT_BOLD,10)
    c.drawString(x0+10, y_sec4+h4-14, tr('counts_title', lang))
    labs4=[
        tr('total_objects_processed_label', lang),
        tr('total_impurities_removed_label', lang),
    ]
    vals4=[f"{int(total_objs):,}",f"{int(total_rem):,}"]
    half=total_w/2; c.setFont(FONT_BOLD,12)
    for i,lab in enumerate(labs4):
        lw=c.stringWidth(lab,FONT_BOLD,12)
        c.drawString(x0+half*i+(half-lw)/2,y_sec4+h4/2+8,lab)
    c.setFont(FONT_BOLD, COUNT_VALUE_FONT_SIZE)
    for i, val in enumerate(vals4):
        vw = c.stringWidth(val, FONT_BOLD, COUNT_VALUE_FONT_SIZE)
        c.drawString(
            x0 + half * i + (half - vw) / 2,
            y_sec4 + h4 / 2 - COUNT_VALUE_FONT_SIZE,
            val,
        )
    c.setFillColor(colors.black)
    c.setStrokeColor(colors.black)
    c.rect(x0, y_sec4, total_w, h4)
    
    # Return the Y position where the next content should start
    return y_sec4 - spacing_gap

def calculate_global_max_firing_average(csv_parent_dir, machines=None, *, is_lab_mode: bool = False):
    """Calculate the global maximum firing value.

    When ``machines`` is provided, only those machine IDs are considered.
    In lab mode the maximum is based on total counts rather than averages.
    """
    if machines is None:
        machines = sorted(
            d
            for d in os.listdir(csv_parent_dir)
            if os.path.isdir(os.path.join(csv_parent_dir, d)) and d.isdigit()
        )

    global_max = 0

    for machine in machines:
        fp = os.path.join(csv_parent_dir, machine, 'last_24h_metrics.csv')
        if os.path.isfile(fp):
            try:
                df = df_processor.safe_read_csv(fp)
                settings_data = load_machine_settings(csv_parent_dir, machine)
                ts = df.get('timestamp') if is_lab_mode else None
                # Find counter values for this machine
                for i in range(1, 13):
                    col_name = next((c for c in df.columns if c.lower() == f'counter_{i}'), None)
                    if not col_name or col_name not in df.columns:
                        continue
                    if is_lab_mode:
                        assigned_val = _lookup_setting(
                            settings_data,
                            f"Settings.ColorSort.Primary{i}.IsAssigned",
                            True,
                        )
                        if not _bool_from_setting(assigned_val):
                            continue
                        val = last_value_scaled(df[col_name], 60)
                    else:
                        val = df[col_name].mean()
                    if not pd.isna(val):
                        global_max = max(global_max, val)
            except Exception as e:
                logger.error(f"Error calculating max for machine {machine}: {e}")
    
    return global_max


def enhanced_calculate_stats_for_machine(csv_parent_dir, machine, *, is_lab_mode=False):
    """Return aggregated production stats for a machine.

    This helper reads ``last_24h_metrics.csv`` for ``machine`` using
    :func:`df_processor.safe_read_csv` and computes totals for common metrics.
    ``df_processor.process_with_cleanup`` is used when running the calculation
    helpers to ensure any temporary pandas objects are cleaned up.

    When running in lab mode, counters for sensitivities whose
    ``IsAssigned`` flag is false are ignored so that inactive sensitivities do
    not contribute to the totals.
    """
    fp = os.path.join(csv_parent_dir, str(machine), "last_24h_metrics.csv")
    if not os.path.isfile(fp):
        return {
            "capacity_lbs": 0,
            "accepts_lbs": 0,
            "rejects_lbs": 0,
            "objects": 0,
            "removed": 0,
            "running_mins": 0,
            "stopped_mins": 0,
        }

    df = df_processor.safe_read_csv(fp)
    if df.empty:
        return {
            "capacity_lbs": 0,
            "accepts_lbs": 0,
            "rejects_lbs": 0,
            "objects": 0,
            "removed": 0,
            "running_mins": 0,
            "stopped_mins": 0,
        }

    ts = df.get("timestamp") if is_lab_mode else None
    settings_data = load_machine_settings(csv_parent_dir, machine)

    def calc_cap(series):
        return calculate_total_capacity_from_csv_rates(series, timestamps=ts, is_lab_mode=is_lab_mode)

    def calc_obj(series):
        return calculate_total_objects_from_csv_rates(series, timestamps=ts, is_lab_mode=is_lab_mode)

    capacity_total = 0
    if "capacity" in df.columns:
        capacity_total = df_processor.process_with_cleanup(df["capacity"], calc_cap)["total_capacity_lbs"]

    accepts_total = 0
    ac_col = next((c for c in df.columns if c.lower() == "accepts"), None)
    if ac_col:
        accepts_total = df_processor.process_with_cleanup(df[ac_col], calc_cap)["total_capacity_lbs"]

    rejects_total = 0
    rj_col = next((c for c in df.columns if c.lower() == "rejects"), None)
    if rj_col:
        rejects_total = df_processor.process_with_cleanup(df[rj_col], calc_cap)["total_capacity_lbs"]

    objects_total = 0
    if "objects_per_min" in df.columns:
        objects_total = df_processor.process_with_cleanup(df["objects_per_min"], calc_obj)["total_objects"]

    removed_total = 0
    for i in range(1, 13):
        col = next((c for c in df.columns if c.lower() == f"counter_{i}"), None)
        if not col:
            continue

        if is_lab_mode:
            assigned_val = _lookup_setting(
                settings_data,
                f"Settings.ColorSort.Primary{i}.IsAssigned",
                True,
            )
            if not _bool_from_setting(assigned_val):
                continue

        removed_total += df_processor.process_with_cleanup(df[col], calc_obj)["total_objects"]

    running_mins = df.get("running", pd.Series(dtype=float)).sum() if "running" in df.columns else 0
    stopped_mins = df.get("stopped", pd.Series(dtype=float)).sum() if "stopped" in df.columns else 0

    return {
        "capacity_lbs": capacity_total,
        "accepts_lbs": accepts_total,
        "rejects_lbs": rejects_total,
        "objects": objects_total,
        "removed": removed_total,
        "running_mins": running_mins,
        "stopped_mins": stopped_mins,
    }


def load_machine_settings(csv_parent_dir, machine):
    """Load machine settings from a JSON file if available."""
    path = os.path.join(csv_parent_dir, str(machine), "settings.json")
    if os.path.isfile(path):
        try:




            with open(path) as f:
                return json.load(f)
        except Exception as exc:
            logger.warning(f"Unable to read settings for machine {machine}: {exc}")
    return {}







def draw_machine_settings_section(c, x0, y0, total_w, section_h, settings, *, lang="en"):
    """Draw a 6x6 grid of machine settings with merged cells."""


    c.saveState()

    rows, cols = 6, 6
    row_h = section_h / rows
    col_w = total_w / cols

    get = lambda key: _lookup_setting(settings, key)

    data = [
        [tr('machine_settings_title', lang), "", "Calibration", "", "", ""],
        [
            "Preset:",
            get("Status.Info.PresetName"),
            "Product Lights Target Values",
            "",
            "Background:",
            "",
        ],
        [
            "Ejector Dwell:",
            get("Settings.Ejectors.PrimaryDwell"),
            "R:",
            get("Settings.Calibration.FrontProductRed"),
            "R:",
            get("Settings.Calibration.FrontBackgroundRed"),
        ],
        [
            "Pixel Overlap:",
            get("Settings.Ejectors.PixelOverlap"),
            "G:",
            get("Settings.Calibration.FrontProductGreen"),
            "G:",
            get("Settings.Calibration.FrontBackgroundGreen"),
        ],
        [
            "Non Object Band:",
            get("Settings.Calibration.NonObjectBand"),
            "B:",
            get("Settings.Calibration.FrontProductBlue"),
            "B:",
            get("Settings.Calibration.FrontBackgroundBlue"),
        ],
        [
            "Ejector Delay:",
            get("Settings.Ejectors.PrimaryDelay"),
            "LED Drive %:",
            get("Settings.Calibration.LedDriveForGain"),
            "Erosion:",
            get("Settings.ColorSort.Config.Erosion"),
        ],
    ]

    # Cell merge definitions: (row, col) -> (rowspan, colspan)
    merges = {
        (0, 0): (1, 2),  # "Machine Settings" spanning two columns
        (0, 2): (1, 4),  # "Calibration" spanning remaining columns
        (1, 2): (1, 2),  # "Product Lights Target Values" header
        (1, 4): (1, 2),  # "Background" header
    }



    # Map each cell to the start of its merge region
    merged_to = {}

    for (r, c_idx), (rs, cs) in merges.items():
        for rr in range(r, r + rs):
            for cc in range(c_idx, c_idx + cs):
                merged_to[(rr, cc)] = (r, c_idx)


    # Draw base grid
    c.setStrokeColor(colors.black)
    for i in range(rows + 1):
        c.line(x0, y0 + i * row_h, x0 + total_w, y0 + i * row_h)
    for j in range(cols + 1):

        c.line(x0 + j * col_w, y0, x0 + j * col_w, y0 + section_h)


    # Overlay merged cell rectangles to hide interior lines

    for (r, c_idx), (rs, cs) in merges.items():
        x = x0 + c_idx * col_w
        y = y0 + section_h - (r + rs) * row_h
        w = cs * col_w
        h = rs * row_h
        c.setFillColor(colors.white)
        c.rect(x, y, w, h, fill=1, stroke=0)
        c.setStrokeColor(colors.black)
        c.rect(x, y, w, h, fill=0, stroke=1)

    # Ensure subsequent text renders in black
    c.setFillColor(colors.black)


    # Draw cell text with optional blue background for missing values

    for r, row in enumerate(data):
        for j, cell in enumerate(row):
            if merged_to.get((r, j), (r, j)) != (r, j):
                # Skip cells that are part of a merge but not the top-left
                continue
            rs, cs = merges.get((r, j), (1, 1))
            x = x0 + j * col_w
            y = y0 + section_h - (r + rs) * row_h
            w = cs * col_w
            h = rs * row_h
            if r == 2 and j == 1:  # Ejector Dwell cell (PrimaryDwell)
                try:
                    # Format the Ejector Dwell to 1 decimal place
                    text = f"{float(cell):.1f}" if cell not in {"N/A", "", "None", None} else str(cell)
                except (ValueError, TypeError):
                    text = str(cell)
            else:
                text = str(cell)

            is_data_cell = r >= 1 and j % 2 == 1
            fill_color = colors.white if is_data_cell else colors.lightblue
            if is_data_cell and text in {"N/A", "", "None"}:
                fill_color = colors.lightblue

            c.setFillColor(fill_color)
            c.rect(x, y, w, h, fill=1, stroke=0)
            c.setStrokeColor(colors.black)
            c.rect(x, y, w, h, fill=0, stroke=1)

            c.setFillColor(colors.black)
            tx = x + 2
            ty = y + h - 8
            if r == 0 or j % 2 == 0:
                c.setFont(FONT_BOLD, 6)
            else:
                c.setFont(FONT_DEFAULT, 6)
            c.drawString(tx, ty, text)

    c.restoreState()


def _bool_from_setting(val):
    """Return True if setting value represents a true value."""
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "yes", "on"}
    return bool(val)



def draw_sensitivity_grid(
    c,
    x0,
    y0,
    total_w,
    section_h,
    settings,
    primary_num,
    *,
    counter_value=None,
    lang="en",
    is_lab_mode=False,
    border_color=colors.black,
):
    """Draw a grid of settings for a single sensitivity.

    When ``is_lab_mode`` is ``True`` a new column is added on the left that
    spans all rows and displays the sample image for the sensitivity.
    """

    try:
        c.saveState()

        rows = 5
        base_cols = 8
        extra_image_col = 1 if is_lab_mode else 0
        extra_removed_col = 1 if is_lab_mode else 0
        cols = base_cols + extra_image_col + extra_removed_col
        row_h = section_h / rows
        col_w = total_w / cols

        get = lambda key: _lookup_setting(settings, key)
        #print(f"DEBUG Primary{p}: Full settings dump = {settings}")
        p = primary_num

        sample_image = get(f"Settings.ColorSort.Primary{p}.SampleImage")
        
        # Get the type value to check if it's "Grid"
        type_val = get(f"Settings.ColorSort.Primary{p}.TypeId")
        is_grid_type = str(type_val) != "0"  # 0 = Ellipsoid, anything else = Grid
        
        # Get axis wave values for position text logic
        position_text = "Location:"  # Default
        try:
            x_axis_wave = get(f"Settings.ColorSort.Primary{p}.XAxisWave")
            y_axis_wave = get(f"Settings.ColorSort.Primary{p}.YAxisWave")
            z_axis_wave = get(f"Settings.ColorSort.Primary{p}.ZAxisWave")
            print(f"DEBUG Primary{p}: x_axis_wave={x_axis_wave} (type: {type(x_axis_wave)})")
            print(f"DEBUG Primary{p}: y_axis_wave={y_axis_wave} (type: {type(y_axis_wave)})")
            print(f"DEBUG Primary{p}: z_axis_wave={z_axis_wave} (type: {type(z_axis_wave)})")
            print(f"DEBUG Primary{p}: is_grid_type={is_grid_type}, type_val={type_val}")

            # Determine position text based on axis wave values
            if x_axis_wave is not None and y_axis_wave is not None and z_axis_wave is not None:
                x_val = int(float(x_axis_wave))
                y_val = int(float(y_axis_wave))
                z_val = int(float(z_axis_wave))

                if x_val == 9 and y_val == 7 and z_val == 8:
                    position_text = "Top Right"
                elif x_val == 8 and y_val == 7 and z_val == 9:
                    position_text = "Top Left"
                elif x_val == 8 and y_val == 9 and z_val == 7:
                    position_text = "Bottom"
        except (ValueError, TypeError, Exception):
            # If anything fails, keep default "Location:"
            pass

        if is_lab_mode:
            # Modify labels based on Grid type
            x_label = "" if is_grid_type else "X"
            y_label = "" if is_grid_type else "Y" 
            z_label = "" if is_grid_type else "Z"
            
            first_row = [
                {"image": sample_image},
                f"Sensitivity: {p}",
                "",
                "",
                x_label,
                y_label,
                z_label,
                "And Mode:",
                get(f"Settings.ColorSort.Primary{p}.FrontAndRearLogic"),
                "Total Removed",
            ]
        else:
            # Modify labels based on Grid type (lowercase for non-lab mode)
            x_label = "" if is_grid_type else "x"
            y_label = "" if is_grid_type else "y"
            z_label = "" if is_grid_type else "z"
            
            first_row = [
                f"Sensitivity: {p}",
                sample_image,
                "",
                x_label,
                y_label,
                z_label,
                "And Mode:",
                get(f"Settings.ColorSort.Primary{p}.FrontAndRearLogic"),
            ]

        row_prefix = [""] if is_lab_mode else []

        data = [
            first_row,
            [
                *row_prefix,
                "Name:",
                get(f"Settings.ColorSort.Primary{p}.Name"),
                "Position:",
                position_text if is_grid_type else get(f"Settings.ColorSort.Primary{p}.EllipsoidCenterX"),  # Grid position text or X center
                "" if is_grid_type else get(f"Settings.ColorSort.Primary{p}.EllipsoidCenterY"),  # Blank if Grid or Y center
                "" if is_grid_type else get(f"Settings.ColorSort.Primary{p}.EllipsoidCenterZ"),  # Blank if Grid or Z center
                "Ej. Delay Offset:",
                get(f"Settings.ColorSort.Primary{p}.EjectorDelayOffset"),
                int(counter_value) if counter_value is not None else 0,
            ],
            [
                *row_prefix,
                "Area/Spot Size:",
                get(f"Settings.ColorSort.Primary{p}.AreaSize"),
                "" if is_grid_type else "Size:",  # Blank if Grid type
                "" if is_grid_type else get(f"Settings.ColorSort.Primary{p}.EllipsoidAxisLengthX"),  # Blank if Grid
                "" if is_grid_type else get(f"Settings.ColorSort.Primary{p}.EllipsoidAxisLengthY"),  # Blank if Grid
                "" if is_grid_type else get(f"Settings.ColorSort.Primary{p}.EllipsoidAxisLengthZ"),  # Blank if Grid
                "Ej. Offset:",
                get(f"Settings.ColorSort.Primary{p}.EjectorDwellOffset"),
                "",
            ],
            [
                *row_prefix,
                "Type:",
                ("Ellipsoid" if str(type_val) == "0" else "Grid"),
                "Angle:",
                get(f"Settings.ColorSort.Primary{p}.PlaneAngle") if is_grid_type else get(f"Settings.ColorSort.Primary{p}.EllipsoidRotationX"),  # PlaneAngle for Grid, RotationX for Ellipsoid
                "" if is_grid_type else get(f"Settings.ColorSort.Primary{p}.EllipsoidRotationY"),  # Blank if Grid
                "" if is_grid_type else get(f"Settings.ColorSort.Primary{p}.EllipsoidRotationZ"),  # Blank if Grid
                "",
                "",
                "",
            ],
            [
                *row_prefix,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
        ]

        merges = {(0, 0): (rows, 1)} if is_lab_mode else {}
        if is_lab_mode:
            merges[(1, cols - 1)] = (rows - 1, 1)

        merged_to = {}

        for (r, c_idx), (rs, cs) in merges.items():
            for rr in range(r, r + rs):
                for cc in range(c_idx, c_idx + cs):
                    merged_to[(rr, cc)] = (r, c_idx)

        c.setStrokeColor(colors.black)
        for i in range(rows + 1):
            c.line(x0, y0 + i * row_h, x0 + total_w, y0 + i * row_h)
        for j in range(cols + 1):
            c.line(x0 + j * col_w, y0, x0 + j * col_w, y0 + section_h)

        for r, row in enumerate(data):
            for j, cell in enumerate(row):
                if merged_to.get((r, j), (r, j)) != (r, j):
                    continue
                rs, cs = merges.get((r, j), (1, 1))
                x = x0 + j * col_w
                y = y0 + section_h - (r + rs) * row_h
                w = cs * col_w
                h = rs * row_h
                text = str(cell)

                offset = 1 if is_lab_mode else 0
                is_image_cell = is_lab_mode and j == 0

                # Define which cells contain tag data for lab mode
                if is_lab_mode:
                    # Check if this cell contains tag data (should be white)
                    is_data_cell = False
                    if r == 0 and j == 8:  # FrontAndRearLogic
                        is_data_cell = True
                    elif r == 1 and j in [2, 8]:  # Name, EjectorDelayOffset
                        is_data_cell = True
                    elif r == 1 and j == 4 and is_grid_type:  # Position text for Grid
                        is_data_cell = True
                    elif r == 1 and not is_grid_type and j in [4, 5, 6]:  # CenterX/Y/Z for Ellipsoid
                        is_data_cell = True
                    elif r == 2 and j in [2, 8]:  # AreaSize, EjectorDwellOffset
                        is_data_cell = True
                    elif r == 2 and not is_grid_type and j in [4, 5, 6]:  # AxisLengthX/Y/Z for Ellipsoid
                        is_data_cell = True
                    elif r == 3 and j == 2:  # Type value
                        is_data_cell = True
                    elif r == 3 and j == 4:  # PlaneAngle for Grid OR RotationX for Ellipsoid
                        is_data_cell = True
                    elif r == 3 and not is_grid_type and j in [5, 6]:  # RotationY/Z for Ellipsoid only
                        is_data_cell = True
                    elif r >= 1 and j == cols - 1:
                        is_data_cell = True
                else:
                    # Original logic for non-lab mode
                    is_data_cell = (j - offset) % 2 == 1 if j >= offset else False

                fill_color = colors.white if is_data_cell or is_image_cell else colors.lightblue
                
                if is_data_cell and text in {"N/A", "", "None"}:
                    fill_color = colors.lightblue

                c.setFillColor(fill_color)
                c.rect(x, y, w, h, fill=1, stroke=0)
                c.setStrokeColor(colors.black)
                c.rect(x, y, w, h, fill=0, stroke=1)

                if is_image_cell and isinstance(cell, dict) and "image" in cell:
                    image_data = cell["image"]
                    try:
                        img_bytes = base64.b64decode(image_data)
                        pil_image = Image.open(io.BytesIO(img_bytes))
                        
                        # Handle transparency by creating white background
                        if pil_image.mode in ('RGBA', 'LA'):
                            white_bg = Image.new('RGBA', pil_image.size, (255, 255, 255, 255))
                            final_image = Image.alpha_composite(white_bg, pil_image)
                            final_image = final_image.convert('RGB')
                        else:
                            final_image = pil_image
                        
                        # Save the processed image to bytes
                        processed_bytes = io.BytesIO()
                        final_image.save(processed_bytes, format='PNG')
                        processed_bytes.seek(0)
                        
                        img_reader = ImageReader(processed_bytes)
                        c.drawImage(img_reader, x, y, width=w, height=h, preserveAspectRatio=True, anchor='c')
                    except Exception:
                        c.setFillColor(colors.black)
                        c.setFont(FONT_DEFAULT, 6)
                        c.drawString(x + 2, y + h - 8, "No Image")
                else:
                    c.setFillColor(colors.black)
                    tx = x + 2
                    if r >= 1 and j == cols - 1:
                        c.setFont(FONT_BOLD, SENSITIVITY_VALUE_FONT_SIZE)
                        vw = pdfmetrics.stringWidth(
                            text, FONT_BOLD, SENSITIVITY_VALUE_FONT_SIZE
                        )
                        tx = x + (w - vw) / 2
                        ty = y + (h - SENSITIVITY_VALUE_FONT_SIZE) / 2

                    else:
                        ty = y + h - 8
                        if (j - offset) % 2 == 0:
                            c.setFont(FONT_BOLD, 6)
                        else:
                            c.setFont(FONT_DEFAULT, 6)
                    c.drawString(tx, ty, text)

    except Exception as e:
        # Log the error but don't let it crash the report generation
        print(f"Error in draw_sensitivity_grid: {e}")
    finally:
        # Always restore state even if there was an error
        try:
            # Draw colored border around entire section
            c.setStrokeColor(border_color)
            c.rect(x0, y0, total_w, section_h, fill=0, stroke=1)
        except Exception:
            pass
        try:
            c.restoreState()
        except:
            # If restoreState fails, there's not much we can do
            pass


def draw_sensitivity_sections(
    c,
    x0,
    y_start,
    total_w,
    section_h,
    settings,
    *,
    counter_values=None,
    lang="en",
    is_lab_mode=False,
    width=None,
    height=None,
    lab_test_name: str | None = None,
    bar_colors=BAR_COLORS,
):
    """Draw grids for all active sensitivities and return new y position."""
    spacing = 10
    current_y = y_start
    width = width or (c._pagesize[0] if c else letter[0])
    height = height or (c._pagesize[1] if c else letter[1])

    active_indices = []
    for i in range(1, 13):
        active_val = _lookup_setting(
            settings, f"Settings.ColorSort.Primary{i}.IsAssigned", False
        )
        if _bool_from_setting(active_val):
            active_indices.append(i)

    for idx, i in enumerate(active_indices):
        if (
            is_lab_mode
            and idx == 5
            and len(active_indices) > 5
            and c is not None
        ):
            # Start a new page after the first five sections in lab mode
            c.showPage()
            page_number = c.getPageNumber()
            content_start_y = draw_header(
                c,
                width,
                height,
                page_number,
                lang=lang,
                is_lab_mode=is_lab_mode,
                lab_test_name=lab_test_name,
            )
            current_y = content_start_y

        y_grid = current_y - section_h
        draw_sensitivity_grid(
            c,
            x0,
            y_grid,
            total_w,
            section_h,
            settings,
            i,
            counter_value=counter_values.get(i) if counter_values else None,
            lang=lang,
            is_lab_mode=is_lab_mode,

            border_color=bar_colors[idx % len(bar_colors)] if bar_colors else colors.black,

        )
        current_y = y_grid - spacing

    return current_y





def generate_report_filename(script_dir):
    """Generate date-stamped filename for the report"""
    # Get current date
    current_date = datetime.datetime.now()
    
    # Format: EnpresorReport_M_D_YYYY.pdf
    date_stamp = current_date.strftime('%m_%d_%Y')
    filename = f"EnpresorReport_{date_stamp}.pdf"
    
    # Create full path
    pdf_path = os.path.join(script_dir, filename)
    
    logger.debug(f"Generated filename: {filename}")
    logger.debug(f"Full path: {pdf_path}")

    return pdf_path


def fetch_last_24h_metrics(export_dir: str = METRIC_EXPORT_DIR):
    """Return the last 24 hours of metrics for all machines.

    This is a thin wrapper around :func:`hourly_data_saving.get_historical_data`
    that iterates over the machine directories found in ``export_dir``.
    """
    if not os.path.isdir(export_dir):
        return {}

    metrics = {}
    for machine in sorted(os.listdir(export_dir)):
        machine_path = os.path.join(export_dir, machine)
        if os.path.isdir(machine_path):
            metrics[machine] = get_historical_data(
                "24h", export_dir=export_dir, machine_id=machine
            )

    return metrics


def build_report(
    metrics: dict,
    pdf_path: str,
    *,
    use_optimized: bool = False,
    export_dir: str = METRIC_EXPORT_DIR,
    machines: list | None = None,
    include_global: bool = True,
    is_lab_mode: bool = False,
    values_in_kg: bool = False,
    lang: str = "en",
    lab_test_name: str | None = None,
) -> None:
    """Generate a PDF report and write it to ``pdf_path``.

    ``metrics`` is currently unused but retained for compatibility with
    :func:`fetch_last_24h_metrics`.
    """

    if use_optimized:
        draw_layout_optimized(
            pdf_path,
            export_dir,
            machines=machines,
            include_global=include_global,
            lang=lang,
            is_lab_mode=is_lab_mode,
            values_in_kg=values_in_kg,
            lab_test_name=lab_test_name,
        )
    else:
        draw_layout_standard(
            pdf_path,
            export_dir,
            machines=machines,
            include_global=include_global,
            lang=lang,
            is_lab_mode=is_lab_mode,
            values_in_kg=values_in_kg,
            lab_test_name=lab_test_name,
        )

def draw_machine_sections(
    c,
    csv_parent_dir,
    machine,
    x0,
    y_start,
    total_w,
    available_height,
    global_max_firing=None,
    *,
    is_lab_mode=False,
    lang="en",
    values_in_kg=False,
    width=None,
    height=None,
    lab_test_name: str | None = None,
):
    width = width or (c._pagesize[0] if c else letter[0])
    height = height or (c._pagesize[1] if c else letter[1])
   
    logger.warning(f"DEBUG MACHINE SECTIONS: machine={machine}, is_lab_mode={is_lab_mode}")

    """Draw the three sections for a single machine - OPTIMIZED FOR 2 MACHINES PER PAGE"""
    fp = os.path.join(csv_parent_dir, machine, 'last_24h_metrics.csv')
    if not os.path.isfile(fp):
        return y_start  # Return same position if no data
    
    try:
        df = df_processor.safe_read_csv(fp)
    except Exception as e:
        logger.error(f"Error reading data for machine {machine}: {e}")
        return y_start

    settings_data = load_machine_settings(csv_parent_dir, machine)
    
    # OPTIMIZED DIMENSIONS FOR 2 MACHINES PER PAGE
    w_left = total_w * 0.4
    w_right = total_w * 0.6
    
    # Height allocation optimized for 2 machines
    pie_height = available_height * 0.75      # 35% for pie chart
    bar_height = available_height * 0.75      # 35% for bar chart
    counts_height = available_height * 0.30   # 25% for counts (REDUCED!)
    trend_height = available_height * 0.75            # Trend graph same height as counts
    spacing = 1  # Reduced spacing
    
    current_y = y_start
    
    # Section 1: Machine pie chart (left side)
    y_pie = current_y - pie_height
    ac_col = next((c for c in df.columns if c.lower()=='accepts'), None)
    rj_col = next((c for c in df.columns if c.lower()=='rejects'), None)
    run_col = next((c for c in df.columns if c.lower()=='running'), None)
    stop_col = next((c for c in df.columns if c.lower()=='stopped'), None)

    a_val = df[ac_col].sum() if ac_col else 0
    r_val = df[rj_col].sum() if rj_col else 0

    # In lab mode use the counters for rejects
    if is_lab_mode:
        r_val = 0
        for i in range(1, 13):
            col = next((c for c in df.columns if c.lower() == f"counter_{i}"), None)
            if not col:
                continue
            assigned_val = _lookup_setting(
                settings_data,
                f"Settings.ColorSort.Primary{i}.IsAssigned",
                True,
            )
            if not _bool_from_setting(assigned_val):
                continue
            r_val += df[col].sum()

    run_total = df[run_col].sum() if run_col else 0
    stop_total = df[stop_col].sum() if stop_col else 0

    # Calculate total objects processed and removed counts for percentages
    machine_objs = 0
    if 'objects_60M' in df.columns and is_lab_mode:
        machine_objs = last_value_scaled(df['objects_60M'], 60)
    elif 'objects_per_min' in df.columns:
        if is_lab_mode:
            machine_objs = last_value_scaled(df['objects_per_min'], 60)
        else:
            # Live mode: Use integration method
            obj_stats = calculate_total_objects_from_csv_rates(
                df['objects_per_min'],
                timestamps=df['timestamp'] if is_lab_mode else None,
                is_lab_mode=is_lab_mode,
            )
            machine_objs = obj_stats['total_objects']
    elif is_lab_mode:
        ac_tot = last_value_scaled(df[ac_col], 60) if ac_col else 0
        rj_tot = last_value_scaled(df[rj_col], 60) if rj_col else 0
        machine_objs = ac_tot + rj_tot

    machine_rem = 0
    sensitivity_counts = {}
    for idx in range(1, 13):
        col = next((c for c in df.columns if c.lower() == f'counter_{idx}'), None)
        if not col:
            continue
        
        # Apply active sensitivity filtering for BOTH lab and live mode
        assigned_val = _lookup_setting(
            settings_data,
            f"Settings.ColorSort.Primary{idx}.IsAssigned",
            True,
        )
        if not _bool_from_setting(assigned_val):
            continue
        
        # Use appropriate calculation method based on mode
        if is_lab_mode:
            cnt_val = last_value_scaled(df[col], 60)
        else:
            # Live mode: Use integration method
            c_stats = calculate_total_objects_from_csv_rates(
                df[col],
                timestamps=df['timestamp'] if is_lab_mode else None,
                is_lab_mode=is_lab_mode,
            )
            cnt_val = c_stats['total_objects']
        
        machine_rem += cnt_val
        sensitivity_counts[idx] = cnt_val
    
    # Draw pie chart section border
    c.setStrokeColor(colors.black)
    c.rect(x0, y_pie, w_left, pie_height)
    
    # Pie chart title
    title_pie = f"{tr('machine_label', lang)} {machine}"
    c.setFont(FONT_BOLD, 10)  # Smaller font
    c.setFillColor(colors.black)
    c.drawCentredString(x0 + w_left/2, y_pie + pie_height - 12, title_pie)
    
    if a_val > 0 or r_val > 0:
        # Draw pie chart with reduced padding
        pad, lh = 6, 12  # Reduced padding and label height
        aw, ah = w_left - 2*pad, pie_height - 2*pad - lh
        psz = min(aw, ah) * 0.7  # Slightly smaller pie
        px = x0 + pad + (aw - psz)/2
        py = y_pie + pad + lh + (ah - psz)/2
        
        d_pie = Drawing(psz, psz)
        p_pie = Pie()
        p_pie.x = p_pie.y = 0
        p_pie.width = p_pie.height = psz
        p_pie.startAngle = -30
        p_pie.direction = 'clockwise'
        accept_obj = machine_objs - machine_rem
        reject_obj = machine_rem
        p_pie.data = [accept_obj, reject_obj]
        p_pie.slices[0].fillColor = colors.green
        p_pie.slices[1].fillColor = colors.red
        p_pie.sideLabels = False
        d_pie.add(p_pie)
        
        c.saveState()
        c.translate(px + psz/2, py + psz/2)
        c.rotate(-30)
        renderPDF.draw(d_pie, c, -psz/2, -psz/2)
        c.restoreState()
        
        # Add labels with smaller fonts
        total_pie = machine_objs
        if total_pie > 0:
            accept_obj = machine_objs - machine_rem
            reject_obj = machine_rem
            percentages = [(accept_obj/total_pie)*100, (reject_obj/total_pie)*100]
            angles = [180+-59 + (360*((reject_obj/total_pie)*100)/2/100), -59 + (360*((reject_obj/total_pie)*100)/2/100)]
            labels = [tr('accepts', lang), tr('rejects', lang)]
            
            for i, (label, pct, angle) in enumerate(zip(labels, percentages, angles)):
                angle_rad = math.radians(angle)
                radius = psz/2 * 0.9
                cx = px + psz/2 + math.cos(angle_rad) * radius
                cy = py + psz/2 + math.sin(angle_rad) * radius
                ex = cx + math.cos(angle_rad) * 15  # Shorter line
                ey = cy + math.sin(angle_rad) * 15
                
                c.setStrokeColor(colors.black)
                c.setLineWidth(1)
                c.line(cx, cy, ex, ey)
                
                c.setFont(FONT_BOLD, 7)  # Smaller font
                c.setFillColor(colors.black)
                label_text = f"{label}"
                pct_text = f"{pct:.1f}%"
                
                if math.cos(angle_rad) >= 0:
                    c.drawString(ex + 2, ey + 1, label_text)
                    c.setFont(FONT_DEFAULT, 6)
                    c.drawString(ex + 2, ey - 6, pct_text)
                else:
                    label_width = c.stringWidth(label_text, FONT_BOLD, 7)
                    pct_width = c.stringWidth(pct_text, FONT_DEFAULT, 6)
                    c.drawString(ex - 2 - label_width, ey + 1, label_text)
                    c.setFont(FONT_DEFAULT, 6)
                    c.drawString(ex - 2 - pct_width, ey - 6, pct_text)
    else:
        c.setFont(FONT_DEFAULT, 8)
        c.setFillColor(colors.gray)
        c.drawCentredString(x0 + w_left/2, y_pie + pie_height/2, tr('no_data_available', lang))

    # Display runtime and stop time below the pie chart
    runtime_text = (
        f"{tr('run_time_label', lang)} {_minutes_to_hm(run_total)}  "
        f"{tr('stop_time_label', lang)} {_minutes_to_hm(stop_total)}"
    )
    c.setFont(FONT_DEFAULT, 8)
    c.setFillColor(colors.black)
    c.drawCentredString(x0 + w_left/2, y_pie + 4, runtime_text)

    # Section 2: Bar chart (right side) - IMPROVED VERSION
    c.setStrokeColor(colors.black)
    c.rect(x0 + w_left, y_pie, w_right, bar_height)
    
    title_key = 'sensitivity_firing_total_title' if is_lab_mode else 'sensitivity_percentage_title'
    title_bar = f"{tr('machine_label', lang)} {machine} - {tr(title_key, lang)}"
    c.setFont(FONT_BOLD, 12)  # Increased from 9 to 12
    c.setFillColor(colors.black)
    c.drawCentredString(x0 + w_left + w_right/2, y_pie + bar_height - 10, title_bar)
    
    # Draw bar chart with counter values
    counter_values = []
    for i in range(1, 13):
        col_name = next((c for c in df.columns if c.lower() == f'counter_{i}'), None)
        if not col_name or col_name not in df.columns:
            continue

        # Apply active sensitivity filtering for BOTH lab and live mode
        assigned_val = _lookup_setting(
            settings_data,
            f"Settings.ColorSort.Primary{i}.IsAssigned",
            True,
        )
        if not _bool_from_setting(assigned_val):
            continue

        if is_lab_mode:
            val = last_value_scaled(df[col_name], 60)
        else:
            # Live mode: Use integration method
            c_stats = calculate_total_objects_from_csv_rates(
                df[col_name],
                timestamps=df['timestamp'] if is_lab_mode else None,
                is_lab_mode=is_lab_mode,
            )
            val = c_stats['total_objects']

        if not pd.isna(val):
            counter_values.append((f"S{i}", val))

    if counter_values:
        # UPDATED: Reduced width by 5%, increased height by 5%
        tp_bar = 6
        bw_bar, bh_bar = w_right - 2*tp_bar, bar_height - 2*tp_bar - 12
        chart_w = bw_bar * 0.862  # Reduced from 0.908 to 0.862 (5% reduction)
        chart_h = bh_bar * 0.797  # Increased from 0.759 to 0.797 (5% increase)
        
        # Improved centering calculation
        chart_x = x0 + w_left + tp_bar + (bw_bar - chart_w)/2
        chart_y = y_pie + tp_bar + 12 + (bh_bar - 12 - chart_h)/2  # Better vertical centering
        
        num_bars = len(counter_values)
        bar_width = chart_w / (num_bars * 1.5)
        bar_spacing = chart_w / num_bars
        
        # Convert counter values to percentages for proper scaling
        percentage_values = []
        for counter_name, val in counter_values:
            pct_val = (val / machine_objs) * 100 if machine_objs > 0 else 0
            percentage_values.append((counter_name, pct_val))
        
        # Use percentage scale (0-100%) instead of raw values
        max_val = max(pct for _, pct in percentage_values) if percentage_values else 0
        # Ensure minimum scale for visibility
        max_val = max(max_val, 1.0)  # At least 1% for minimum scale
        
        bar_colors = BAR_COLORS
        
        for i, (counter_name, pct_val) in enumerate(percentage_values):
            bar_x = chart_x + i * bar_spacing + (bar_spacing - bar_width)/2
            # Scale bar height based on percentage, not raw value
            bar_height_val = (pct_val / max_val) * chart_h if max_val > 0 else 0
            bar_y = chart_y
            
            c.setFillColor(bar_colors[i % len(bar_colors)])
            c.setStrokeColor(colors.black)
            c.rect(bar_x, bar_y, bar_width, bar_height_val, fill=1, stroke=1)
            
            c.setFont(FONT_DEFAULT, 8)
            c.setFillColor(colors.black)
            label_x = bar_x + bar_width/2
            c.drawCentredString(label_x, bar_y - 8, counter_name)

            c.setFont(FONT_DEFAULT, 8)
            # Display the percentage value above the bar
            c.drawCentredString(label_x, bar_y + bar_height_val + 2, f"{pct_val:.1f}%")
        
        # Draw axes with LARGER fonts
        c.setStrokeColor(colors.black)
        c.setLineWidth(1)
        c.line(chart_x - 5, chart_y, chart_x - 5, chart_y + chart_h)
        c.line(chart_x - 5, chart_y, chart_x + chart_w, chart_y)
        
        # Y-axis tick marks and values with percentage labels
        c.setFont(FONT_DEFAULT, 7)
        c.setFillColor(colors.black)
        for i in range(4):
            y_val = (max_val * i / 3) if max_val > 0 else 0
            y_pos = chart_y + (chart_h * i / 3)
            c.line(chart_x - 5, y_pos, chart_x - 2, y_pos)
            c.drawRightString(chart_x - 6, y_pos - 1, f"{y_val:.1f}%")
        
        # NOTE: Y-axis title/label has been removed as requested
    else:
        c.setFont(FONT_DEFAULT, 8)
        c.setFillColor(colors.gray)
        c.drawCentredString(x0 + w_left + w_right/2, y_pie + bar_height/2, tr('no_counter_data', lang))
    
    # Section 3: Counter trend graph (full width)
    y_trend = y_pie - trend_height - spacing

    # Build counter trend data
    all_t = []
    series = []
    max_trend_val = 0
    base_time = None

    if 'timestamp' in df.columns:
        time_vals = pd.to_datetime(df['timestamp'], errors='coerce')
        for idx in range(1, 13):
            col_name = next((c for c in df.columns if c.lower() == f'counter_{idx}'), None)
            if not col_name:
                continue
            vals = pd.to_numeric(df[col_name], errors='coerce')
            valid = (~time_vals.isna()) & (~vals.isna())
            if not valid.any():
                continue
            times = time_vals[valid]
            if base_time is None:
                base_time = times.min()
            else:
                base_time = min(base_time, times.min())
            pts = [((t - base_time).total_seconds() / 3600.0, float(v)) for t, v in zip(times, vals[valid])]
            series.append(pts)
            all_t.extend(times)
            max_trend_val = max(max_trend_val, vals[valid].max())

    c.setStrokeColor(colors.black)
    c.rect(x0, y_trend, total_w, trend_height)
    c.setFont(FONT_BOLD, 8)
    c.setFillColor(colors.black)
    c.drawCentredString(x0 + total_w/2, y_trend + trend_height - 10, tr('counter_values_trend_title', lang))

    if series:
        pad = 6
        bw, bh = total_w - 2*pad, trend_height - 2*pad - 12
        tw, th = bw * 0.9, bh * 0.8
        gx = x0 + pad + (bw - tw)/2
        gy = y_trend + pad + 12 + (bh - th)/2

        d_trend = Drawing(tw, th)
        lp = LinePlot()
        lp.x = lp.y = 0
        lp.width = tw
        lp.height = th
        lp.data = series
        for i in range(len(series)):
            lp.lines[i].strokeColor = BAR_COLORS[i % len(BAR_COLORS)]
            lp.lines[i].strokeWidth = 1.5

        xs = [x for pts in series for x, _ in pts]
        if xs:
            lp.xValueAxis.valueMin = min(xs)
            lp.xValueAxis.valueMax = max(xs)
            step = (max(xs) - min(xs)) / 4 if max(xs) > min(xs) else 1
            lp.xValueAxis.valueSteps = [min(xs) + j * step for j in range(5)]
            if base_time is not None:
                lp.xValueAxis.labelTextFormat = lambda v: (base_time + timedelta(hours=v)).strftime('%H:%M')
                lp.xValueAxis.labels.angle = 45
                lp.xValueAxis.labels.boxAnchor = 'n'

        lp.yValueAxis.valueMin = 0
        lp.yValueAxis.valueMax = max_trend_val * 1.1 if max_trend_val else 1
        lp.yValueAxis.valueSteps = None
        d_trend.add(lp)
        renderPDF.draw(d_trend, c, gx, gy)
    else:
        c.setFont(FONT_DEFAULT, 8)
        c.setFillColor(colors.gray)
        c.drawCentredString(x0 + total_w/2, y_trend + trend_height/2, tr('no_counter_data', lang))

    # Section 4: Machine counts (full width) - SIGNIFICANTLY REDUCED HEIGHT
    y_counts = y_trend - counts_height - spacing
    
   
    # Calculate machine totals
    # (machine_objs, machine_rem) computed earlier)

    machine_accepts = 0
    machine_rejects = 0

    if is_lab_mode:
        clean_objects = machine_objs - machine_rem
        machine_accepts = clean_objects * LAB_WEIGHT_MULTIPLIER
        machine_rejects = machine_rem * LAB_WEIGHT_MULTIPLIER
    else:
        if ac_col:
            a_stats = calculate_total_capacity_from_csv_rates(
                df[ac_col],
                timestamps=df['timestamp'] if is_lab_mode else None,
                is_lab_mode=is_lab_mode,
                values_in_kg=values_in_kg,
            )
            machine_accepts = a_stats['total_capacity_lbs']  # ← ADD THIS LINE
        
        if rj_col:
            r_stats = calculate_total_capacity_from_csv_rates(
                df[rj_col],
                timestamps=df['timestamp'] if is_lab_mode else None,
                is_lab_mode=is_lab_mode,
                values_in_kg=values_in_kg,
            )
            machine_rejects = r_stats['total_capacity_lbs'] 

    
    # Draw SMALLER blue counts section
    c.setFillColor(colors.HexColor('#1f77b4'))
    c.rect(x0, y_counts, total_w, counts_height, fill=1, stroke=0)
    
    # Draw section title
    c.setFillColor(colors.white)
    c.setFont(FONT_BOLD, 8)  # Smaller font
    c.drawString(
        x0 + 8,
        y_counts + counts_height - 10,
        tr('machine_counts_title', lang).format(
            machine=machine, machine_label=tr('machine_label', lang)
        ),
    )
    
    # Two columns layout with smaller fonts
    half_counts = total_w / 2
    
    # TOP ROW: Objects and Impurities
    labs_top = [
        tr('objects_processed_label', lang),
        tr('impurities_removed_label', lang),
    ]
    vals_top = [f"{int(machine_objs):,}", f"{int(machine_rem):,}"]
    
    # Center the labels over their data
    c.setFont(FONT_BOLD, 8)  # Keep label font size the same
    for i, lab in enumerate(labs_top):
        center_x = x0 + half_counts * i + half_counts/2
        lw = c.stringWidth(lab, FONT_BOLD, 8)
        c.drawString(center_x - lw/2, y_counts + counts_height * 0.7, lab)
    
    # Increase data text size and center over labels
    c.setFont(FONT_BOLD, COUNT_VALUE_FONT_SIZE)
    for i, val in enumerate(vals_top):
        center_x = x0 + half_counts * i + half_counts/2
        vw = c.stringWidth(val, FONT_BOLD, COUNT_VALUE_FONT_SIZE)
        c.drawString(
            center_x - vw / 2,
            y_counts + counts_height * 0.7 - COUNT_VALUE_FONT_SIZE,
            val,
        )
    

    # BOTTOM ROW: Accepts and Rejects
    labs_bottom = [tr('accepts_label', lang), tr('rejects_label', lang)]
    if is_lab_mode:
        vals_bottom = [
            f"{machine_accepts:,.2f} lbs",
            f"{machine_rejects:,.2f} lbs",
        ]
    else:
        vals_bottom = [
            f"{int(machine_accepts):,} lbs",
            f"{int(machine_rejects):,} lbs",
        ]
    

    # Center the labels over their data
    c.setFont(FONT_BOLD, 8)
    for i, lab in enumerate(labs_bottom):
        center_x = x0 + half_counts * i + half_counts/2
        lw = c.stringWidth(lab, FONT_BOLD, 8)
        c.drawString(center_x - lw/2, y_counts + counts_height * 0.3, lab)

    # Increase data text size and center over labels
    c.setFont(FONT_BOLD, COUNT_VALUE_FONT_SIZE)
    for i, val in enumerate(vals_bottom):
        center_x = x0 + half_counts * i + half_counts/2
        vw = c.stringWidth(val, FONT_BOLD, COUNT_VALUE_FONT_SIZE)
        c.drawString(
            center_x - vw / 2,
            y_counts + counts_height * 0.3 - COUNT_VALUE_FONT_SIZE,
            val,
        )
    
    c.setFillColor(colors.black)
    c.setStrokeColor(colors.black)
    c.rect(x0, y_counts, total_w, counts_height)

    next_y = y_counts - spacing

    if is_lab_mode:
        settings_height = 60
        y_settings = next_y - settings_height
        draw_machine_settings_section(
            c,
            x0,
            y_settings,
            total_w,
            settings_height,
            settings_data,
            lang=lang,
        )
        settings_spacing = 10
        next_y = y_settings - settings_spacing

    grid_height = 50
    next_y = draw_sensitivity_sections(
        c,
        x0,
        next_y,
        total_w,
        grid_height,
        settings_data,
        counter_values=sensitivity_counts,
        lang=lang,
        is_lab_mode=is_lab_mode,
        width=width,
        height=height,
        lab_test_name=lab_test_name,
        bar_colors=BAR_COLORS,
    )

    # Return the Y position where the next content should start
    return next_y


def draw_layout_optimized(
    pdf_path,
    csv_parent_dir,
    *,
    machines=None,
    include_global=True,
    lang="en",
    is_lab_mode: bool = False,
    values_in_kg: bool = False,
    lab_test_name: str | None = None,
):
    """Optimized version - CONSISTENT SIZING, 2 machines per page"""
    
    # Calculate global maximum firing average first
    global_max_firing = calculate_global_max_firing_average(csv_parent_dir, machines, is_lab_mode=is_lab_mode)
    
    c = canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter
    
    margin = 40
    x0 = margin
    total_w = width - 2 * margin
    
    machines = machines or sorted(
        [d for d in os.listdir(csv_parent_dir)
         if os.path.isdir(os.path.join(csv_parent_dir, d)) and d.isdigit()])
    
    
    page_number = 0
    if include_global:
        page_number += 1
        content_start_y = draw_header(
            c,
            width,
            height,
            page_number,
            lang=lang,
            is_lab_mode=is_lab_mode,
            lab_test_name=lab_test_name,
        )
        available_height = content_start_y - margin - 50

        # Draw global summary (takes full page)
        draw_global_summary(
            c,
            csv_parent_dir,
            x0,
            margin,
            total_w,
            available_height,
            is_lab_mode=is_lab_mode,
            lang=lang,
            values_in_kg=values_in_kg,
        )
    
    # Process machines in groups of 2 (HARD LIMIT)
    machines_per_page = 2
    machine_batches = [machines[i:i + machines_per_page] 
                      for i in range(0, len(machines), machines_per_page)]
    
    for batch_idx, machine_batch in enumerate(machine_batches):
        # Start new page for machines (page 2, 3, 4, etc.)
        if page_number > 0:
            c.showPage()
        page_number += 1
        
        # Draw header with page number
        content_start_y = draw_header(
            c,
            width,
            height,
            page_number,
            lang=lang,
            is_lab_mode=is_lab_mode,
            lab_test_name=lab_test_name,
        )
        available_height = content_start_y - margin - 50
        
        # INCREASED height per machine to accommodate larger sections
        fixed_height_per_machine = 260  # INCREASED from 220 to 260
        
        current_y = content_start_y
        
        for machine_idx, machine in enumerate(machine_batch):
            
            current_y = draw_machine_sections(
                c,
                csv_parent_dir,
                machine,
                x0,
                current_y,
                total_w,
                fixed_height_per_machine,
                global_max_firing,
                is_lab_mode=is_lab_mode,
                lang=lang,
                values_in_kg=values_in_kg,
                width=width,
                height=height,
                lab_test_name=lab_test_name,
            )
                
            # FIXED spacing between machines
            current_y -= 20
    
    c.save()
    


def draw_layout_standard(
    pdf_path,
    csv_parent_dir,
    *,
    machines=None,
    include_global=True,
    lang="en",
    is_lab_mode: bool = False,
    values_in_kg: bool = False,
    lab_test_name: str | None = None,
):
    """Standard layout - CONSISTENT SIZING with dynamic page breaks"""
  
    
    # Calculate global maximum firing average first
    global_max_firing = calculate_global_max_firing_average(csv_parent_dir, machines, is_lab_mode=is_lab_mode)
    
    c = canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter
    
    margin = 40
    x0 = margin
    total_w = width - 2 * margin
    fixed_machine_height = 260  # INCREASED from 220 to 260 for larger sections
    
    machines = machines or sorted(
        [d for d in os.listdir(csv_parent_dir)
         if os.path.isdir(os.path.join(csv_parent_dir, d)) and d.isdigit()])
    
    
    page_number = 0
    if include_global:
        page_number += 1
        content_start_y = draw_header(
            c,
            width,
            height,
            page_number,
            lang=lang,
            is_lab_mode=is_lab_mode,
            lab_test_name=lab_test_name,
        )
        available_height = content_start_y - margin - 50

        # Draw global summary (takes full page)
        draw_global_summary(
            c,
            csv_parent_dir,
            x0,
            margin,
            total_w,
            available_height,
            is_lab_mode=is_lab_mode,
            lang=lang,
            values_in_kg=values_in_kg,
        )
    
    # Process machines starting on page 2
    machines_processed = 0
    next_y = None  # Will be set when we start page 2
    
    for machine in machines:
        
        # Check if we need a new page or if this is the first machine
        if next_y is None or (next_y - margin) < fixed_machine_height:
            # Start new page
            if page_number > 0:
                c.showPage()
            page_number += 1
            
            # Draw header on new page with page number
            content_start_y = draw_header(
                c,
                width,
                height,
                page_number,
                lang=lang,
                is_lab_mode=is_lab_mode,
                lab_test_name=lab_test_name,
            )
            next_y = content_start_y
        
        # Draw machine sections with FIXED height and global max
        next_y = draw_machine_sections(
            c,
            csv_parent_dir,
            machine,
            x0,
            next_y,
            total_w,
            fixed_machine_height,
            global_max_firing,
            is_lab_mode=is_lab_mode,
            lang=lang,
            values_in_kg=values_in_kg,
            width=width,
            height=height,
            lab_test_name=lab_test_name,
        )
        
        machines_processed += 1
        
        # FIXED spacing between machines
        next_y -= 20
    
    c.save()
    

if __name__=='__main__':
    sd = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Generate production report")
    parser.add_argument("export_dir", nargs="?", default=os.path.join(sd, 'exports'))
    parser.add_argument("--optimized", action="store_true", help="use optimized layout")
    parser.add_argument("--lab", action="store_true", help="enable lab mode calculations")
    parser.add_argument("--log-kg", action="store_true", help="metrics in CSV are in kilograms")
    parser.add_argument("--lab-test-name", help="lab test name to include in the report")
    args = parser.parse_args()

    pdf_path = generate_report_filename(sd)

    if args.optimized:
        draw_layout_optimized(
            pdf_path,
            args.export_dir,
            lang="en",
            is_lab_mode=args.lab,
            values_in_kg=args.log_kg,
            lab_test_name=args.lab_test_name,
        )
    else:
        draw_layout_standard(
            pdf_path,
            args.export_dir,
            lang="en",
            is_lab_mode=args.lab,
            values_in_kg=args.log_kg,
            lab_test_name=args.lab_test_name,
        )
