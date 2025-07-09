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
import math  # for label angle calculations
# Default fonts used throughout the report
FONT_DEFAULT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
LAB_OBJECT_SCALE_FACTOR = 1.042
# Weight conversion for lab metrics: 46 lbs per 1800 pieces
LAB_WEIGHT_MULTIPLIER = 46 / 1800

from i18n import tr

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

from hourly_data_saving import EXPORT_DIR as METRIC_EXPORT_DIR, get_historical_data

log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
if not logging.getLogger().handlers:
    logging.getLogger().addHandler(logging.StreamHandler())



def draw_header(c, width, height, page_number=None, *, lang="en"):
    """Draw the header section on each page with optional page number"""
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
        search_dirs = [base_dir, os.path.join(base_dir, "assets")]
    
    # Check what font files actually exist in the search directories
    for d in search_dirs:
        logger.debug(f"Checking directory: {d}")
        try:
            files_in_dir = [f for f in os.listdir(d) if f.lower().endswith('.ttf')]
            logger.debug(f"TTF files found in {d}: {files_in_dir}")
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
        'NotoSansJP.ttf'
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
    enpresor = "Enpresor"
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
    for m in machines:
        fp = os.path.join(csv_parent_dir, m, 'last_24h_metrics.csv')
        if os.path.isfile(fp):
            df = df_processor.safe_read_csv(fp)
            if 'capacity' in df.columns:
                stats = calculate_total_capacity_from_csv_rates(
                    df['capacity'],
                    timestamps=df['timestamp'] if is_lab_mode else None,
                    is_lab_mode=is_lab_mode,
                    values_in_kg=values_in_kg,
                )
                total_capacity += stats['total_capacity_lbs']
            ac = next((c for c in df.columns if c.lower()=='accepts'), None)
            rj = next((c for c in df.columns if c.lower()=='rejects'), None)
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

    if is_lab_mode:
        total_accepts *= LAB_WEIGHT_MULTIPLIER
        total_rejects *= LAB_WEIGHT_MULTIPLIER

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
        angles = [45, -50]
        
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
    
    total_objs = total_rem = 0
    for m in machines:
        fp = os.path.join(csv_parent_dir, m, 'last_24h_metrics.csv')
        if os.path.isfile(fp):
            df = df_processor.safe_read_csv(fp)
            if 'objects_per_min' in df.columns:
                obj_stats = calculate_total_objects_from_csv_rates(
                    df['objects_per_min'],
                    timestamps=df['timestamp'] if is_lab_mode else None,
                    is_lab_mode=is_lab_mode
                )
                total_objs += obj_stats['total_objects']
            for i in range(1, 13):
                col = next((c for c in df.columns if c.lower() == f'counter_{i}'), None)
                if col:
                    c_stats = calculate_total_objects_from_csv_rates(
                        df[col],
                        timestamps=df['timestamp'] if is_lab_mode else None,
                        is_lab_mode=is_lab_mode
                    )
                    total_rem += c_stats['total_objects']
    
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
    c.setFont(FONT_BOLD,14)
    for i,val in enumerate(vals4):
        vw=c.stringWidth(val,FONT_BOLD,14)
        c.drawString(x0+half*i+(half-vw)/2,y_sec4+h4/2-14,val)
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
                ts = df.get('timestamp') if is_lab_mode else None
                # Find counter values for this machine
                for i in range(1, 13):
                    col_name = next((c for c in df.columns if c.lower() == f'counter_{i}'), None)
                    if col_name and col_name in df.columns:
                        if is_lab_mode:
                            stats = calculate_total_objects_from_csv_rates(
                                df[col_name],
                                timestamps=ts,
                                is_lab_mode=True,
                            )
                            val = stats['total_objects']
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
        if col:
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
):
    """Draw the three sections for a single machine - OPTIMIZED FOR 2 MACHINES PER PAGE"""
    fp = os.path.join(csv_parent_dir, machine, 'last_24h_metrics.csv')
    if not os.path.isfile(fp):
        return y_start  # Return same position if no data
    
    try:
        df = df_processor.safe_read_csv(fp)
    except Exception as e:
        logger.error(f"Error reading data for machine {machine}: {e}")
        return y_start
    
    # OPTIMIZED DIMENSIONS FOR 2 MACHINES PER PAGE
    w_left = total_w * 0.4
    w_right = total_w * 0.6
    
    # Height allocation optimized for 2 machines
    pie_height = available_height * 0.75      # 35% for pie chart
    bar_height = available_height * 0.75      # 35% for bar chart  
    counts_height = available_height * 0.30   # 25% for counts (REDUCED!)
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
    run_total = df[run_col].sum() if run_col else 0
    stop_total = df[stop_col].sum() if stop_col else 0
    
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
        p_pie.data = [a_val, r_val]
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
        total_pie = a_val + r_val
        if total_pie > 0:
            percentages = [(a_val/total_pie)*100, (r_val/total_pie)*100]
            angles = [45, -52]
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
    
    title_key = 'sensitivity_firing_total_title' if is_lab_mode else 'sensitivity_firing_avg_title'
    title_bar = f"{tr('machine_label', lang)} {machine} - {tr(title_key, lang)}"
    c.setFont(FONT_BOLD, 12)  # Increased from 9 to 12
    c.setFillColor(colors.black)
    c.drawCentredString(x0 + w_left + w_right/2, y_pie + bar_height - 10, title_bar)
    
    # Draw bar chart with counter values
    counter_values = []
    for i in range(1, 13):
        col_name = next((c for c in df.columns if c.lower() == f'counter_{i}'), None)
        if col_name and col_name in df.columns:
            if is_lab_mode:
                stats = calculate_total_objects_from_csv_rates(
                    df[col_name],
                    timestamps=df['timestamp'] if 'timestamp' in df.columns else None,
                    is_lab_mode=True,
                )
                val = stats['total_objects']
            else:
                val = df[col_name].mean()
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
        
        # Use global max if provided, otherwise use local max
        max_val = global_max_firing if global_max_firing and global_max_firing > 0 else max(val for _, val in counter_values)
        
        bar_colors = [colors.red, colors.blue, colors.green, colors.orange, 
                    colors.purple, colors.brown, colors.pink, colors.gray,
                    colors.cyan, colors.magenta, colors.yellow, colors.black]
        
        for i, (counter_name, val) in enumerate(counter_values):
            bar_x = chart_x + i * bar_spacing + (bar_spacing - bar_width)/2
            bar_height_val = (val / max_val) * chart_h if max_val > 0 else 0
            bar_y = chart_y
            
            c.setFillColor(bar_colors[i % len(bar_colors)])
            c.setStrokeColor(colors.black)
            c.rect(bar_x, bar_y, bar_width, bar_height_val, fill=1, stroke=1)
            
            c.setFont(FONT_DEFAULT, 8)  # Increased X-axis label size from 6 to 8
            c.setFillColor(colors.black)
            label_x = bar_x + bar_width/2
            c.drawCentredString(label_x, bar_y - 8, counter_name)

            c.setFont(FONT_DEFAULT, 5)  # Smaller font
            c.drawCentredString(label_x, bar_y + bar_height_val + 2, f"{val:.1f}")
        
        # Draw axes with LARGER fonts
        c.setStrokeColor(colors.black)
        c.setLineWidth(1)
        c.line(chart_x - 5, chart_y, chart_x - 5, chart_y + chart_h)
        c.line(chart_x - 5, chart_y, chart_x + chart_w, chart_y)
        
        # Y-axis tick marks and values with LARGER font
        c.setFont(FONT_DEFAULT, 7)  # Increased Y-axis label size from 5 to 7
        c.setFillColor(colors.black)
        for i in range(4):  # Reduced tick marks
            y_val = (max_val * i / 3) if max_val > 0 else 0
            y_pos = chart_y + (chart_h * i / 3)
            c.line(chart_x - 5, y_pos, chart_x - 2, y_pos)
            c.drawRightString(chart_x - 6, y_pos - 1, f"{y_val:.0f}")
        
        # NOTE: Y-axis title/label has been removed as requested
    else:
        c.setFont(FONT_DEFAULT, 8)
        c.setFillColor(colors.gray)
        c.drawCentredString(x0 + w_left + w_right/2, y_pie + bar_height/2, tr('no_counter_data', lang))
    
    # Section 3: Machine counts (full width) - SIGNIFICANTLY REDUCED HEIGHT
    y_counts = y_pie - counts_height - spacing
    
   
    # Calculate machine totals
    machine_objs = 0
    if 'objects_per_min' in df.columns:
        obj_stats = calculate_total_objects_from_csv_rates(
            df['objects_per_min'],
            timestamps=df['timestamp'] if is_lab_mode else None,
            is_lab_mode=is_lab_mode
        )
        machine_objs = obj_stats['total_objects']
    machine_rem = 0
    for i in range(1, 13):
        col = next((c for c in df.columns if c.lower() == f'counter_{i}'), None)
        if col:
            c_stats = calculate_total_objects_from_csv_rates(
                df[col],
                timestamps=df['timestamp'] if is_lab_mode else None,
                is_lab_mode=is_lab_mode
            )
            machine_rem += c_stats['total_objects']
    

    machine_accepts = 0
    if ac_col:
        a_stats = calculate_total_capacity_from_csv_rates(
            df[ac_col],
            timestamps=df['timestamp'] if is_lab_mode else None,
            is_lab_mode=is_lab_mode,
            values_in_kg=values_in_kg,
        )
        machine_accepts = a_stats["total_capacity_lbs"]

    machine_rejects = 0
    if rj_col:
        r_stats = calculate_total_capacity_from_csv_rates(
            df[rj_col],
            timestamps=df['timestamp'] if is_lab_mode else None,
            is_lab_mode=is_lab_mode,
            values_in_kg=values_in_kg,
        )
        machine_rejects = r_stats["total_capacity_lbs"]

    if is_lab_mode:
        machine_accepts *= LAB_WEIGHT_MULTIPLIER
        machine_rejects *= LAB_WEIGHT_MULTIPLIER

    
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
    c.setFont(FONT_BOLD, 14)  # Increased from 10 to 14
    for i, val in enumerate(vals_top):
        center_x = x0 + half_counts * i + half_counts/2
        vw = c.stringWidth(val, FONT_BOLD, 14)
        c.drawString(center_x - vw/2, y_counts + counts_height * 0.7 - 14, val)
    

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
    c.setFont(FONT_BOLD, 14)
    for i, val in enumerate(vals_bottom):
        center_x = x0 + half_counts * i + half_counts/2
        vw = c.stringWidth(val, FONT_BOLD, 14)
        c.drawString(center_x - vw/2, y_counts + counts_height * 0.3 - 14, val)
    
    c.setFillColor(colors.black)
    c.setStrokeColor(colors.black)
    c.rect(x0, y_counts, total_w, counts_height)
    
    # Return the Y position where the next content should start
    return y_counts - spacing


def draw_layout_optimized(
    pdf_path,
    csv_parent_dir,
    *,
    machines=None,
    include_global=True,
    lang="en",
    is_lab_mode: bool = False,
    values_in_kg: bool = False,
):
    """Optimized version - CONSISTENT SIZING, 2 machines per page"""
    logger.debug("=== DEBUGGING MACHINE DATA ===")
    logger.debug("==============================")
    
    # Calculate global maximum firing average first
    logger.debug("Calculating global maximum firing average...")
    global_max_firing = calculate_global_max_firing_average(csv_parent_dir, machines, is_lab_mode=is_lab_mode)
    logger.debug(f"Global maximum firing average: {global_max_firing:.2f}")
    
    c = canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter
    
    margin = 40
    x0 = margin
    total_w = width - 2 * margin
    
    machines = machines or sorted(
        [d for d in os.listdir(csv_parent_dir)
         if os.path.isdir(os.path.join(csv_parent_dir, d)) and d.isdigit()])
    
    logger.debug(f"Processing {len(machines)} machines: {machines}")
    
    page_number = 0
    if include_global:
        page_number += 1
        logger.debug("Creating Page 1: Global Summary Only")
        content_start_y = draw_header(c, width, height, page_number, lang=lang)
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
        logger.debug(f"Creating Page {page_number}: Machines {machine_batch}")
        
        # Draw header with page number
        content_start_y = draw_header(c, width, height, page_number, lang=lang)
        available_height = content_start_y - margin - 50
        
        # INCREASED height per machine to accommodate larger sections
        fixed_height_per_machine = 260  # INCREASED from 220 to 260
        
        current_y = content_start_y
        
        for machine_idx, machine in enumerate(machine_batch):
            logger.debug(
                f"  Drawing Machine {machine} ({machine_idx + 1}/{len(machine_batch)}) - FIXED SIZE"
            )
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
            )
            # FIXED spacing between machines
            current_y -= 20
    
    c.save()
    logger.info(f"Optimized multi-page layout saved at: {os.path.abspath(pdf_path)}")
    logger.info(f"Total pages created: {page_number}")
    if include_global:
        logger.info("Page 1: Global Summary")
        logger.info(
            f"Pages 2+: Individual machines (CONSISTENT sizing, max {machines_per_page} per page)"
        )
    else:
        logger.info(
            f"Machine pages only (CONSISTENT sizing, max {machines_per_page} per page)"
        )


def draw_layout_standard(
    pdf_path,
    csv_parent_dir,
    *,
    machines=None,
    include_global=True,
    lang="en",
    is_lab_mode: bool = False,
    values_in_kg: bool = False,
):
    """Standard layout - CONSISTENT SIZING with dynamic page breaks"""
    logger.debug("=== DEBUGGING MACHINE DATA ===")
    logger.debug("==============================")
    
    # Calculate global maximum firing average first
    logger.debug("Calculating global maximum firing average...")
    global_max_firing = calculate_global_max_firing_average(csv_parent_dir, machines, is_lab_mode=is_lab_mode)
    logger.debug(f"Global maximum firing average: {global_max_firing:.2f}")
    
    c = canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter
    
    margin = 40
    x0 = margin
    total_w = width - 2 * margin
    fixed_machine_height = 260  # INCREASED from 220 to 260 for larger sections
    
    machines = machines or sorted(
        [d for d in os.listdir(csv_parent_dir)
         if os.path.isdir(os.path.join(csv_parent_dir, d)) and d.isdigit()])
    
    logger.debug(f"Processing {len(machines)} machines: {machines}")
    
    page_number = 0
    if include_global:
        page_number += 1
        logger.debug("Creating Page 1: Global Summary Only")
        content_start_y = draw_header(c, width, height, page_number, lang=lang)
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
        logger.debug(f"Processing Machine {machine}")
        
        # Check if we need a new page or if this is the first machine
        if next_y is None or (next_y - margin) < fixed_machine_height:
            # Start new page
            logger.debug(f"Starting new page for Machine {machine}")
            if page_number > 0:
                c.showPage()
            page_number += 1
            logger.debug(f"Creating Page {page_number}: Machine {machine}")
            
            # Draw header on new page with page number
            content_start_y = draw_header(c, width, height, page_number, lang=lang)
            next_y = content_start_y
        
        # Draw machine sections with FIXED height and global max
        logger.debug(f"  Drawing Machine {machine} - FIXED SIZE ({fixed_machine_height}px)")
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
        )
        
        machines_processed += 1
        
        # FIXED spacing between machines
        next_y -= 20
    
    c.save()
    logger.info(f"Standard multi-page layout saved at: {os.path.abspath(pdf_path)}")
    logger.info(f"Total pages created: {page_number}")
    if include_global:
        logger.info("Page 1: Global Summary")
        logger.info("Pages 2+: Individual machines (CONSISTENT sizing)")
    else:
        logger.info("Machine pages only (CONSISTENT sizing)")


if __name__=='__main__':
    sd = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Generate production report")
    parser.add_argument("export_dir", nargs="?", default=os.path.join(sd, 'exports'))
    parser.add_argument("--optimized", action="store_true", help="use optimized layout")
    parser.add_argument("--lab", action="store_true", help="enable lab mode calculations")
    parser.add_argument("--log-kg", action="store_true", help="metrics in CSV are in kilograms")
    args = parser.parse_args()

    pdf_path = generate_report_filename(sd)

    if args.optimized:
        logger.info("Using optimized layout (2 machines per page)...")
        draw_layout_optimized(
            pdf_path,
            args.export_dir,
            lang="en",
            is_lab_mode=args.lab,
            values_in_kg=args.log_kg,
        )
    else:
        logger.info("Using standard layout (dynamic page breaks)...")
        draw_layout_standard(
            pdf_path,
            args.export_dir,
            lang="en",
            is_lab_mode=args.lab,
            values_in_kg=args.log_kg,
        )
