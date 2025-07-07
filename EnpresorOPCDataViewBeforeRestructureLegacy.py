
"""
Satake Evolution Sorter OPC UA Monitoring Dashboard with Company Logo
"""


import os
import sys
import asyncio
import logging
import time
import base64
import argparse
try:
    from distutils.util import strtobool
except ImportError:  # Python 3.12+ where distutils is removed
    def strtobool(val: str) -> int:
        """Return 1 for truthy strings and 0 for falsy ones."""
        val = val.lower()
        if val in ("y", "yes", "t", "true", "on", "1"):
            return 1
        if val in ("n", "no", "f", "false", "off", "0"):
            return 0
        raise ValueError(f"invalid truth value {val!r}")
from threading import Thread
from datetime import datetime, timedelta
import csv
import io
import math
import random
import json
import tempfile
from pathlib import Path
from collections import defaultdict
import autoconnect
try:
    import generate_report
except Exception as exc:  # pragma: no cover - optional dependency
    logging.warning(f"generate_report module could not be loaded: {exc}")
    generate_report = None

try:
    from hourly_data_saving import (
        initialize_data_saving,
        get_historical_data,
        append_metrics,
        append_control_log,
        get_historical_control_log,
        clear_machine_data,

    )

except Exception as e:
    logging.warning(f"hourly_data_saving module could not be loaded: {e}")

    def initialize_data_saving(machine_ids=None):
        """Fallback if hourly_data_saving is unavailable"""
        return None


    def get_historical_data(timeframe="24h", machine_id=None):
        """Fallback if hourly_data_saving is unavailable"""
        return {}


    def append_metrics(metrics, machine_id=None, mode=None):
        return None


    def append_control_log(entry, machine_id=None):
        return None


    def get_historical_control_log(timeframe="24h", machine_id=None):
        return []

    def clear_machine_data(machine_id=None):
        return None

      
#from dash import callback_context, no_update
try:
    from dash.exceptions import PreventUpdate
except Exception:  # pragma: no cover - optional dependency
    class PreventUpdate(Exception):
        pass

logging.getLogger('opcua').setLevel(logging.WARNING)  # Turn off OPC UA debug logs
logging.getLogger('opcua.client.ua_client').setLevel(logging.WARNING)
logging.getLogger('opcua.uaprotocol').setLevel(logging.WARNING)
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
# Older versions cleared the handlers created by ``basicConfig``. Ensure a
# console handler is present so logs appear when running from the command line.
if not logging.getLogger().handlers:
    logging.getLogger().addHandler(logging.StreamHandler())
logging.getLogger("werkzeug").setLevel(logging.ERROR)

# Common numeric font for dashboard values
NUMERIC_FONT = "Monaco, Consolas, 'Courier New', monospace"

# Height for the header card in the machine dashboard
HEADER_CARD_HEIGHT = "65px"
# Standard height for dashboard sections
SECTION_HEIGHT = "220px"
SECTION_HEIGHT2 = "250px"
# Path to display settings JSON file
DISPLAY_SETTINGS_PATH = Path(__file__).resolve().parent / "display_settings.json"
EMAIL_SETTINGS_PATH = Path(__file__).resolve().parent / "email_settings.json"

reconnection_state = {
    
}

# Define known tags instead of discovering them. Keys are human friendly names
# and values are full OPC UA node ids. Dictionary comprehensions below expand
# repetitive patterns.
KNOWN_TAGS = {
    # Status Information
    "Status.Info.Serial": "ns=2;s=Status.Info.Serial",
    "Status.Info.Type": "ns=2;s=Status.Info.Type", 
    "Status.Info.PresetNumber": "ns=2;s=Status.Info.PresetNumber",
    "Status.Info.PresetName": "ns=2;s=Status.Info.PresetName",
    
    # Alive counter
    "Alive": "ns=2;s=Alive",
    
    # Production data
    "Status.ColorSort.Sort1.Throughput.KgPerHour.Current": "ns=2;s=Status.ColorSort.Sort1.Throughput.KgPerHour.Current",
    "Status.Production.Accepts": "ns=2;s=Status.Production.Accepts", 
    "Status.Production.Rejects": "ns=2;s=Status.Production.Rejects",
    "Status.Production.Weight": "ns=2;s=Status.Production.Weight",
    "Status.Production.Count": "ns=2;s=Status.Production.Count",
    "Status.Production.Units": "ns=2;s=Status.Production.Units",
    
    # Test weight settings tags - THESE ARE THE NEW ONES
    "Settings.ColorSort.TestWeightValue": "ns=2;s=Settings.ColorSort.TestWeightValue",
    "Settings.ColorSort.TestWeightCount": "ns=2;s=Settings.ColorSort.TestWeightCount",
    
    # ADD THIS NEW TAG:
    "Diagnostic.Counter": "ns=2;s=Diagnostic.Counter",
    
    # Faults and warnings
    "Status.Faults.GlobalFault": "ns=2;s=Status.Faults.GlobalFault",
    "Status.Faults.GlobalWarning": "ns=2;s=Status.Faults.GlobalWarning",
    
    # Feeders (1-4)
    **{f"Status.Feeders.{i}IsRunning": f"ns=2;s=Status.Feeders.{i}IsRunning" for i in range(1, 5)},
    **{f"Status.Feeders.{i}Rate": f"ns=2;s=Status.Feeders.{i}Rate" for i in range(1, 5)},
    
    # Counter rates (1-12)
    **{f"Status.ColorSort.Sort1.DefectCount{i}.Rate.Current": f"ns=2;s=Status.ColorSort.Sort1.DefectCount{i}.Rate.Current" for i in range(1, 13)},
    
    # Primary color sort settings (1-12)
    **{f"Settings.ColorSort.Primary{i}.IsAssigned": f"ns=2;s=Settings.ColorSort.Primary{i}.IsAssigned" for i in range(1, 13)},
    **{f"Settings.ColorSort.Primary{i}.IsActive": f"ns=2;s=Settings.ColorSort.Primary{i}.IsActive" for i in range(1, 13)},
    **{f"Settings.ColorSort.Primary{i}.Name": f"ns=2;s=Settings.ColorSort.Primary{i}.Name" for i in range(1, 13)},
    
    # Environmental
    "Status.Environmental.AirPressurePsi": "ns=2;s=Status.Environmental.AirPressurePsi",
    
    # Objects per minute
    "Status.ColorSort.Primary.ObjectPerMin": "ns=2;s=Status.ColorSort.Primary.ObjectPerMin",
}

# Tags that are updated on every cycle in live mode. These names come from
# ``KNOWN_TAGS`` and drive the real-time dashboard callbacks.
FAST_UPDATE_TAGS = {
    "Status.Info.Serial",
    "Status.Info.Type",
    "Status.Info.PresetNumber",
    "Status.Info.PresetName",
    "Status.Faults.GlobalFault",
    "Status.Faults.GlobalWarning",
    "Status.ColorSort.Sort1.Throughput.KgPerHour.Current",
    "Status.ColorSort.Sort1.Total.Percentage.Current",
    "Status.ColorSort.Sort1.Throughput.ObjectPerMin.Current",
    "Status.ColorSort.Primary.ObjectPerMin",
    "Settings.ColorSort.TestWeightValue",
    "Settings.ColorSort.TestWeightCount",
    "Diagnostic.Counter",
    "Settings.ColorSort.Primary1.SampleImage",
    "Settings.ColorSort.Primary2.SampleImage",
    "Settings.ColorSort.Primary3.SampleImage",
    "Settings.ColorSort.Primary4.SampleImage",
    "Settings.ColorSort.Primary5.SampleImage",
    "Settings.ColorSort.Primary6.SampleImage",
    "Settings.ColorSort.Primary7.SampleImage",
    "Settings.ColorSort.Primary8.SampleImage",
    "Settings.ColorSort.Primary9.SampleImage",
    "Settings.ColorSort.Primary10.SampleImage",
    "Settings.ColorSort.Primary11.SampleImage",
    "Settings.ColorSort.Primary12.SampleImage",
    "Settings.ColorSort.Primary1.Name",
    "Settings.ColorSort.Primary2.Name",
    "Settings.ColorSort.Primary3.Name",
    "Settings.ColorSort.Primary4.Name",
    "Settings.ColorSort.Primary5.Name",
    "Settings.ColorSort.Primary6.Name",
    "Settings.ColorSort.Primary7.Name",
    "Settings.ColorSort.Primary8.Name",
    "Settings.ColorSort.Primary9.Name",
    "Settings.ColorSort.Primary10.Name",
    "Settings.ColorSort.Primary11.Name",
    "Settings.ColorSort.Primary12.Name",
    "Settings.ColorSort.Primary1.IsAssigned",
    "Settings.ColorSort.Primary2.IsAssigned",
    "Settings.ColorSort.Primary3.IsAssigned",
    "Settings.ColorSort.Primary4.IsAssigned",
    "Settings.ColorSort.Primary5.IsAssigned",
    "Settings.ColorSort.Primary6.IsAssigned",
    "Settings.ColorSort.Primary7.IsAssigned",
    "Settings.ColorSort.Primary8.IsAssigned",
    "Settings.ColorSort.Primary9.IsAssigned",
    "Settings.ColorSort.Primary10.IsAssigned",
    "Settings.ColorSort.Primary11.IsAssigned",
    "Settings.ColorSort.Primary12.IsAssigned",
    "Settings.ColorSort.Primary1.IsActive",
    "Settings.ColorSort.Primary2.IsActive",
    "Settings.ColorSort.Primary3.IsActive",
    "Settings.ColorSort.Primary4.IsActive",
    "Settings.ColorSort.Primary5.IsActive",
    "Settings.ColorSort.Primary6.IsActive",
    "Settings.ColorSort.Primary7.IsActive",
    "Settings.ColorSort.Primary8.IsActive",
    "Settings.ColorSort.Primary9.IsActive",
    "Settings.ColorSort.Primary10.IsActive",
    "Settings.ColorSort.Primary11.IsActive",
    "Settings.ColorSort.Primary12.IsActive",
} | {f"Status.Feeders.{i}IsRunning" for i in range(1, 5)} \
  | {f"Status.Feeders.{i}Rate" for i in range(1, 5)} \
  | {f"Status.ColorSort.Sort1.DefectCount{i}.Rate.Current" for i in range(1, 13)}

# How often non fast-update tags should be polled when not in live mode.
SLOW_UPDATE_EVERY = 10


DEFAULT_THRESHOLD_SETTINGS = {
    i: {
        'min_enabled': True,
        'max_enabled': True,
        'min_value': 50 - (i-1)*5 if i <= 5 else (20 if i <= 8 else (15 if i <= 10 else 10)),
        'max_value': 140 - (i-1)*10 if i <= 10 else (40 if i == 11 else 30)
    } for i in range(1, 13)
}

# Add the email_enabled setting to the default settings
DEFAULT_THRESHOLD_SETTINGS['email_enabled'] = False

# Initialize threshold_settings with a copy of the defaults
threshold_settings = DEFAULT_THRESHOLD_SETTINGS.copy()
DEFAULT_THRESHOLD_SETTINGS['email_address'] = ''
DEFAULT_THRESHOLD_SETTINGS['email_minutes'] = 2  # Default 2 minutes
threshold_violation_state = {
    i: {
        'is_violating': False,
        'violation_start_time': None,
        'email_sent': False
    } for i in range(1, 13)
}

def save_uploaded_image(image_data):
    """Save the uploaded image data to a file - simplified version"""
    try:
        # Create a data directory if it doesn't exist. Uploaded images and
        # layout files live here so they persist between application runs.
        if not os.path.exists('data'):
            os.makedirs('data')
        
        # Save the complete image data string directly
        with open('data/custom_image.txt', 'w') as f:
            f.write(image_data)
            
        logger.info("Custom image saved successfully")
        return True
    except Exception as e:
        logger.error(f"Error saving custom image: {e}")
        return False

def load_saved_image():
    """Load the saved custom image if it exists - simplified version"""
    try:
        if os.path.exists('data/custom_image.txt'):
            with open('data/custom_image.txt', 'r') as f:
                image_data = f.read()
                
            logger.info("Custom image loaded successfully")
            # Return the data in the exact format needed by the Store
            return {"image": image_data}
        else:
            logger.info("No saved custom image found")
            return {}
    except Exception as e:
        logger.error(f"Error loading custom image: {e}")
        return {}


# Default SMTP settings used when ``email_settings.json`` is missing. These
# allow optional alarm emails without storing credentials in version control.
DEFAULT_EMAIL_SETTINGS = {
    "smtp_server": "smtp.postmarkapp.com",
    "smtp_port": 587,
    "smtp_username": "",
    "smtp_password": "",
    "from_address": "jcantu@satake-usa.com",
}


def load_email_settings():
    """Load SMTP email settings from a JSON file."""
    try:
        if EMAIL_SETTINGS_PATH.exists():
            with open(EMAIL_SETTINGS_PATH, "r") as f:
                data = json.load(f)
                return {
                    "smtp_server": data.get("smtp_server", DEFAULT_EMAIL_SETTINGS["smtp_server"]),
                    "smtp_port": data.get("smtp_port", DEFAULT_EMAIL_SETTINGS["smtp_port"]),
                    "smtp_username": data.get("smtp_username", ""),
                    "smtp_password": data.get("smtp_password", ""),
                    "from_address": data.get("from_address", DEFAULT_EMAIL_SETTINGS["from_address"]),
                }
    except Exception as e:
        logger.error(f"Error loading email settings: {e}")
    return DEFAULT_EMAIL_SETTINGS.copy()


def save_email_settings(settings):
    """Save SMTP email settings to ``email_settings.json``."""
    try:
        with open(EMAIL_SETTINGS_PATH, "w") as f:
            json.dump(settings, f, indent=4)
        logger.info("Email settings saved successfully")
        return True
    except Exception as e:
        logger.error(f"Error saving email settings: {e}")
        return False


# Load user specific SMTP settings (if any). These values are kept outside
# of version control so credentials remain private.
email_settings = load_email_settings()

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_threshold_email(sensitivity_num, is_high=True):
    """Send an email notification for a threshold violation"""
    try:
        # Get email settings
        email_address = threshold_settings.get('email_address', '')
        if not email_address:
            logger.warning("No email address configured for notifications")
            return False
        
        # Create the email
        msg = MIMEMultipart()
        msg['Subject'] = "Enpresor Alarm"
        msg['From'] = "jcantu@satake-usa.com"  # Your verified sender address
        msg['To'] = email_address
        
        # Email body
        threshold_type = "upper" if is_high else "lower"
        body = f"Sensitivity {sensitivity_num} has reached the {threshold_type} threshold."
        msg.attach(MIMEText(body, 'plain'))
        
        # Log the email (for debugging)
        logger.info(f"Sending email to {email_address}: {body}")
        
        # Configure SMTP server and send email using stored credentials
        server_addr = email_settings.get('smtp_server', DEFAULT_EMAIL_SETTINGS['smtp_server'])
        port = email_settings.get('smtp_port', DEFAULT_EMAIL_SETTINGS['smtp_port'])
        server = smtplib.SMTP(server_addr, port)
        server.starttls()
        username = email_settings.get('smtp_username')
        password = email_settings.get('smtp_password')
        if username and password:
            server.login(username, password)

        # Send email
        from_addr = email_settings.get('from_address', DEFAULT_EMAIL_SETTINGS['from_address'])
        text = msg.as_string()
        server.sendmail(from_addr, email_address, text)
        server.quit()
        return True
        
    except Exception as e:
        logger.error(f"Error sending threshold email: {e}")
        return False

# Then define the load function
def load_threshold_settings():
    """Load threshold settings from a JSON file"""
    try:
        # Log the current working directory to help debug file access issues
        current_dir = os.getcwd()
        logger.info(f"Loading threshold settings from '{current_dir}/threshold_settings.json'")
        
        if os.path.exists('threshold_settings.json'):
            with open('threshold_settings.json', 'r') as f:
                loaded_settings = json.load(f)
                
                # Convert string keys back to integers for internal use (except special keys)
                settings = {}
                for key, value in loaded_settings.items():
                    if key in ['email_enabled', 'email_address', 'email_minutes']:
                        settings[key] = value
                    else:
                        settings[int(key)] = value
                
                # Log what was loaded
                logger.info(f"Loaded threshold settings: {settings.keys()}")
                return settings
        else:
            logger.warning("No threshold_settings.json file found")
            return None
    except Exception as e:
        logger.error(f"Error loading threshold settings: {e}")
        return None

def save_theme_preference(theme):
    """Save theme preference to display_settings.json"""
    try:
        # Load existing settings if file exists
        settings = {}
        if os.path.exists('display_settings.json'):
            with open('display_settings.json', 'r') as f:
                try:
                    settings = json.load(f)
                except json.JSONDecodeError:
                    logger.warning("display_settings.json is corrupted, creating new file")
                    settings = {}
        
        # Update the theme setting
        settings['app_theme'] = theme
        
        # Save back to file
        with open('display_settings.json', 'w') as f:
            json.dump(settings, f, indent=4)
        logger.info(f"Successfully saved theme preference: {theme} to display_settings.json")
        return True
        
    except Exception as e:
        logger.error(f"Error saving theme preference: {e}")
        return False

def save_threshold_settings(settings):
    """Save threshold settings to a JSON file"""
    try:
        # Log the current working directory to help debug file access issues
        current_dir = os.getcwd()
        logger.info(f"Saving threshold settings to '{current_dir}/threshold_settings.json'")
        
        # Convert integer keys to strings for JSON serialization
        json_settings = {}
        for key, value in settings.items():
            if isinstance(key, int):
                json_settings[str(key)] = value
            else:
                json_settings[key] = value
        
        # Log what we're saving
        logger.info(f"Saving settings with keys: {json_settings.keys()}")
        
        with open('threshold_settings.json', 'w') as f:
            json.dump(json_settings, f, indent=4)
            
        logger.info("Threshold settings saved successfully")
        return True
    except Exception as e:
        logger.error(f"Error saving threshold settings: {e}")
        
        return False


threshold_settings = DEFAULT_THRESHOLD_SETTINGS.copy()
# Try to load settings
try:
    loaded_settings = load_threshold_settings()
    if loaded_settings:
        # Update settings with loaded values
        for key, value in loaded_settings.items():
            threshold_settings[key] = value
        logger.info(f"Applied loaded threshold settings: {list(loaded_settings.keys())}")
    else:
        logger.info("No saved settings found, using defaults")
except Exception as e:
    logger.error(f"Error applying threshold settings: {e}")


# Import required modules
try:
    from opcua import Client, ua
except Exception as e:  # pragma: no cover - optional dependency
    logger.warning(f"OPC UA modules not available: {e}")
    Client = ua = None

try:
    import dash
    from dash import dcc, html, no_update, callback_context
    from dash.dependencies import Input, Output, State, ALL
    import dash_bootstrap_components as dbc
    from i18n import tr
except Exception as e:  # pragma: no cover - optional dependency
    logger.warning(f"Dash modules not available: {e}")
    dash = dcc = html = no_update = callback_context = None
    Input = Output = State = ALL = None
    dbc = None
    from i18n import tr

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    import plotly.express as px
    import pandas as pd
    import numpy as np
except Exception as e:  # pragma: no cover - optional dependency
    logger.warning(f"Plotly modules not available: {e}")
    go = make_subplots = px = pd = np = None


# Global display settings - initialize with all traces visible
display_settings = {i: True for i in range(1, 13)}  # Default: all traces visible
active_machine_id = None  # This will track which machine's data to display on main dashboard

# Current application mode ("demo", "live" or "historical").  This is updated by
# a Dash callback whenever the ``app-mode`` store changes so that background
# threads can check the latest mode without needing a callback context.
current_app_mode = "live"

# Tag for the currently loaded preset name
PRESET_NAME_TAG = "Status.Info.PresetName"

# First, let's define which tags we want to monitor in a global variable
MONITORED_TAGS = [
    f"Sensitivity {i}" for i in range(1, 13)  # Sensitivity 1-12
] + ["Feeder Rate"]  # Adding Feed Rate as the 13th tag

# Mapping of feeder rate OPC tags to human-friendly names
MONITORED_RATE_TAGS = {
    f"Status.Feeders.{i}Rate": f"Feeder {i} Rate"
    for i in range(1, 5)
}


# Mapping of sensitivity activation/assignment tags to their number for easy reference
SENSITIVITY_ACTIVE_TAGS = {
    f"Settings.ColorSort.Primary{i}.IsActive": i
    for i in range(1, 13)
} | {
    f"Settings.ColorSort.Primary{i}.IsAssigned": i
    for i in range(1, 13)
}

# Create a list to store log entries
machine_control_log = []

# Number of consecutive read failures allowed before a connection is
# considered lost
FAILURE_THRESHOLD = 3

def add_control_log_entry(tag_name, old_value, new_value, *, demo=False,
                          machine_id=None):
    """Add an entry to the machine control log

    Parameters
    ----------
    tag_name : str
        Friendly name of the tag being changed.
    old_value : Any
        Previous value read from the tag.
    new_value : Any
        Newly read value for the tag.
    demo : bool, optional
        If ``True`` mark this entry as demo data so it can be filtered when
        displaying logs in Live mode.  Defaults to ``False``.
    """
    if machine_id is None:
        machine_id = active_machine_id

    timestamp = datetime.now().strftime("%I:%M:%S %p")  # 12-hour format with AM/PM
    
    # Determine if value increased or decreased using arrows for compact display
    if new_value > old_value:
        icon = "\u2B06"  # Up arrow
    elif new_value < old_value:
        icon = "\u2B07"  # Down arrow
    else:
        icon = "\u2192"  # Right arrow for unchanged values

    action = icon
    
    # Create log entry
    entry = {
        "tag": tag_name,
        "action": action,
        "icon": icon,
        "old_value": old_value,
        "new_value": new_value,
        "display_timestamp": timestamp,
        "time": datetime.now(),  # Store actual datetime for sorting
        "demo": demo,
        "machine_id": machine_id,
    }
    
    # Add to log and keep only most recent 100 entries
    global machine_control_log
    machine_control_log.insert(0, entry)  # Insert at beginning (newest first)
    del machine_control_log[100:]  # Keep only most recent 100 entries

    entry_for_file = entry.copy()
    entry_for_file.pop("machine_id", None)
    append_control_log(entry_for_file, machine_id)

    return entry


def add_activation_log_entry(sens_num, enabled, *, demo=False, machine_id=None):
    """Log a sensitivity activation or deactivation event."""
    if machine_id is None:
        machine_id = active_machine_id

    timestamp = datetime.now().strftime("%I:%M:%S %p")
    action_text = "Enabled" if enabled else "Disabled"
    icon = "✅" if enabled else "❌"

    entry = {
        "tag": f"Sens {sens_num}",
        "action": action_text,
        "icon": icon,
        "old_value": "",
        "new_value": "",
        "display_timestamp": timestamp,
        "time": datetime.now(),
        "demo": demo,
        "machine_id": machine_id,
    }

    global machine_control_log
    machine_control_log.insert(0, entry)
    del machine_control_log[100:]  # Keep only most recent 100 entries

    entry_for_file = entry.copy()
    entry_for_file.pop("machine_id", None)
    append_control_log(entry_for_file, machine_id)

    return entry


def add_preset_log_entry(old_name, new_name, *, demo=False, machine_id=None):
    """Log a preset name change event."""
    if machine_id is None:
        machine_id = active_machine_id

    timestamp = datetime.now().strftime("%I:%M:%S %p")
    icon = "\U0001F504"  # Swap icon

    entry = {
        "tag": "Preset",
        "action": icon,
        "icon": icon,
        "old_value": old_name,
        "new_value": new_name,
        "display_timestamp": timestamp,
        "time": datetime.now(),
        "demo": demo,
        "machine_id": machine_id,
    }

    global machine_control_log
    machine_control_log.insert(0, entry)
    del machine_control_log[100:]

    entry_for_file = entry.copy()
    entry_for_file.pop("machine_id", None)
    append_control_log(entry_for_file, machine_id)

    return entry

# Initialize with some demo data
if not machine_control_log:
    # Clear any existing entries
    machine_control_log = []
    
    # Add demo entries with timestamps in the past
    now = datetime.now()
    add_control_log_entry("Sens 1", 45, 48, demo=True).update({"display_timestamp": "11:30:33PM", "time": now - timedelta(hours=1)})
    add_control_log_entry("Sens 4", 66, 60, demo=True).update({"display_timestamp": "10:09:45PM", "time": now - timedelta(hours=2)})
    add_control_log_entry("Feed", 85, 70, demo=True).update({"display_timestamp": "12:15:30AM", "time": now - timedelta(hours=10)})


# Global variable to store previous tag values for comparison
previous_tag_values = {tag: None for tag in MONITORED_RATE_TAGS.keys()}

# Dictionary of previous values for each machine's monitored tags
prev_values = defaultdict(lambda: {tag: None for tag in MONITORED_RATE_TAGS})

# Dictionary of previous values for sensitivity activation tags
prev_active_states = defaultdict(
    lambda: {tag: None for tag in SENSITIVITY_ACTIVE_TAGS}
)

# Dictionary of previous preset names per machine
prev_preset_names = defaultdict(lambda: None)


# Function to load display settings
def load_display_settings():
    """Load display settings from a JSON file"""
    try:
        if os.path.exists('display_settings.json'):
            with open('display_settings.json', 'r') as f:
                loaded_settings = json.load(f)
                
                # Convert numeric keys back to integers and keep others as-is
                settings = {}
                for key, value in loaded_settings.items():
                    if str(key).isdigit():
                        settings[int(key)] = value
                    else:
                        settings[key] = value
                
                # Return the loaded settings
                return settings
        return None
    except Exception as e:
        logger.error(f"Error loading display settings: {e}")
        return None

# Function to save display settings
def save_display_settings(settings):
    """Save display settings to a JSON file"""
    try:
        # Convert all keys to strings for JSON serialization
        json_settings = {}
        for key, value in settings.items():
            json_settings[str(key)] = value
            
        with open('display_settings.json', 'w') as f:
            json.dump(json_settings, f, indent=4)
        return True
    except Exception as e:
        logger.error(f"Error saving display settings: {e}")
        return False

# Try to load display settings at startup
try:
    loaded_display_settings = load_display_settings()
    if loaded_display_settings is not None:
        display_settings.update(loaded_display_settings)
        logger.info("Loaded display settings from file")
    else:
        logger.info("No display settings file found, using defaults")
except Exception as e:
    logger.error(f"Error updating display settings: {e}")



# Function to save IP addresses to a file
def save_ip_addresses(addresses):
    """Save IP addresses to a JSON file"""
    try:
        with open('ip_addresses.json', 'w') as f:
            json.dump(addresses, f, indent=4)
        return True
    except Exception as e:
        logger.error(f"Error saving IP addresses: {e}")
        return False

# Function to load IP addresses from a file
def load_ip_addresses():
    """Load IP addresses from a JSON file"""
    try:
        default_data = {"addresses": [{"ip": "192.168.0.125", "label": "Default"}]}
        
        if os.path.exists('ip_addresses.json'):
            with open('ip_addresses.json', 'r') as f:
                addresses = json.load(f)
                
            # Validate data structure
            if not isinstance(addresses, dict) or "addresses" not in addresses:
                logger.warning("Invalid format in ip_addresses.json, using default")
                return default_data
                
            # Ensure addresses is a list
            if not isinstance(addresses["addresses"], list):
                logger.warning("'addresses' is not a list in ip_addresses.json, using default")
                return default_data
                
            # Validate each address entry has ip and label
            valid_addresses = []
            for item in addresses["addresses"]:
                if isinstance(item, dict) and "ip" in item and "label" in item:
                    valid_addresses.append(item)
                else:
                    logger.warning(f"Invalid address entry: {item}")
            
            if valid_addresses:
                addresses["addresses"] = valid_addresses
                logger.info(f"Loaded IP addresses: {addresses}")
                return addresses
            else:
                logger.warning("No valid addresses found, using default")
                return default_data
        else:
            logger.info(f"No IP addresses file found, using default: {default_data}")
            return default_data
    except Exception as e:
        logger.error(f"Error loading IP addresses: {e}")
        default_data = {"addresses": [{"ip": "192.168.0.125", "label": "Default"}]}
        logger.info(f"Error loading IP addresses, using default: {default_data}")
        return default_data

def generate_csv_string(tags_data):
    """Return CSV data for the provided tags as a string."""

    csv_buffer = io.StringIO()
    csv_writer = csv.writer(csv_buffer)

    csv_writer.writerow(["Tag Name", "Value", "Timestamp"])

    for tag_name, tag_info in tags_data.items():
        tag_data = tag_info["data"]
        value = tag_data.latest_value
        timestamp = tag_data.timestamps[-1] if tag_data.timestamps else datetime.now()
        csv_writer.writerow([tag_name, value, timestamp])

    csv_string = csv_buffer.getvalue()
    csv_buffer.close()

    return csv_string


def generate_csv_download(tags_data):
    """Generate CSV download link from OPC UA tags."""

    csv_string = generate_csv_string(tags_data)

    csv_b64 = base64.b64encode(csv_string.encode()).decode()

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    href = f"data:text/csv;base64,{csv_b64}"
    download_link = html.A(
        tr("export_data"),
        id="download-link",
        href=href,
        download=f"satake_data_export_{timestamp_str}.csv",
        target="_blank",
        className="btn btn-success btn-sm",
    )

    return download_link


# Define the base64 encoded Satake logo (blue SATAKE with red ENPRESOR)
SATAKE_LOGO = """
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
"""

base64_image_string1 = """
iVBORw0KGgoAAAANSUhEUgAAACAAAAATCAIAAAB+9pigAAAEpElEQVQ4EY3BT4id5RUH4PM757zv+/25c2cmYlPIQkWKC1eVuhc0KQp1UQp1o5sWgqaWVs1QpaOLLJpN0irYIpSuXHbhxo3QjbRWXYiUCmmQJtQwYpKbmbn3fvd+3/f+OZWAEEht+zwwM/oaJ59+spp0+Hx9eOXg4Ea3/lb1zSNH2yN3H0l3nTn3Av1/YGb0n3z3R9+bfMMTT9HBru63cV9bbmU6Vl6U+JgLNDm38wb9LzAzusWZ3ZcP3J6bRq7apvaFlQXjysaL166urieXwz31ndWmdrLokUo2Ib62/dq58/Q1YGZ004vPPjeUyxFDvlPuuGu61WxuhC/5FqweNblIhcg0JDdYZqxvxGv74zKv+7F0i7L8+32/fvMXdBuYGRGd/vHJRbqYQvJT1MemR7e3ateGBpPWbVVORCumymUeSpkmzzxRl/fTYp7213G1tnmXlvNhedEuvXf6dx9/m24BM/v5ky+ZvdtjTY3Vm229PdlUlSZ4VX+H2/KhnujUaXAs0XpvajBzvXFZDpaiLcb1sl/FmBfjci999rdHf/Wnn9BXYGY7Tzw6yh6YMNFqsw0hTFrnNlofqtBOKqRkRYM2FXuBNFwRgXg0oIeNMc/GReps3fE89rEcdDS7cP/uW+foJvz08dOVvlN4qY3XrdBo45umbgTNhlPxG77yaiPERj9JXgK20IxiQRN56S0dxHF2eNgviEd3Iw05LRP6keLF+59/+7dEhJ3Hfij8gfniVdx2U6ORytWVx6R1Poh3WihTroQhg6vJbbuGvaKYMQql/TjO+vlqGcmkG1Ifl2KsYoe8/8kjO385g90TD7H/tHhSYtf4SoOrfOW4VC3Ec5/LYt0fsW11lWbeVr8RaogDRiMxlBXl/f5wvu7ioNlAeSBKRJwRO5r9+Q28fPw7CFeA4gt0wl6CC1oxJ9XAQgUYUmxs4oMiwplvnRN1IqkQ9xaNaB27XIYhY1k6jeYhBY5o6HP614N45fiD5i45mAip50qcBFcpw4szJkHo8qKmKRG4qBh71BrIhdgjw7gkInTR4rIM83EVRq0tOPEFw2A228IvHzuh/FGwDCOtUTtVp00tzKJgIWjJgzqOGZLVCgfzEIikIkRCyID0a5p3g5ORi/VKEPYFKVK5sQkzO/v9+yRfFSNyaBQuuKZi77QRFWGXrUtWciHK4CIKMQSHxIJEmcGifU99HDWnbNYxmBhW8pjt0wdgZs+ceune2R9pNYNmL1BPjYh4dYE3Kq1YrbCt80A5wwikRKYogBgRTIA+c4yGISUrPdOXXMpxES6//weYGd30+s+eylfepzgjl2sQPAePUPOG0+zchNgMsVhCYYMxFyIjYjMm6zMsFuSSjPqU85g0yt6Hu6cvnYKZ0S1ee/Zk3Hu3tmvkYy0InlpFFG5YJJBTx440wZTHxJ4pFiKjNNqILJHHxbiIsbt+9B8XfnP+sxNEBDOj27z5+6cW731ow6yVsVXKsABAiBkSmAs5YGDhSIbCsawyDbFQj/nBsU8uP/3qhVP0FZgZ/Vfnd34Qxn/Wy+uwlZYMKZpRhDIRJWSSMk4OV3d/8cXDZ/+6S7f5N07qg0+PBiUVAAAAAElFTkSuQmCC
"""

base64_image_string2 = """
iVBORw0KGgoAAAANSUhEUgAAAB0AAAAUCAIAAAD+/qGQAAAEqElEQVQ4EZXBS6hdVxkA4P+11tr7vO6rsTGjpqWZdFwHztriA8WBAy2COnJgwIkji8WAtGJrbY2DgIITEcRGEKkjBSeFBqTS0IeNNTfBJPXe5L7O3fucs/dez78QKLSIAb8PVRXu6ScXfp125+H2PrR3Qj8sF7O91cO/e/27cE+oqvBxF178Rbz6bth9u3Q3SY9SiRUpkWrBoKpIWVGBCrjV4vS/5599+eoz8F9QVeGuF5/6fv7XJT+/BrktViSViXgsWRFFoCgTQy4ICKpQCmYtpAgAqNWNnSfO//Nl+AhUVQD4+Zcf9YtbYViWHNGwbs6oqW0+BIyIRILCIIRFAZA05wKQURkQAVjAgMZ28rdr3/7Nzg/hLlTVC184E/q9PsScUyqIlnFzKmXNLEI2jYhlYNFEGHICgKxQiBAQCJQIVEAIBFQDbb/32HPXXwEAfOYzn5rlK8EHnzUBelM1aXLgRwdb6dGHHrh/f0Ub6lrbH+7F3ITkmZMQWCIVJQBCJaEMRUQdAQ105a3PP3/r9/jTx+8XbWMoQ4FC0o03rmyP3+qP8UF4ZP1BCFrI5lBgWFTD8QyWJys/FTCkrkIikgzZASkAh9qSBQ5H5s9v/AhfeGKLtNMMPmkm6TYnh8cnrx8Pe8thUTxNje3rWEJounwynurk9Mbw8CjPHFjLhqVoEQMqwiUZo6ykke688wC+8PgJwVUsmoEyYVibzPsTu0I+sRddtdAHj0Na+X68ZUd3+lOb3WlXZhUbo2JFiExNJSFDSYwCWGLpdgTPf+6hhPsKCkIl4wCuzZM5uOO2HHEa1h2FccGCmre61WZZbNZ5JliPiqm4XhNrxFjDpJK0C5oC+FXxDeDFJ59cLF9t0GchNJKUE0gm0zfQpegRQnZSsTEw4cgxky0iUk0KEZoZj2ZVPaklK3sNQ4mprOZ5NU+oqm9/40uvHVw+cl6shUwMUAhjUiJKBXJlpXLgmItAKYzJWMhBoytuzUw3RtPZiJAZKQ0a59rOV91Rj6oKAH//+hcvLd5cOEUyFVISCJGtgDKjAFeW1ytnx6gskPIyLPuU2MuI7puNZ2LYIBYTlPvdvhmGPgCqKtz1yre+cnXx5nKUjVqpUKO4qoAlU5GzgtNqvDEmmJRem/mqa3qf+o2K3Q1YLZd98o6xdjYc6z7m3llUVfjQb7/zzZvD5b6CccWMVI8JawZ2VW14wlCPUEdlAH/YhflKoVuLJlxeHizaJGGLuY/cxOzXTNq7D1UVPuLiU+durf56PPNG2I1YRq6SmqTYdVdZS2qdsdAqNHG535gccTvc3j2spnnL0+5tv5OGuCXN9tdQVeHj/vTLV5tbv3ojXZcpGEuuduOps9WYa1uiQZ9TyUgOfDJHGu+0VdfWsdCe7v7nYH8azHDi2dcuoarC/3Du3NmjcjWaKFaABRAUjVQMYNBz1ORGtcl1DRn/0Q4HezrvFup33vnqH/d+gKoK9/T0986X9cvzUcuGCB0bUC48raefWP+k21g7wsn7jX9959r7N26mtHv903/Y/hkAoKrC/+PsxbNnTk0mufadr2qww3h1COG9/Xm707575qW//Bju+gCYO7/WvCfMEQAAAABJRU5ErkJggg==
"""

base64_image_string3 = """
iVBORw0KGgoAAAANSUhEUgAAABsAAAAWCAIAAAC+KHDcAAAElElEQVQ4EXXBOY9eZxUA4HPOu93lW2c8xokVTJMKJCNDAQQPMYmteAApAjoafg4UNCwSBU0QBQIpClgIOQWmQQEkCldUVIgExhnPfMu9993OOUiWLGFhPw+qKrzYZz792VXNsyW1nafQXJsd4cvXfvCjH8KLoarC83zx1msD7+O2uF1aqOLCNMaEFkIbQtOqO/rVu/fgeVBV4Vl37rwOXTuonI9jfXRhxwgElvGSI792U1CoWiOhMXNY2PDJe3/8BfwPVFV46vj4C9YbaXpmW7lMaUz7fd4NyowMSwOu903Qjx36C3HWT2Tn4q92c7jyyq/feweeQFWFJz7/1euwiVGgZhUVTwjWZCSbykSiU2mUnKUDB6cLhHMl8UTWkPNE6M0Vmi8PXv35+z9FVQWAG197Lf/7HKsWIiNcvJJWY6gnEx3EUkrOWrjJ5irZj9aUHwlV46wjsOQggULmJbqj7gqq6q1vvHVWL/o9S+sSSJCMDsmoBbGWTpH1Ype4pqmEvX4KWzpqJzCp1FikDkIMBg0SGIMZAVX1W9/98ofi7LkacnOv/cJrx9ZIDPB4ypuzi7QZcso11TrVMEKjIWDTGlN7UsEI1UwQFLuWRtvgmye35+35Ztbm0TYVLs+COUS2KsSnmMezsY77zCnFksZa9oUntplAyBZriQJZ7XxQqKxO0dsO3zz53KLxs6XbdK1LBA42WsouMsdsi2pV5So1cp62tQ4FRkBFUbJMIOjBiEWsIAouY3BrvHty/WDZ2HXg1jYT7Bv4R4xwOloG2xntTC0yxWnYR9hqYI+mNc55JAw0QtKYNBcohVk0hl4O8OtvX18sQ991ZUkYMXP5Z5K4iQaTeKoKEkUT5p1KRaMIZA2RNcBOLRTwiFpqqrArAf2f//R3vPvtG7NgVrOWjnwEu9tVty//mms63xUe81QlalPCxE3dJRCxXhtv7cKTtwEAQZaCzsGWgfcHD35/H2/dve3n25kxYR2cobHlYa+Ph5jKUDVxlrIDuzGoVqxFLNbQrPOzZWuWnQOlwn2WVTVwafWTH78LAKiqx3ePsbG9kdJyoVySFFMBM4NiNRIRBtgnsdvaeLRt6Lpu1c/Xl9ezVdg53pzuP7Ez9eqrP/v+9wAAVfXmV471sAWUa5AevQIX59WVAVvTYzhy5rQz+cPtxxeF/8NzQb/oV8vWrvse/LzD2LpdLDzKe+/8Fp5AVb1zcjOuXa3YJx5DQoHL4A5f9uMhDZtxuIhyzo/3wtvcV3VtmDfULh0vTK5SGceI29w8vPcAnkBVfeObt6hrjGsnljCNh800v+T7tZk6G6dUHxeJ9aPEB2elrEwWFuYxoWQgrgxm5PDw/l/hKVRVALh98pbOTWQwaXjJp9nV5uCwlXUbgeMmT1N6tI/ubBSnQ9FpqD7a5FCT/u39h/AsVFV46sbN6ysni0MXDjx2zpCdQOI+Yi5Kwqw58zBUndByA836g9/9Af4Pqio86+Q7b9gmwdykXHOZ6pg0MhDmDFCUhdBYZ1/64Df34XlQVeEFvvT262i2yMkaRYdIVqyd8uIvv3wAL/ZfIWHMm/o7SgwAAAAASUVORK5CYII=
"""

base64_image_string4 = """
iVBORw0KGgoAAAANSUhEUgAAAB0AAAARCAIAAACuMzAjAAAEAUlEQVQ4EXXBS6hWVRQA4LX285zzP+716tW6ipbZLDALw16jQJoUIWFCqIFCVhAF1aSQGjRoFtkgyCgpjJIIAudNGlRE0KQaFUSDoiS95z9nr73W3qsQAkX8PlRVuNqR+19dm/zcN5dqUBxlZrIJpQmmXTKbp91DN3Yruzd8m7ftffAkXB+qKlx2YO8r7ez7xl6oRh0ahKooIAimcFVTAYCxVK91Ht0t83b1ppWNOzf/uG37iYdfh2ugqgLAE/ceiEu/x+CdQ3DGW2PAoJEsFQFqrUU4ZSpjzsxQ0VR11m2M9r4t3R17V9/fsvXlQ+/CFVBVj+872G75JXZNG7z13oRgTHVoEGsuRbRIpkpcSxlF0sA1kzCUUgFMRNjWxeWtrd+04Su99ewbb8NlePS2E/PtX3cz18xaH9vWO2hdNBFMZS2UsiZWKQiFoZSiPVXKWYaRcxWqpahD451ZmyCtNL/Vmz86cw4A8Mn990xXZNLaaRNtF5sQoI3ReHW2QJF1shWKR2Vh4Ex5GBJLyUSJJA+cGbSAAFhFZwGsW+CuT85/hs8/sreZ20lj21mIIcQ2YhOt8dYFqCJSq2IVwcqKmnLuh0TEKoUKEDGlyiSZNUktuWjlDmYffvkdvnTwrmbuusa1rYv/aS02jTcBfXBoBLGgFmZNGVSYdUG5auUKRDKQLIZRF0xSB1bizEOxYqzZhi8+vq+d2dhAF5q2i6232sbO2BrbGFA0VGtAq2oVyZjrQDmDENVxpDRwpsRU+8RE3CdOi2ITBr+Czz72wPJyjq1tJ74Lrg0O2hB8wBAtFMAIaMEigFGQXIX6RJw5M3HNizyUTCT9mGWQ9YGHgW02rd2Az+w/vrzjpzh3XbAhhK6xLgbvog3OBS+IwVmwwYApIddSL64T51QWPBahsYw5SZYFFerzei7jpdEm2/jNeGjnF6t3n54u/z1pQ9vYtgngsLXRTxrjg3XOxGgb742BDpPqPxcu1n6klGnIA3FKuQwyljqS9GMeepmQs90OVNUjt7+3fOe5+bSP3seIGOLEOx9c8NbGqZ9P3MRZZ6jWNBAvhkGkUk4jp8QDM4+Ux7Ko2vcZFmZqVz84dx5VFQAO7/l0affZ6exPHxBDjBa9wxCisbZrppOlSZ1FkcQE6zQypUqSUk5UBqJMQkNJXIDdxN5w+sznAICqCv879ujJ+aZvQjcGh96jM6rWG++Cd9EHnbmcZFhIllyk5iwplSTMVDTbxs1Nt+udN0/BZaiqcLWnj75gpj8Y03tbrHXOG7DBRWc8AAYiBS2iSlwLs6opYkPdeOqtj+EKqKpwHYefe8rN/vCLFGr1kdcMOrUX1MVdYQ/EX/+Km2ahb9aOHX8NrvEv/XGAcWhKhcEAAAAASUVORK5CYII=
"""

base64_image_string5 = """
iVBORw0KGgoAAAANSUhEUgAAABgAAAALCAIAAADqV9qaAAACGUlEQVQoFU3BPYudVRQF4LXWPue+9yajYoREGy00ICFt8gPstUovpHEQP5BIkFERAyYgCJYSyA+wt7e2s5WAVZxgM8yQkXvv+569l1Ugz0PbeMGdDz9av3qsfjZz0UgK61D1PtmW+9C0bubmpF7+9/TNX37+Ec/RNoDDD44uXv6TB6eto1HornAbBN2BEUHahWlgvUZ17pbqZ/u1Ljy7dPWbo0cAaPuT27eml443IqZqcjAQQKOSrUFG9agEy8qKlQxsAZ5vW41sMePK9z/8xk8P3+vTf1OrSV0rAREkOpJdCWBZRcwoZ46q2JX6kJhiLbnYHrXUtN2/yy8/uxHBHmrVs5Pq0dFCM4BMDIeRzS1dmeFSRxB7GGMMewb2Y9ru3uHRFzdIKayMubGlGtU659ZUBSMqHSKaOfoMbSBUosbI4RrJGQfny1V+ffcmyQ4ForpcsMHgkDQIjDXkFiLt5DA7V/KMqloyPSPLB6d5jd/dvYmwyFgCnbZkttAupHQfFZTXVMVA9QJ7hbGoOOYsD2DRK0+X63zw+SFff0w8W+01BziaKKkt8qoY5ZD2XZGsGs5qUQqUiJyLMDcnfPveVw9p++j939+4/sfm8l/bzT/zfsvdsMlitygFMVowlVzaXNGrgCSIsC4d89qDO/cB0DZecO/jXy+89nh98W9tThrPwll0NmlXC5IjMKFjOueVJ2dv/fTtfTz3PyJfMVz3YSNpAAAAAElFTkSuQmCC
"""
base64_image_string6 = """
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
"""
base64_image_string7 = """
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
"""
base64_image_string8 = """
iVBORw0KGgoAAAANSUhEUgAAACkAAAAQCAIAAAAEd8HEAAAD30lEQVQ4EZXBUY5d1REF0L2r6pz7ug2EELfbdroBRcofc8lEGA4TYSpRfkgUEAIZ28IYBZv33j1VtYMt8WERK8lalIT/zV/+9NnnX34K4OLO+8SS5GDMCaMLnF4tVb/roy8PXYulNlN3wBmX337zd7yJkvCm6w8/WcfvTdmAC2kwyc1ijKzKrspUywC4Ba2NWnnYtjMVTTqcYU4asztVlp006w74exfvffHVF3iNkvDaH64+MhwbIqFXGg0Pj+FL4l4ysCXzVnUXUnAzI2mvEBXwBZtT3Y2yVgmECaABUEqojp5Pv38EgPeuPznmd35K34YNszCQQef083nvc04j56iCGYRiGTeejzu6C/I2C+ewznKzloxwmW1eApaWlU7pYWoVugXLHvPy8eNv+f7v74Zg0zlMJMjNQ+aQyG42mhTbNIWj1v7zPmnLqS6tHm4cg7RT7jPZQYcg+sVEtUekOvcdQleDGKCGdbblgXev7go0Ys4pdwzraqhG0jfe8fl8FP+1VteErc3zuFP9i+pCycxI27bt1IurL3zku9E/rTlcTtA38KVX/nRye6VaZhRby3j/5obqxfZCkWEYY5TLEzK42TJydTn6tCBtM86OPO7a040FmCyGgbCkHcJlGrY6dUo4IRTkWRlmezdBQkAXeP/2RmoD07qzpwyHyFwuMmxfOYWeQaEkVG4Wp6AdV09ao7JAbDZryAtwM0MmKDEckgWPp7Pt3Q6VCKOjS9Albx/exNX24/MXhyPrTmgV3bJWJGIbGsbz4mHk3hO2HLUnJDe2Q6tNjeEbvNgtdMvp5bBsCxfow6sLS9Wl7k7B2VXAQ0oCcHX7ANmMUKXDNL3XDsFgNkLSoGG6G0vozFGKy3ixV+8ZkJut+kV3453Y5juHfSpf7tWN7sHIIBtL3aczQOB3zx7/g5Lwq/u3NwwoW2brfCIIgRCM0dzuXPT0AStHn89dAlndm8wP8eJ8zJ93GlmUE8CkcU437g7sidLqCsbTR4/wGiXhTVcPrmMwaNkooJXRYQMNmhBbdPV5ldY+fPSwKauBXMtSMKtuA4xWEBNxMVuy1jLk+YMfvvsrfkVJ+E/u3T5EN4drlQl2MbS3hztREB2ranTkwflyHYLnYK2qLG+JzuBa6SUafYzy7clX/8SbKAlvd3XzYPrQ5uoOWQ/Y3nJ2S11I+MXMzC1x9kLCjCVk7p1lkpGKcL968s3f8BuUhP/m+uMPWS2z6oVqh2vSSxwOUaKs65whyK1VpfbCrkbqx2fP8BaUhP/fvQd/vJ4Tf/7g6aPnenFSozvXylnsS89jBQxx+cOTr/F2/wZgl+BtvBxrqQAAAABJRU5ErkJggg==
"""

# Global state for the application
class AppState:
    def __init__(self):
        self.client = None
        self.connected = False
        self.last_update_time = None
        self.thread_stop_flag = False
        self.update_thread = None
        self.tags = {}  # Dictionary to store tag data
        
        
app_state = AppState()

# ---------------------------------------------------------------------------
# Memory management helpers
# ---------------------------------------------------------------------------
from memory_leak_fixes import (
    MemoryMonitor,
    CounterHistoryManager,
    ImageManager,
    DataFrameProcessor,
    AppStateManager,
)
import atexit

# Instantiate helpers so callbacks can access them
memory_monitor = MemoryMonitor()
counter_manager = CounterHistoryManager()
image_manager = ImageManager()
data_frame_processor = DataFrameProcessor()
app_state_manager = AppStateManager(app_state)
app_state_manager.start_cleanup_thread()
atexit.register(app_state_manager.stop_cleanup_thread)

# TagData class to store tag history
from threading import Lock


class TagData:
    def __init__(self, name, max_points=1000):
        self.name = name
        self.max_points = max_points
        self.timestamps = []
        self.values = []
        self.latest_value = None
        self.lock = Lock()
        
    def add_value(self, value, timestamp=None):
        """Add a new value to the tag history"""
        if timestamp is None:
            timestamp = datetime.now()
        
        with self.lock:
            self.timestamps.append(timestamp)
            self.values.append(value)
            self.latest_value = value

        # Keep only the latest max_points
        if len(self.timestamps) > self.max_points:
            self.timestamps = self.timestamps[-self.max_points:]
            self.values = self.values[-self.max_points:]
            
    def get_dataframe(self):
        """Return the tag history as a pandas DataFrame"""
        if not self.timestamps:
            return pd.DataFrame({'timestamp': [], 'value': []})
        return pd.DataFrame({'timestamp': self.timestamps, 'value': self.values})

data_saver = initialize_data_saving()

# ---------------------------------------------------------------------------
# Helper to update tag values for all connected machines
# ---------------------------------------------------------------------------
def update_machine_connections():
    """Iterate over ``machine_connections`` and refresh tag values."""
    for machine_id, info in list(machine_connections.items()):
        if not info.get("connected", False):
            continue
        tags = info.get("tags", {})
        for tag_name, tag_info in tags.items():
            try:
                value = tag_info["node"].get_value()
                tag_info["data"].add_value(value)
            except Exception as e:
                logger.debug(
                    f"Error updating tag {tag_name} for machine {machine_id}: {e}"
                )
        info["last_update"] = datetime.now()

# Initialize asyncio event loop
def get_event_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop

# Background thread for OPC UA updates
def opc_update_thread():
    """Enhanced OPC update thread with better error handling and connection validation"""
    logger.info("OPC update thread started")
    consecutive_failures = 0
    max_failures = 5
    stalled_cycles = 0
    stalled_threshold = 3
    prev_update_time = app_state.last_update_time
    
    while not app_state.thread_stop_flag:
        if prev_update_time is not None and app_state.last_update_time == prev_update_time:
            stalled_cycles += 1
            if stalled_cycles > stalled_threshold:
                logger.warning(
                    "OPC update thread has not updated for %d cycles (last update at %s)",
                    stalled_cycles,
                    app_state.last_update_time,
                )
        else:
            stalled_cycles = 0
        prev_update_time = app_state.last_update_time
        logger.debug(
            "opc_update_thread loop: mode=%s, active_machine=%s, stop_flag=%s",
            current_app_mode,
            active_machine_id,
            app_state.thread_stop_flag,
        )
        # Track read failures for this cycle
        failure_counts = defaultdict(int)
        try:
            # Always refresh tags for all connected machines
            update_machine_connections()

            # Only update if we have an active, connected machine
            if not app_state.connected or not app_state.client:
                logger.debug("No connected machine in update thread - sleeping")
                time.sleep(1)
                consecutive_failures = 0  # Reset failure count when not connected
                continue
                
            # Ensure a machine is selected before attempting updates
            if active_machine_id is None:
                logger.debug("No active machine selected in update thread - sleeping")
                time.sleep(1)
                consecutive_failures = 0
                continue

            # Verify the active machine is still in machine_connections
            if active_machine_id not in machine_connections:
                logger.warning(f"Active machine {active_machine_id} no longer in connections - stopping thread")
                break
                
            if not machine_connections[active_machine_id].get('connected', False):
                logger.warning(f"Active machine {active_machine_id} is no longer connected - stopping thread")
                break
            
            # Verify we have tags to read
            if not app_state.tags:
                logger.warning("No tags available for reading - sleeping")
                time.sleep(1)
                consecutive_failures += 1
                if consecutive_failures >= max_failures:
                    logger.error("Too many consecutive failures - stopping thread")
                    break
                continue
            
            # Test connection by reading a simple tag
            test_successful = False
            for tag_name, tag_info in list(app_state.tags.items())[:3]:  # Test first 3 tags
                try:
                    test_value = tag_info['node'].get_value()
                    test_successful = True
                    break
                except Exception as e:
                    logger.debug(f"Failed to read test tag {tag_name}: {e}")
                    continue
            
            if not test_successful:
                logger.warning("Failed to read any test tags")
                consecutive_failures += 1
                if consecutive_failures >= max_failures:
                    logger.error("Too many consecutive read failures - stopping thread")
                    break
                time.sleep(2)
                continue
            
            # Reset failure count on success
            consecutive_failures = 0
            
            # Continue with tag updates (your existing logic)
            for tag_name, tag_info in app_state.tags.items():
                try:
                    current_value = tag_info['node'].get_value()
                    tag_info['data'].add_value(current_value)
                except Exception as e:
                    logger.debug(f"Error reading tag {tag_name}: {e}")
                    failure_counts[tag_name] += 1
                    continue

            if failure_counts:
                failure_msg = ", ".join(
                    f"{name}={count}" for name, count in failure_counts.items()
                )
                logger.info("Read failures: %s", failure_msg)
            
            # Update last update time
            app_state.last_update_time = datetime.now()
            logger.info("last_update_time updated to %s", app_state.last_update_time)

            machine_connections[active_machine_id]['last_update'] = app_state.last_update_time

            # Log diagnostic counter after each update cycle
            if "Diagnostic.Counter" in app_state.tags:
                diag_value = app_state.tags["Diagnostic.Counter"]["data"].latest_value
                logger.info(
                    "Diagnostic.Counter=%s at %s",
                    diag_value,
                    datetime.now(),
                )
            
            # Sleep between updates
            time.sleep(1)
            
        except Exception as e:
            logger.error(f"Error in OPC update thread: {e}")
            consecutive_failures += 1
            if consecutive_failures >= max_failures:
                logger.error("Too many consecutive errors - stopping thread")
                break
            time.sleep(2)  # Wait before retrying
            
    logger.info("OPC update thread stopped")

# Run async function in the event loop
def run_async(coro):
    loop = get_event_loop()
    return loop.run_until_complete(coro)


def pause_update_thread():
    """Stop the background update thread if running."""
    if app_state.update_thread and app_state.update_thread.is_alive():
        app_state.thread_stop_flag = True
        app_state.update_thread.join(timeout=5)


def resume_update_thread():
    """Restart the background update thread if it is not running."""
    if app_state.update_thread is None or not app_state.update_thread.is_alive():
        app_state.thread_stop_flag = False
        app_state.update_thread = Thread(target=opc_update_thread)
        app_state.update_thread.daemon = True
        app_state.update_thread.start()
    print(f"DEBUG: resume_update_thread called, alive={app_state.update_thread.is_alive()}", flush=True)

# Connect to OPC UA server
async def connect_to_server(server_url, server_name=None):
    """Connect to the OPC UA server"""
    try:
        logger.info(f"Connecting to OPC UA server at {server_url}...")
        
        # Create client
        app_state.client = Client(server_url)
        
        # Set application name
        if server_name:
            app_state.client.application_uri = f"urn:{server_name}"
            logger.info(f"Setting application URI to: {app_state.client.application_uri}")
        
        # Connect to server
        app_state.client.connect()
        logger.info("Connected to server")
        
        # Discover tags
        await discover_tags()
        debug_discovered_tags()  # Add this line

        # Start background thread
        if app_state.update_thread is None or not app_state.update_thread.is_alive():
            app_state.thread_stop_flag = False
            app_state.update_thread = Thread(target=opc_update_thread)
            app_state.update_thread.daemon = True
            app_state.update_thread.start()
            logger.info("Started background update thread")
            
        app_state.connected = True
        app_state.last_update_time = datetime.now()
        return True
        
    except Exception as e:
        logger.error(f"Connection error: {e}")
        app_state.connected = False
        return False

def create_threshold_settings_form(lang=None):
    """Create a form for threshold settings."""
    if lang is None:
        lang = load_language_preference()
    form_rows = []

    counter_colors = {
        1: "green",
        2: "lightgreen",
        3: "orange",
        4: "blue",
        5: "#f9d70b",
        6: "magenta",
        7: "cyan",
        8: "red",
        9: "purple",
        10: "brown",
        11: "gray",
        12: "lightblue",
    }

    # Create row for each counter
    for i in range(1, 13):
        settings = threshold_settings[i]

        form_rows.append(
            dbc.Row([
                # Counter label
                dbc.Col(
                    html.Div(
                        f"{tr('sensitivity_label', lang)} {i}:",
                        className="fw-bold",
                        style={"color": counter_colors.get(i, "black")},
                    ),
                    width=2,
                ),
                                                
                # Min Value Input
                dbc.Col(
                    dbc.Input(
                        id={"type": "threshold-min-value", "index": i},
                        type="number",
                        value=settings['min_value'],
                        min=0, 
                        max=180,
                        step=1,
                        size="sm"
                    ),
                    width=1
                ),
                
                # Min Enable Switch
                dbc.Col(
                    dbc.Switch(
                        id={"type": "threshold-min-enabled", "index": i},
                        label="Min",
                        value=settings['min_enabled'],
                        className="medium"
                    ),
                    width=2
                ),

                                
                # Max Value Input
                dbc.Col(
                    dbc.Input(
                        id={"type": "threshold-max-value", "index": i},
                        type="number",
                        value=settings['max_value'],
                        min=0,
                        max=200,
                        step=1,
                        size="sm"
                    ),
                    width=1
                ),

                # Max Enable Switch
                dbc.Col(
                    dbc.Switch(
                        id={"type": "threshold-max-enabled", "index": i},
                        label="Max",
                        value=settings['max_enabled'],
                        className="medium"
                    ),
                    width=2
                ),

            ], className="mb-2")
        )
    
    # Add email notifications with email and minutes inputs
    form_rows.append(
        dbc.Row([
            # Label
            dbc.Col(html.Div(f"{tr('notification_label', lang)}:", className="fw-bold"), width=2),
            
            # Email Input
            dbc.Col(
                dbc.Input(
                    id="threshold-email-address",
                    type="email",
                    placeholder="Email address",
                    value=threshold_settings.get('email_address', ''),
                    size="sm"
                ),
                width=3
            ),
            
            # Minutes Input
            dbc.Col(
                dbc.InputGroup([
                    dbc.Input(
                        id="threshold-email-minutes",
                        type="number",
                        min=1,
                        max=60,
                        step=1,
                        value=threshold_settings.get('email_minutes', 2),
                        size="sm"
                    ),
                    dbc.InputGroupText("min", className="p-1 small"),
                ], size="sm"),
                width=1
            ),
            
            # Enable Switch
            dbc.Col(
                dbc.Switch(
                    id="threshold-email-enabled",
                    value=threshold_settings.get('email_enabled', False),
                    className="medium"
                ),
                width=2
            ),
        ], className="mt-3 mb-2")  # Added margin top to separate from sensitivity rows
    )
    
    return form_rows


try:
    loaded_settings = load_threshold_settings()
    if loaded_settings:
        threshold_settings.update(loaded_settings)
        logger.info("Threshold settings loaded and applied")
except Exception as e:
    logger.error(f"Error loading threshold settings: {e}")

# Discover available tags
async def discover_tags():
    """Discover available tags on the server"""
    if not app_state.client:
        return False
        
    try:
        logger.info("Discovering tags...")
        root = app_state.client.get_root_node()
        objects = app_state.client.get_objects_node()
        
        # Clear existing tags
        app_state.tags = {}
        
        # First, try to connect to all known tags explicitly
        logger.info("Attempting to connect to known tags...")
        for tag_name, node_id in KNOWN_TAGS.items():
            if tag_name not in FAST_UPDATE_TAGS:
                continue
            try:
                node = app_state.client.get_node(node_id)
                value = node.get_value()
                
                logger.info(f"Successfully connected to known tag: {tag_name} = {value}")
                
                # Add to tags
                tag_data = TagData(tag_name)
                tag_data.add_value(value)
                app_state.tags[tag_name] = {
                    'node': node,
                    'data': tag_data
                }
            except Exception as e:
                logger.warning(f"Could not connect to known tag {tag_name} ({node_id}): {e}")
        
        # Then do the existing discovery process for any additional tags
        logger.info("Performing additional tag discovery...")
        
        # Function to recursively browse nodes
        async def browse_nodes(node, level=0, max_level=3):
            if level > max_level:
                return
                
            try:
                children = node.get_children()
                for child in children:
                    try:
                        name = child.get_browse_name().Name
                        node_class = child.get_node_class()
                        
                        # If it's a variable, add it to our tags (if not already added)
                        if node_class == ua.NodeClass.Variable:
                            try:
                                # Skip if name already exists or is not in FAST_UPDATE_TAGS
                                if name in app_state.tags or name not in FAST_UPDATE_TAGS:
                                    continue
                                    
                                value = child.get_value()
                                logger.debug(f"Found additional tag: {name} = {value}")
                                
                                tag_data = TagData(name)
                                tag_data.add_value(value)
                                app_state.tags[name] = {
                                    'node': child,
                                    'data': tag_data
                                }
                            except Exception:
                                pass
                        
                        # Continue browsing deeper
                        await browse_nodes(child, level + 1, max_level)
                    except Exception:
                        pass
            except Exception:
                pass
        
        # Start browsing from objects node with limited depth
        await browse_nodes(objects, 0, 2)
        
        logger.info(f"Total tags discovered: {len(app_state.tags)}")
        
        # Log specifically if our test weight tags were found
        if "Settings.ColorSort.TestWeightValue" in app_state.tags:
            weight_value = app_state.tags["Settings.ColorSort.TestWeightValue"]["data"].latest_value
            logger.info(f"✓ TestWeightValue tag found with value: {weight_value}")
        else:
            logger.warning("✗ TestWeightValue tag NOT found")
            
        if "Settings.ColorSort.TestWeightCount" in app_state.tags:
            count_value = app_state.tags["Settings.ColorSort.TestWeightCount"]["data"].latest_value
            logger.info(f"✓ TestWeightCount tag found with value: {count_value}")
        else:
            logger.warning("✗ TestWeightCount tag NOT found")
        
        return True
        
    except Exception as e:
        logger.error(f"Error discovering tags: {e}")
        return False

# Disconnect from OPC UA server
async def disconnect_from_server():
    try:
        logger.info("Disconnecting from server...")
        
        # Stop background thread
        if app_state.update_thread and app_state.update_thread.is_alive():
            app_state.thread_stop_flag = True
            app_state.update_thread.join(timeout=5)
            
        # Disconnect client
        if app_state.client:
            app_state.client.disconnect()
            
        app_state.connected = False
        logger.info("Disconnected from server")
        return True
        
    except Exception as e:
        logger.error(f"Disconnection error: {e}")
        return False

def debug_discovered_tags():
    """Write discovered tags to a file to see what's actually available"""
    import os
    
    # Use absolute path so we know exactly where it goes
    file_path = os.path.abspath('discovered_tags.txt')
    logger.info(f"Writing {len(app_state.tags)} discovered tags to: {file_path}")
    
    try:
        with open(file_path, 'w') as f:
            f.write(f"Total tags discovered: {len(app_state.tags)}\n\n")
            
            # Group tags by category to make it easier to read
            categories = {}
            
            for tag_name, tag_info in app_state.tags.items():
                try:
                    value = tag_info['data'].latest_value
                    node_id = str(tag_info['node'].nodeid)
                    
                    # Try to categorize by the first part of the name
                    category = tag_name.split('.')[0] if '.' in tag_name else 'Other'
                    if category not in categories:
                        categories[category] = []
                    
                    categories[category].append({
                        'name': tag_name,
                        'node_id': node_id,
                        'value': value
                    })
                    
                except Exception as e:
                    category = 'Errors'
                    if category not in categories:
                        categories[category] = []
                    categories[category].append({
                        'name': tag_name,
                        'node_id': 'unknown',
                        'value': f'Error: {e}'
                    })
            
            # Write organized output
            for category, tags in sorted(categories.items()):
                f.write(f"\n=== {category.upper()} TAGS ===\n")
                for tag in tags[:50]:  # Limit to first 50 per category
                    f.write(f"Name: {tag['name']}\n")
                    f.write(f"NodeID: {tag['node_id']}\n") 
                    f.write(f"Value: {tag['value']}\n\n")
                
                if len(tags) > 50:
                    f.write(f"... and {len(tags) - 50} more tags in this category\n\n")
        
        logger.info(f"SUCCESS: Tag discovery results written to: {file_path}")
        
    except Exception as e:
        logger.error(f"ERROR writing file: {e}")


async def discover_all_tags(client):
    """Return a dict of all tags available from the OPC server."""
    tags = {}

    try:
        objects = client.get_objects_node()

        async def browse_nodes(node, level=0, max_level=3):
            if level > max_level:
                return
            try:
                children = node.get_children()
                for child in children:
                    try:
                        name = child.get_browse_name().Name
                        node_class = child.get_node_class()
                        if node_class == ua.NodeClass.Variable:
                            if name not in tags:
                                try:
                                    value = child.get_value()
                                    tag_data = TagData(name)
                                    tag_data.add_value(value)
                                    tags[name] = {"node": child, "data": tag_data}
                                except Exception:
                                    pass
                        await browse_nodes(child, level + 1, max_level)
                    except Exception:
                        pass
            except Exception:
                pass

        await browse_nodes(objects, 0, 2)
        logger.info(f"Full tag discovery found {len(tags)} tags")
    except Exception as e:
        logger.error(f"Error during full tag discovery: {e}")

    return tags

def load_theme_preference():
    """Load theme preference from display_settings.json"""
    try:
        # Check if the settings file exists
        if os.path.exists('display_settings.json'):
            with open('display_settings.json', 'r') as f:
                try:
                    settings = json.load(f)
                    theme = settings.get('app_theme', 'light')
                    logger.info(f"Loaded theme from file: {theme}")
                    return theme
                except json.JSONDecodeError:
                    logger.warning("display_settings.json is corrupted, using default theme")
                    return 'light'
        else:
            logger.info("display_settings.json doesn't exist, using default theme")
            return 'light'  # Default theme if file doesn't exist
            
    except Exception as e:
        logger.error(f"Error loading theme preference: {e}")
        return 'light'  # Default to light theme in case of error


DEFAULT_WEIGHT_PREF = {"unit": "lb", "label": "lbs", "value": 1.0}

def load_weight_preference():
    """Load capacity unit preference from display_settings.json"""
    try:
        if DISPLAY_SETTINGS_PATH.exists():
            with open(DISPLAY_SETTINGS_PATH, 'r') as f:
                settings = json.load(f)
                return {
                    "unit": settings.get('capacity_unit', 'lb'),
                    "label": settings.get('capacity_custom_label', ''),
                    "value": settings.get('capacity_custom_value', 1.0),
                }
    except Exception as e:
        logger.error(f"Error loading capacity unit preference: {e}")
    return DEFAULT_WEIGHT_PREF.copy()


def save_weight_preference(unit, label="", value=1.0):
    """Save capacity unit preference to display_settings.json"""
    try:
        settings = {}
        if DISPLAY_SETTINGS_PATH.exists():
            with open(DISPLAY_SETTINGS_PATH, 'r') as f:
                try:
                    settings = json.load(f)
                except json.JSONDecodeError:
                    settings = {}

        settings['capacity_unit'] = unit
        settings['capacity_custom_label'] = label
        settings['capacity_custom_value'] = value

        with open(DISPLAY_SETTINGS_PATH, 'w') as f:
            json.dump(settings, f, indent=4)
        logger.info(f"Saved capacity unit preference: {unit}")
        return True
    except Exception as e:
        logger.error(f"Error saving capacity unit preference: {e}")
        return False


DEFAULT_LANGUAGE = "en"

def load_language_preference():
    """Load UI language preference from ``display_settings.json``"""
    try:
        if DISPLAY_SETTINGS_PATH.exists():
            with open(DISPLAY_SETTINGS_PATH, 'r') as f:
                settings = json.load(f)
                return settings.get('language', DEFAULT_LANGUAGE)
    except Exception as e:
        logger.error(f"Error loading language preference: {e}")
    return DEFAULT_LANGUAGE


def save_language_preference(language):
    """Save UI language preference to ``display_settings.json``"""
    try:
        settings = {}
        if DISPLAY_SETTINGS_PATH.exists():
            with open(DISPLAY_SETTINGS_PATH, 'r') as f:
                try:
                    settings = json.load(f)
                except json.JSONDecodeError:
                    settings = {}

        settings['language'] = language

        with open(DISPLAY_SETTINGS_PATH, 'w') as f:
            json.dump(settings, f, indent=4)
        logger.info(f"Saved language preference: {language}")
        global _initial_lang
        _initial_lang = language
        return True
    except Exception as e:
        logger.error(f"Error saving language preference: {e}")
        return False


def convert_capacity_from_kg(value_kg, pref):
    """Convert capacity from kilograms based on selected unit preference"""
    if value_kg is None:
        return 0
    unit = pref.get('unit', 'lb')
    if unit == 'kg':
        return value_kg
    lbs = value_kg * 2.205
    if unit == 'lb':
        return lbs
    if unit == 'custom':
        per_unit = pref.get('value', 1.0)
        if per_unit:
            return lbs / per_unit
        return 0
    return lbs


def convert_capacity_to_lbs(value, pref):
    """Convert a capacity value based on selected unit preference to pounds."""
    if value is None:
        return 0
    unit = pref.get('unit', 'lb')
    if unit == 'kg':
        return value * 2.205
    if unit == 'lb':
        return value
    if unit == 'custom':
        per_unit = pref.get('value', 1.0)
        return value * per_unit
    return value


def convert_capacity_from_lbs(value_lbs, pref):
    """Convert a capacity value in pounds to the preferred display unit."""
    if value_lbs is None:
        return 0
    unit = pref.get('unit', 'lb')
    if unit == 'kg':
        return value_lbs / 2.205
    if unit == 'lb':
        return value_lbs
    if unit == 'custom':
        per_unit = pref.get('value', 1.0)
        if per_unit:
            return value_lbs / per_unit
        return 0
    return value_lbs


def capacity_unit_label(pref, per_hour=True):
    unit = pref.get('unit', 'lb')
    if unit == 'kg':
        label = 'kg'
    elif unit == 'lb':
        label = 'lbs'
    else:
        label = pref.get('label', 'unit')
    return f"{label}/hr" if per_hour else label





initial_image_data = load_saved_image()
logger.info(f"Initial image data: {'' if not initial_image_data else 'Image loaded'}")

# Initialize Dash app if Dash is available
if dash is not None:
    app = dash.Dash(
        __name__,
        external_stylesheets=[dbc.themes.BOOTSTRAP],
        meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
        suppress_callback_exceptions=True,
    )
    app.title = tr("dashboard_title")
else:  # pragma: no cover - optional dependency
    app = None

# Create the modal for threshold settings - to be included in the app layout
threshold_modal = dbc.Modal([
    dbc.ModalHeader(html.Span(tr("threshold_settings_title"), id="threshold-modal-header")),
    dbc.ModalBody([
        html.Div(id="threshold-form-container", children=create_threshold_settings_form(load_language_preference()))
    ]),
    dbc.ModalFooter([
        dbc.Button(tr("close"), id="close-threshold-settings", color="secondary", className="me-2"),
        dbc.Button(tr("save_changes"), id="save-threshold-settings", color="primary")
    ])
], id="threshold-modal", size="xl", is_open=False)

# Create the modal for display settings
display_modal = dbc.Modal([
    dbc.ModalHeader(html.Span(tr("display_settings_title"), id="display-modal-header")),
    dbc.ModalBody([
        html.Div(id="display-form-container", children=[
            html.P(tr("display_settings_header"), id="display-modal-description"),
            # Will be populated with checkboxes in the callback
        ])
    ]),
    dbc.ModalFooter([
        dbc.Button(tr("close"), id="close-display-settings", color="secondary", className="me-2"),
        dbc.Button(tr("save_changes"), id="save-display-settings", color="primary")
    ])
], id="display-modal", size="lg", is_open=False)

# Modal to select units for the production rate chart
units_modal = dbc.Modal([
    dbc.ModalHeader(html.Span(tr("production_rate_units_title"), id="production-rate-units-header")),
    dbc.ModalBody(
        dbc.RadioItems(
            id="production-rate-unit-selector",
            options=[
                {"label": tr("objects_per_min"), "value": "objects"},
                {"label": tr("capacity"), "value": "capacity"},
            ],
            value="objects",
            inline=True,
        )
    ),
    dbc.ModalFooter([
        dbc.Button(tr("close"), id="close-production-rate-units", color="secondary", className="me-2"),
        dbc.Button(tr("save"), id="save-production-rate-units", color="primary"),
    ])
], id="production-rate-units-modal", is_open=False)

# Add this code right after app initialization
app.index_string = """<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
        <style>
            :root {
                /* Default (light) theme variables */
                --bs-body-bg: #f0f0f0;
                --bs-body-color: #212529;
                --bs-card-bg: #ffffff;
                --bs-card-border-color: rgba(0,0,0,0.125);
                --chart-bg: rgba(255,255,255,0.9);
            }
            
            /* Blinking animation for feeder running indicator */
            @keyframes blink {
                0%, 50% { opacity: 1; }
                51%, 100% { opacity: 0; }
            }

            body {
                margin: 0;
                background-color: var(--bs-body-bg) !important;
                color: var(--bs-body-color) !important;
                transition: background-color 0.3s, color 0.3s;
            }

            /* Ensure radio buttons remain visible regardless of theme */
            input[type="radio"] {
                accent-color: var(--bs-body-color);
                border: 1px solid var(--bs-body-color);

            }

            /* Ensure selector switches remain visible regardless of theme */
            input[type="checkbox"] {
                accent-color: var(--bs-body-color);
                border: 1px solid var(--bs-body-color);
            }
            
            /* Card styling with variables */
            .card {
                margin-bottom: 0.5rem;
                box-shadow: 0 2px 5px rgba(0,0,0,0.1);
                background-color: var(--bs-card-bg) !important;
                border-color: var(--bs-card-border-color) !important;
                color: var(--bs-body-color) !important;
                transition: background-color 0.3s, color 0.3s, border-color 0.3s;
            }

            /* Ensure Bootstrap contextual background classes override
               the generic card background color */
            .card.bg-primary {
                background-color: #0d6efd !important;
                color: #fff !important;
            }
            
            /* Dark mode specific overrides */
            body.dark-mode .card {
                box-shadow: 0 2px 5px rgba(0,0,0,0.3);
            }
            
            body.dark-mode .modal-content {
                background-color: #2d2d30;
                color: #e8eaed;
            }
            
            body.dark-mode .modal-header {
                border-bottom-color: rgba(255,255,255,0.125);
            }
            
            body.dark-mode .modal-footer {
                border-top-color: rgba(255,255,255,0.125);
            }
            
            body.dark-mode .form-control,
            body.dark-mode .form-select,
            body.dark-mode .input-group-text {
                background-color: #3c4043;
                color: #e8eaed;
                border-color: rgba(255,255,255,0.125);
            }
            
            body.dark-mode .dropdown-menu {
                background-color: #2d2d30;
                color: #e8eaed;
                border-color: rgba(255,255,255,0.125);
            }
            
            body.dark-mode .dropdown-item {
                color: #e8eaed;
            }
            
            body.dark-mode .dropdown-item:hover {
                background-color: #3c4043;
            }
            
            /* Dark mode specific overrides for dropdowns */
            body.dark-mode .Select-control,
            body.dark-mode .Select-menu-outer,
            body.dark-mode .Select-value,
            body.dark-mode .Select-value-label,
            body.dark-mode .Select input,
            body.dark-mode .Select-placeholder,
            body.dark-mode .has-value.Select--single>.Select-control .Select-value .Select-value-label,
            body.dark-mode .has-value.is-pseudo-focused.Select--single>.Select-control .Select-value .Select-value-label {
                color: #e8eaed !important;
                background-color: #3c4043 !important;
            }
            
            body.dark-mode .Select-control {
                border-color: rgba(255,255,255,0.2) !important;
            }
            
            body.dark-mode .Select-menu-outer {
                background-color: #2d2d30 !important;
                border-color: rgba(255,255,255,0.2) !important;
            }
            
            body.dark-mode .Select-option {
                background-color: #2d2d30 !important;
                color: #e8eaed !important;
            }
            
            body.dark-mode .Select-option:hover,
            body.dark-mode .Select-option.is-focused {
                background-color: #4d4d50 !important;
            }
            
            body.dark-mode .Select-arrow {
                border-color: #e8eaed transparent transparent !important;
            }
            
            /* Fix for Dash dropdown components in dark mode */
            body.dark-mode .dash-dropdown .Select-control,
            body.dark-mode .dash-dropdown .Select-menu-outer,
            body.dark-mode .dash-dropdown .Select-value,
            body.dark-mode .dash-dropdown .Select-value-label {
                color: #e8eaed !important;
                background-color: #3c4043 !important;
            }
            
            /* Fix for selected option in dark mode */

            body.dark-mode .Select.is-focused:not(.is-open)>.Select-control {
                background-color: #3c4043 !important;
                border-color: rgba(255,255,255,0.5) !important;
            }

            /* Light mode overrides for dropdowns */
            body.light-mode .Select-control,
            body.light-mode .Select-menu-outer,
            body.light-mode .Select-value,
            body.light-mode .Select-value-label,
            body.light-mode .Select input,
            body.light-mode .Select-placeholder,
            body.light-mode .has-value.Select--single>.Select-control .Select-value .Select-value-label,
            body.light-mode .has-value.is-pseudo-focused.Select--single>.Select-control .Select-value .Select-value-label {
                color: #343a40 !important;
                background-color: #ffffff !important;
            }

            body.light-mode .Select-control {
                border-color: rgba(0,0,0,0.2) !important;
            }

            body.light-mode .Select-menu-outer {
                background-color: #ffffff !important;
                border-color: rgba(0,0,0,0.2) !important;
            }

            body.light-mode .Select-option {
                background-color: #ffffff !important;
                color: #343a40 !important;
            }

            body.light-mode .Select-option:hover,
            body.light-mode .Select-option.is-focused {
                background-color: #e9ecef !important;
            }

            body.light-mode .Select-arrow {
                border-color: #343a40 transparent transparent !important;
            }

            body.light-mode .dash-dropdown .Select-control,
            body.light-mode .dash-dropdown .Select-menu-outer,
            body.light-mode .dash-dropdown .Select-value,
            body.light-mode .dash-dropdown .Select-value-label {
                color: #343a40 !important;
                background-color: #ffffff !important;
            }

            body.light-mode .Select.is-focused:not(.is-open)>.Select-control {
                background-color: #ffffff !important;
                border-color: rgba(0,0,0,0.5) !important;
            }

            
            /* Existing styles with some adaptations */
            .card-body {
                padding: 0.5rem;
            }

            
            .container-fluid {
                padding-left: 0.5rem;
                padding-right: 0.5rem;
            }
            
            .row {
                margin-left: -0.25rem;
                margin-right: -0.25rem;
            }
            
            .col, [class*="col-"] {
                padding-left: 0.25rem;
                padding-right: 0.25rem;
            }
            
            .js-plotly-plot .plotly .main-svg {
                height: calc(100% - 5px);
            }
            
            h5 {
                margin-bottom: 0.5rem !important;
                font-size: 1rem !important;
            }
            
            h6 {
                margin-bottom: 0.25rem !important;
                font-size: 0.875rem !important;
            }
            
            /* Historical slider styling */
            .mode-controls-container .slider-container {
                padding: 0;
                margin: 0;
            }
            
            .mode-controls-container .rc-slider {
                height: 14px;
            }
            
            .mode-controls-container .rc-slider-rail {
                height: 4px;
            }
            
            .mode-controls-container .rc-slider-track {
                height: 4px;
                background-color: #007bff;
            }
            
            .mode-controls-container .rc-slider-handle {
                margin-top: -5px;
                width: 14px;
                height: 14px;
            }
            
            .mode-controls-container .rc-slider-tooltip {
                font-size: 0.7rem;
                padding: 2px 5px;
            }

            /* Machine Card Color Overrides - Add this to your existing <style> section */
            .machine-card-connected {
                background-color: #28a745 !important;
                color: white !important;
                border-color: #28a745 !important;
            }

            .machine-card-disconnected {
                background-color: #d3d3d3 !important;
                color: black !important;
                border-color: #a9a9a9 !important;
            }

            .machine-card-active-connected {
                background-color: #28a745 !important;
                color: white !important;
                border: 3px solid #007bff !important;
                box-shadow: 0 4px 8px rgba(0,123,255,0.3) !important;
            }

            .machine-card-active-disconnected {
                background-color: #d3d3d3 !important;
                color: black !important;
                border: 3px solid #007bff !important;
                box-shadow: 0 4px 8px rgba(0,123,255,0.3) !important;
            }

            /* Floor management button styles */
            .delete-floor-btn {
                width: 1.6875rem;
                height: 90%;
                border-radius: 10%;
                padding: 0;
            }
            .delete-floor-btn-inline {
                width: 1.875rem;
                height: 1.875rem;
                border-radius: 50%;
                margin-right: 0.3125rem;
            }
            .edit-floor-name-btn {
                font-size: 1.5rem;
                padding: 0.3rem;
            }

            .floor-header-text {
                font-size: clamp(2rem, 8vw, 3.8rem);
                font-weight: bold;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                width: 100%;
                flex: 1;
                min-width: 0;

            }

            .floor-tile-btn {
                font-size: clamp(0.9rem, 4vw, 1.25rem);
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                width: 100%;
                flex: 1;
                min-width: 0;

            }

            /* Ensure dropdown and production text fit within machine cards */
            .machine-card-dropdown {
                width: 100%;
            }
            .production-data {
                font-size: 2.6rem;
                font-weight: bold;
                font-family: Monaco, Consolas, 'Courier New', monospace;
            }

            /* Responsive tweaks for very small screens */
            @media (max-width: 576px) {
                h5 {
                    font-size: 0.9rem !important;
                }
                h6 {
                    font-size: 0.8rem !important;
                }
                .card-body {
                    padding: 0.25rem;
                }
                .delete-floor-btn {
                    width: 1.125rem;
                    height: 90%;
                    font-size: 0.7rem;
                    padding: 0;
                }
                .delete-floor-btn-inline {
                    width: 1.25rem;
                    height: 1.25rem;
                    font-size: 0.7rem;
                }
                .edit-floor-name-btn {
                    font-size: 1rem;
                    padding: 0.2rem;
                }
                .floor-header-text {
                    font-size: 2rem !important;
                    width: 100%;
                    flex: 1;
                    min-width: 0;

                }
                .floor-tile-btn {
                    font-size: 0.9rem !important;
                    width: 100%;
                    flex: 1;
                    min-width: 0;

                }
                .machine-info-container {
                    flex-direction: row;
                    flex-wrap: wrap;
                    height: auto !important;
                }
                #section-3-2 > div {
                    height: auto !important;
                }
                .machine-info-logo {

                    flex: 0 0 45%;
                    max-width: 180px; /* Increase size to reduce gap */

                }
                .production-data {
                    font-size: 1.6rem !important;
                }
                .machine-card-dropdown {
                    font-size: 0.9rem !important;
                }
            }
        </style>
        <script>
            // Initialize theme from localStorage on page load
            document.addEventListener('DOMContentLoaded', function() {
                // Get saved theme from localStorage (backup to display_settings.json)
                const savedTheme = localStorage.getItem('satake-theme');
                
                if (savedTheme) {
                    // This will be handled by the theme-selector through callbacks,
                    // but we need to set initial state for the radio buttons
                    setTimeout(function() {
                        const themeSelectorDark = document.querySelector('input[value="dark"]');
                        const themeSelectorLight = document.querySelector('input[value="light"]');
                        
                        if (savedTheme === "dark" && themeSelectorDark) {
                            themeSelectorDark.checked = true;
                            // Trigger a change event to apply the theme immediately
                            themeSelectorDark.dispatchEvent(new Event('change', { bubbles: true }));
                        } else if (savedTheme === "light" && themeSelectorLight) {
                            themeSelectorLight.checked = true;
                            // Trigger a change event to apply the theme immediately
                            themeSelectorLight.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }, 500); // Small delay to ensure components are loaded
                }
            });
        </script>
    </head>
    <body class="light-mode">
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>"""

# Empty div to be used in each grid section
empty_section = html.Div("Empty section", className="border p-2 h-100")


# Create connection controls
_initial_lang = load_language_preference()

def connection_controls(lang=_initial_lang):
    return dbc.Card(
        dbc.CardBody([
            dbc.Row([
                # Active Machine Info (replacing IP dropdown section)
                dbc.Col([
                    html.Div([
                    html.Span(tr("active_machine_label", lang), id="active-machine-label", className="fw-bold small me-1"),
                    html.Span(id="active-machine-display", className="small"),
                ], className="mt-1"),
            ], width={"xs":2, "md":2}, className="px-1"),
            
            # Status (keep this)
            dbc.Col([
                html.Div([
                    html.Span(tr("status_label", lang), id="status-label", className="fw-bold small me-1"),
                    html.Span(tr("no_machine_selected", lang), id="connection-status", className="text-warning small"),
                ], className="mt-1 ms-2"),
            ], width={"xs":2, "md":2}, className="px-1"),
            
            # Mode Selector (keep this)
            dbc.Col([
                dcc.Dropdown(
                    id="mode-selector",
                    options=[
                        {"label": tr("live_mode_option", lang), "value": "live"},
                        {"label": tr("demo_mode_option", lang), "value": "demo"},
                        {"label": tr("historical_mode_option", lang), "value": "historical"},
                        {"label": tr("lab_test_mode_option", lang), "value": "lab"},
                    ],
                    value="live",  # Default to live mode
                    clearable=False,
                    searchable=False,
                    className="small p-0",
                    style={"min-width": "80px"}
                ),
            ], width={"xs":1, "md":1}, className="px-1"),

            # Lab Test Controls
            dbc.Col([
                html.Div(
                    id="lab-test-controls",
                    className="d-none",
                    children=[
                        dbc.Button(tr("start_test", lang), id="start-test-btn", color="success", size="sm", className="py-0 me-1"),
                        dbc.Button(tr("stop_test", lang), id="stop-test-btn", color="danger", size="sm", className="py-0 me-1"),
                        dbc.Input(
                            id="lab-test-name",
                            type="text",
                            placeholder=tr("test_lot_name_placeholder", lang),
                            size="sm",
                            className="me-1",
                            style={"maxWidth": "150px"}
                        ),
                        dbc.Button(tr("clear_data", lang), id="clear-data-btn", color="secondary", size="sm", className="py-0"),
                    ],
                ),
            ], width={"xs":2, "md":2}, className="px-1"),
            
            # Historical Time Slider (keep this)
            dbc.Col([
                html.Div(id="historical-time-controls", className="d-none", children=[
                    dcc.Slider(
                        id="historical-time-slider",
                        min=1,
                        max=24,
                        step=None,
                        value=24,
                        marks={
                            1: {"label": "1hr", "style": {"fontSize": "8px"}},
                            4: {"label": "4hr", "style": {"fontSize": "8px"}},
                            8: {"label": "8hr", "style": {"fontSize": "8px"}},
                            12: {"label": "12hr", "style": {"fontSize": "8px"}},
                            24: {"label": "24hr", "style": {"fontSize": "8px"}},
                        },
                        included=False,
                        className="mt-1",
                    ),
                    html.Div(
                        id="historical-time-display",
                        className="small text-info text-center",
                        style={"whiteSpace": "nowrap", "fontSize": "0.7rem", "marginTop": "-2px"}
                    )
                ]),
            ], width={"xs":2, "md":2}, className="px-1"),
            
            # Settings and Export buttons (keep this)
            dbc.Col([
                html.Div([
                    dbc.ButtonGroup([
                        dbc.Button(
                            html.I(className="fas fa-cog"),
                            id="settings-button",
                            color="secondary",
                            size="sm",
                            className="py-0 me-1",
                            style={"width": "38px"}
                        ),
                        html.Div(
                            id="export-button-container",
                            className="d-inline-block",
                            children=[
                                dbc.Button(
                                    tr("export_data", lang),
                                    id="export-data-button",
                                    color="primary",
                                    size="sm",
                                    className="py-0",
                                    disabled=True,
                                ),
                                dcc.Download(id="export-download"),
                            ],
                        )
                    ], className="")
                ], className="text-end"),
            ], width={"xs":2, "md":2}, className="px-1"),

            # Hidden Name field (keep this)
            dbc.Col([
                dbc.Input(
                    id="server-name-input", 
                    value="Satake.EvoRGB.1", 
                    type="hidden"
                ),
            ], width=0, style={"display": "none"}),
        ], className="g-0 align-items-center"),
    ], className="py-1 px-2"),
    className="mb-1 mt-0",
    )
settings_modal = dbc.Modal([
    dbc.ModalHeader(html.Span(tr("system_settings_title"), id="settings-modal-header")),
    dbc.ModalBody([
        dbc.Tabs([
            # Theme settings tab remains the same
            dbc.Tab([
                html.Div([
                    html.P(tr("display_settings_title"), className="lead mt-2", id="display-settings-subtitle"),
                    html.Hr(),

                    # Theme selector
                    dbc.Row([
                        dbc.Col([
                            dbc.Label(tr("color_theme_label"), className="fw-bold", id="color-theme-label"),
                        ], width=4),
                        dbc.Col([
                            dbc.RadioItems(
                                id="theme-selector",
                                options=[
                                    {"label": tr("light_mode_option"), "value": "light"},
                                    {"label": tr("dark_mode_option"), "value": "dark"},
                                ],
                                value="light",
                                inline=True
                            ),
                        ], width=8),
                    ], className="mb-3"),

                    # Capacity units selector
                    dbc.Row([
                        dbc.Col([
                            dbc.Label(tr("capacity_units_label"), className="fw-bold", id="capacity-units-label"),
                        ], width=4),
                        dbc.Col([
                            dbc.RadioItems(
                                id="capacity-units-selector",
                                options=[
                                    {"label": "Kg", "value": "kg"},
                                    {"label": "Lbs", "value": "lb"},
                                    {"label": "Custom", "value": "custom"},
                                ],
                                value="lb",
                                inline=True,
                            ),
                            dbc.Input(id="custom-unit-name", type="text", placeholder="Unit Name", className="mt-2", style={"display": "none"}),
                            dbc.Input(id="custom-unit-weight", type="number", placeholder="Weight in lbs", className="mt-2", style={"display": "none"}),
                        ], width=8),
                    ], className="mb-3"),

                    # Language selector
                    dbc.Row([
                        dbc.Col([
                            dbc.Label(tr("language_label"), className="fw-bold", id="language-label"),
                        ], width=4),
                        dbc.Col([
                            dbc.RadioItems(
                                id="language-selector",
                                options=[
                                    {"label": tr("english_option"), "value": "en"},
                                    {"label": tr("spanish_option"), "value": "es"},
                                    {"label": tr("japanese_option"), "value": "ja"},
                                ],
                                value="en",
                                inline=True,
                            ),
                        ], width=8),
                    ], className="mb-3"),
                ])
            ], label=tr("display_tab_label", _initial_lang), id="display-tab"),
            
            # Updated System tab with "Add machine IP" and ADD button
            dbc.Tab([
                html.Div([
                    html.P(tr("system_configuration_title"), className="lead mt-2", id="system-configuration-title"),
                    html.Hr(),
                    dbc.Row([
                        dbc.Col([
                            dbc.Label(tr("auto_connect_label"), id="auto-connect-label"),
                        ], width=8),
                        dbc.Col([
                            dbc.Switch(
                                id="auto-connect-switch",
                                value=True,
                                className="float-end"
                            ),
                        ], width=4),
                    ], className="mb-3"),
                    
                    # Changed label and added ADD button
                    dbc.Row([
                        dbc.Col([
                            dbc.Label(tr("add_machine_ip_label"), id="add-machine-ip-label"),
                        ], width=3),
                        dbc.Col([
                            dbc.InputGroup([
                                # Label input
                                dbc.Input(
                                    id="new-ip-label",
                                    value="",
                                    type="text",
                                    placeholder=tr("machine_name_placeholder"),
                                    size="sm"
                                ),
                                # IP input
                                dbc.Input(
                                    id="new-ip-input",
                                    value="",
                                    type="text",
                                    placeholder=tr("ip_address_placeholder"),
                                    size="sm"
                                ),
                                dbc.Button(tr("add_button"), id="add-ip-button", color="primary", size="sm")  # ADD button
                            ]),
                        ], width=9),
                    ], className="mb-3"),
                    
                    # Added a list of currently saved IPs with delete buttons
                    html.Div([
                        html.P(tr("saved_machine_ips"), className="mt-3 mb-2"),
                        html.Div(id="delete-result", className="mb-2 text-success"),
                        html.Div(id="saved-ip-list", className="border p-2 mb-3", style={"minHeight": "100px"}),
                    ]),
                    
                    dbc.Button(
                        tr("save_system_settings"),
                        id="save-system-settings",
                        color="success",
                        className="mt-3 w-100"
                    ),
                    html.Div(id="system-settings-save-status", className="text-success mt-2"),
                ])
            ], label=tr("system_tab_label", _initial_lang), id="system-tab"),

            dbc.Tab([
                html.Div([
                    html.P(tr("smtp_email_configuration_title"), className="lead mt-2", id="smtp-email-configuration-title"),
                    html.Hr(),
                    dbc.Row([
                        dbc.Col(dbc.Label(tr("smtp_server_label"), id="smtp-server-label"), width=4),
                        dbc.Col(dbc.Input(id="smtp-server-input", type="text", value=email_settings.get("smtp_server", "")), width=8),
                    ], className="mb-3"),
                    dbc.Row([
                        dbc.Col(dbc.Label(tr("port_label"), id="smtp-port-label"), width=4),
                        dbc.Col(dbc.Input(id="smtp-port-input", type="number", value=email_settings.get("smtp_port", 587)), width=8),
                    ], className="mb-3"),
                    dbc.Row([
                        dbc.Col(dbc.Label(tr("username_label"), id="smtp-username-label"), width=4),
                        dbc.Col(dbc.Input(id="smtp-username-input", type="text", value=email_settings.get("smtp_username", "")), width=8),
                    ], className="mb-3"),
                    dbc.Row([
                        dbc.Col(dbc.Label(tr("password_label"), id="smtp-password-label"), width=4),
                        dbc.Col(dbc.Input(id="smtp-password-input", type="password", value=email_settings.get("smtp_password", "")), width=8),
                    ], className="mb-3"),
                    dbc.Row([
                        dbc.Col(dbc.Label(tr("from_address_label"), id="smtp-from-label"), width=4),
                        dbc.Col(dbc.Input(id="smtp-sender-input", type="email", value=email_settings.get("from_address", "")), width=8),
                    ], className="mb-3"),
                    dbc.Button(
                        tr("save_email_settings"),
                        id="save-email-settings",
                        color="success",
                        className="mt-3 w-100"
                    ),
                    html.Div(id="email-settings-save-status", className="text-success mt-2"),
                ])
            ], label=tr("email_tab_label", _initial_lang), id="email-tab"),
            
            # About tab remains the same
            dbc.Tab([
                html.Div([
                    html.P("About This Dashboard", className="lead mt-2"),
                    html.Hr(),
                    html.P([
                        "Satake Enpresor Monitor Dashboard ",
                        html.Span("v1.0.3", className="badge bg-secondary")
                    ]),
                    html.P([
                        "OPC UA Monitoring System for Satake Enpresor RGB Sorters",
                    ]),
                    html.P([
                        "© 2023 Satake USA, Inc. All rights reserved."
                    ], className="text-muted small"),
                    
                    html.Hr(),
                    html.P("Support Contact:", className="mb-1 fw-bold"),
                    html.P([
                        html.I(className="fas fa-envelope me-2"),
                        "techsupport@satake-usa.com"
                    ], className="mb-1"),
                    html.P([
                        html.I(className="fas fa-phone me-2"),
                        "(281) 276-3700"
                    ], className="mb-1"),
                ])
            ], label=tr("about_tab_label", _initial_lang), id="about-tab"),
        ]),
    ]),
    dbc.ModalFooter([
        dbc.Button(tr("close"), id="close-settings", color="secondary"),
    ])
], id="settings-modal", size="lg", is_open=False)

# Modal for updating counts
update_counts_modal = dbc.Modal([
    dbc.ModalHeader(html.Span(tr("update_counts_title"), id="update-counts-header")),
    dbc.ModalBody(html.Div(id="update-counts-modal-body")),
    dbc.ModalFooter([
        dbc.Button(tr("close"), id="close-update-counts", color="secondary")
    ])
], id="update-counts-modal", size="lg", is_open=False)
# Load saved IP addresses
initial_ip_addresses = load_ip_addresses()
logger.info(f"Initial IP addresses: {initial_ip_addresses}")

# File I/O functions for floor/machine data persistence
def save_floor_machine_data(floors_data, machines_data):
    """Save floor and machine data to JSON file"""
    try:
        data_to_save = {
            "floors": floors_data,
            "machines": machines_data,
            "saved_timestamp": datetime.now().isoformat()
        }
        
        # Create data directory if it doesn't exist. Saving floor/machine
        # layouts here allows them to be restored on the next startup.
        if not os.path.exists('data'):
            os.makedirs('data')
        
        with open('data/floor_machine_layout.json', 'w') as f:
            json.dump(data_to_save, f, indent=4)
        
        logger.info("Floor and machine layout saved successfully")
        return True
    except Exception as e:
        logger.error(f"Error saving floor/machine data: {e}")
        return False

def load_floor_machine_data():
    """Load floor and machine data from JSON file"""
    try:
        if os.path.exists('data/floor_machine_layout.json'):
            with open('data/floor_machine_layout.json', 'r') as f:
                data = json.load(f)
            
            # Extract floors and machines data
            floors_data = data.get("floors", {"floors": [{"id": 1, "name": "1st Floor"}], "selected_floor": "all"})
            machines_data = data.get("machines", {"machines": [], "next_machine_id": 1})
            
            logger.info(f"Loaded floor and machine layout from file (saved: {data.get('saved_timestamp', 'unknown')})")
            return floors_data, machines_data
        else:
            logger.info("No saved floor/machine layout found, using defaults")
            return None, None
    except Exception as e:
        logger.error(f"Error loading floor/machine data: {e}")
        return None, None

# Function to get current machine data for display
def get_machine_current_data(machine_id):
    """Get current data for a specific machine with enhanced real-time updates"""
    if machine_id not in machine_connections or not machine_connections[machine_id]['connected']:
        return {
            "serial": "Unknown",
            "status": "Offline",
            "model": "Unknown",
            "last_update": "Never"
        }
    
    connection_info = machine_connections[machine_id]
    tags = connection_info['tags']
    
    # Read current values from the continuously updated tags with fresh timestamp
    serial_number = "Unknown"
    if "Status.Info.Serial" in tags:
        serial_value = tags["Status.Info.Serial"]["data"].latest_value
        if serial_value:
            serial_number = str(serial_value)
    
    model_type = "Unknown"
    if "Status.Info.Type" in tags:
        type_value = tags["Status.Info.Type"]["data"].latest_value
        if type_value:
            model_type = str(type_value)
    
    # Determine status from fault/warning tags
    status_text = "GOOD"
    has_fault = False
    has_warning = False
    
    if "Status.Faults.GlobalFault" in tags:
        fault_value = tags["Status.Faults.GlobalFault"]["data"].latest_value
        has_fault = bool(fault_value) if fault_value is not None else False
    
    if "Status.Faults.GlobalWarning" in tags:
        warning_value = tags["Status.Faults.GlobalWarning"]["data"].latest_value
        has_warning = bool(warning_value) if warning_value is not None else False
    
    if has_fault:
        status_text = "FAULT"
    elif has_warning:
        status_text = "WARNING"
    else:
        status_text = "GOOD"
    
    # Use current time for last_update to show real-time updates
    last_update = datetime.now().strftime("%H:%M:%S")
    
    return {
        "serial": serial_number,
        "status": status_text,
        "model": model_type,
        "last_update": last_update
    }


def _render_new_dashboard():
    """Render the new dashboard with floor/machine management"""
    return html.Div([
        # REMOVED: dcc.Interval(id="status-update-interval"...) - now at top level

        # Main content area
        html.Div(id="floor-machine-container", className="px-4 pt-2 pb-4"),

        # Placeholder container for dynamic sections (IDs defined in main dashboard)
        html.Div([], id="new-dashboard-placeholders", style={"display": "none"})
    ])







def render_new_dashboard(lang=_initial_lang):
    # Currently the new dashboard does not use language-specific text directly,
    # but accept the parameter for future-proofing and consistency.
    return _render_new_dashboard()


def render_main_dashboard(lang=_initial_lang):
    return html.Div([
        # Main grid layout - modified to align sections and reduced spacing
        html.Div([
            # Row 1: Top row with 3 panels - REDUCED SPACING
            dbc.Row([
                # First column - Two sections stacked
                dbc.Col([
                    # Top box - Section 1-1
                    dbc.Card(
                        dbc.CardBody(id="section-1-1", className="p-2"),
                        className="mb-2",
                        style={"height": SECTION_HEIGHT}
                    ),
                    
                    # Bottom box - Section 1-2 (unchanged)
                    dbc.Card(
                        dbc.CardBody(id="section-1-2", className="p-2"),
                        className="mb-0",
                        style={"height": SECTION_HEIGHT}
                    ),
                ], width=5),
                
                
                # Middle column - Single large section (MACHINE STATUS)
                dbc.Col([
                    dbc.Card(
                        dbc.CardBody(id="section-2", className="p-2"),
                        style={"height": "449px"}
                    )
                ], width=3),
                
                # Right column - Single large section (MACHINE INFO)
                dbc.Col([
                    dbc.Card(
                        dbc.CardBody(id="section-3-1", className="p-2"),
                        className="mb-2",
                        style={"height": SECTION_HEIGHT}
                    ),
                    dbc.Card(
                        dbc.CardBody(id="section-3-2", className="p-2"),
                        className="mb-0",
                        style={"height": SECTION_HEIGHT}
                    ),
                ], width=4),
            ], className="mb-0 g-0"),  # Reduced mb-3 to mb-2 and added g-2 for smaller gutters
            
            # Row 2: Bottom row (reduced spacing)
            dbc.Row([
                # First column - Single tall section

                dbc.Col([
                    dbc.Card(
                        dbc.CardBody(id="section-4", className="p-2"),
                        className="mb-2",
                        style={"height": "508px"}
                    ),
                ], width=2, className="pe-2"),

                
                # Middle column - Two sections stacked
                dbc.Col([
                    # Top box
                    dbc.Card(
                        dbc.CardBody(id="section-5-1", className="p-2"),
                        className="mb-2",
                        style={"height": SECTION_HEIGHT2}
                    ),

                    # Bottom box
                    dbc.Card(
                        dbc.CardBody(id="section-5-2", className="p-2"),
                        className="mb-2",
                        style={"height": SECTION_HEIGHT2}
                    ),
                ], width=4, className='pe-2'),
                
                # Right column - Two sections stacked
                dbc.Col([
                    # Top box
                    dbc.Card(
                        dbc.CardBody(id="section-6-1", className="p-2"),
                        className="mb-2",
                        style={"height": SECTION_HEIGHT2}
                    ),

                    # Bottom box
                    dbc.Card(
                        dbc.CardBody(id="section-6-2", className="p-2"),
                        className="mb-2",
                        style={"height": SECTION_HEIGHT2}
                    ),
                ], width=4, className='pe-2'),

                dbc.Col([
                    # Top box
                    dbc.Card(
                        dbc.CardBody(id="section-7-1", className="p-2"),
                        className="mb-2",
                        style={"height": SECTION_HEIGHT2}
                    ),

                    # Bottom box
                    dbc.Card(
                        dbc.CardBody(
                            id="section-7-2",
                            className="p-2 overflow-auto h-100"
                        ),
                        className="mb-2",
                        style={"height": SECTION_HEIGHT2}
                    ),
                ], width=2),

            ], className="g-2"),  # Added g-2 for smaller gutters
        ], className="container-fluid px-2"),  # Added px-2 to reduce container padding
    ],
    style={
        'backgroundColor': '#f0f0f0',
        'minHeight': '100vh',
        'display': 'flex',
        'flexDirection': 'column'
    })

# Auto-load saved data on startup
def initialize_floor_machine_data():
    """Initialize floor and machine data from saved file or defaults"""
    floors_data, machines_data = load_floor_machine_data()

    if floors_data is None:
        floors_data = {
            "floors": [{"id": 1, "name": "1st Floor", "editing": False}],
            "selected_floor": "all",
        }
    else:
        # Always start with the "All Machines" view selected rather than
        # whichever floor may have been active when data was saved.
        floors_data["selected_floor"] = "all"

    if machines_data is None:
        machines_data = {"machines": [], "next_machine_id": 1}

    # Ensure all floors have the editing flag
    for floor in floors_data.get("floors", []):
        if "editing" not in floor:
            floor["editing"] = False

    return floors_data, machines_data


# Then in your app.layout definition, use the loaded addresses:
initial_floors_data, initial_machines_data = initialize_floor_machine_data()

# Create the main layout matching the grid image
app.layout = html.Div([
    # ─── CRITICAL: Add status-update-interval at the TOP LEVEL so it's ALWAYS available ───
    dcc.Interval(id="status-update-interval", interval=1000, n_intervals=0),
    dcc.Interval(id="metric-logging-interval", interval=60*1000, n_intervals=0),

    # ─── Hidden state stores ───────────────────────────────────────────────
    dcc.Store(id="current-dashboard",       data="new"),
    dcc.Store(id="production-data-store",   data={"capacity": 50000, "accepts": 47500, "rejects": 2500}),
    dcc.Store(id="alarm-data",              data={"alarms": []}),
    dcc.Store(id="metric-logging-store"),
    dcc.Store(id="historical-time-index",   data={"hours": 24}),
    dcc.Store(id="historical-data-cache",   data={}),
    dcc.Store(id="fullscreen-tracker",      data={"triggered": False}),
    dcc.Store(id="app-state",               data={"connected": False, "auto_connect": True}),
    dcc.Store(id="input-values",            data={"count": 1000, "weight": 500.0, "units": "lb"}),
    dcc.Store(id="user-inputs",             data={"units": "lb", "weight": 500.0, "count": 1000}),
    dcc.Store(id="opc-pause-state",         data={"paused": False}),
    dcc.Store(id="lab-test-running",      data=False),
    dcc.Store(id="lab-test-info",         data={}),
    dcc.Store(id="app-mode",                data={"mode": "live"}),
    # Store used only to trigger the callback that updates the global
    # ``current_app_mode`` variable.
    dcc.Store(id="app-mode-tracker"),
    dcc.Store(id="ip-addresses-store",      data=load_ip_addresses()),
    dcc.Store(id="additional-image-store",  data=load_saved_image()),
    dcc.Store(id="image-error-store"),
    dcc.Store(id="weight-preference-store", data=load_weight_preference()),
    dcc.Store(id="language-preference-store", data=load_language_preference()),
    dcc.Store(id="email-settings-store",   data=load_email_settings()),
    # Store selection for production rate units (objects or capacity)
    dcc.Store(id="production-rate-unit",    data="objects"),
    dcc.Store(id="floors-data", data=initial_floors_data),
    dcc.Store(id="machines-data", data=initial_machines_data),
    dcc.Store(id="machine-data-store", data={}),
    dcc.Store(id="active-machine-store", data={"machine_id": None}),
    dcc.Store(id="delete-pending-store", data={"type": None, "id": None, "name": None}),
    dcc.Store(id="hidden-machines-cache"),
    dcc.Store(id="delete-ip-trigger", data={}),
    dcc.Store(id="auto-connect-trigger", data="init"),
    dcc.Store(id="dashboard-nav-safety", data={}),

    # ─── Title bar + Dashboard-toggle button ───────────────────────────────
    html.Div([
        html.H3(
            id="dashboard-title",
            children=(
                [
                    title_parts[0],
                    html.Span("Enpresor", className="enpresor-font", style={"color": "red"}),
                    title_parts[1],
                ]
                if len((title_parts := tr("dashboard_title").split("Enpresor"))) == 2
                else tr("dashboard_title")
            ),
            className="m-0",
        ),
        dbc.Button(tr("switch_dashboards", _initial_lang),
                   id="new-dashboard-btn",
                   color="light", size="sm", className="ms-2"),
        dbc.Button(tr("generate_report", _initial_lang),
                   id="generate-report-btn",
                   color="light", size="sm", className="ms-2"),
        dcc.Download(id="report-download"),
    ], className="d-flex justify-content-between align-items-center bg-primary text-white p-2 mb-2"),

    # ─── Connection controls (always visible) ──────────────────────────────
    connection_controls(_initial_lang),

    dbc.Modal([
        dbc.ModalHeader(html.Span(tr("upload_image_title"), id="upload-modal-header")),
        dbc.ModalBody([
            dcc.Upload(
                id="upload-image",
                children=html.Div([
                    tr('drag_and_drop'),
                    html.A(tr('select_image'))
                ]),
                style={
                    'width': '100%',
                    'height': '60px',
                    'lineHeight': '60px',
                    'borderWidth': '1px',
                    'borderStyle': 'dashed',
                    'borderRadius': '5px',
                    'textAlign': 'center',
                    'margin': '10px'
                },
                multiple=False
            ),
            html.Div(id="upload-status"),
            dbc.Alert(id="image-error-alert", color="danger", is_open=False, className="mt-2")
        ]),
        dbc.ModalFooter([
            dbc.Button(tr("close"), id="close-upload-modal", color="secondary")
        ])
    ], id="upload-modal", is_open=False),

    # ─── All Modals ────────────────────────────────────────────────────────
    display_modal,      # id="display-modal"
    threshold_modal,    # id="threshold-modal"
    units_modal,        # id="production-rate-units-modal"
    settings_modal,     # id="settings-modal"
    update_counts_modal, # id="update-counts-modal"
    
    # ─── NEW: Delete Confirmation Modal ────────────────────────────────────
    dbc.Modal([
        dbc.ModalHeader([
            dbc.ModalTitle(tr("confirm_deletion_title"), id="delete-confirmation-header"),
            dbc.Button("×", id="close-delete-modal", className="btn-close", style={"background": "none", "border": "none"})
        ]),
        dbc.ModalBody([
            html.Div(id="delete-confirmation-message", children=[
                html.I(className="fas fa-exclamation-triangle text-warning me-2", style={"fontSize": "1.5rem"}),
                html.Span(tr("delete_warning"), id="delete-warning", className="fw-bold")
            ], className="text-center mb-3"),
            html.Div(id="delete-item-details", className="text-center text-muted")
        ]),
        dbc.ModalFooter([
            dbc.Button(tr("cancel"), id="cancel-delete-btn", color="secondary", className="me-2"),
            dbc.Button(tr("yes_delete"), id="confirm-delete-btn", color="danger")
        ])
    ], id="delete-confirmation-modal", is_open=False, centered=True),

    # ─── CONTENT PLACEHOLDER ────────────────────────────────────────────────
    html.Div(
        id="dashboard-content",
        children=render_new_dashboard()
    ),

], className="main-app-container")
from callbacks import register_callbacks

def load_historical_data(timeframe="24h", machine_id=None):
    """Load historical counter data for the requested timeframe and machine.

    Parameters
    ----------
    timeframe : str, optional
        Range of history to retrieve, such as ``"24h"`` for 24 hours.
    machine_id : str, optional
        Identifier for the machine whose metrics should be returned.
    """
    try:
        return get_historical_data(timeframe, machine_id=machine_id)
    except Exception as e:
        print(f"Error loading historical data: {str(e)}")
        return {i: {'times': [], 'values': []} for i in range(1, 13)}







# Callback to open/close the settings modal

def auto_reconnection_thread():
    """Background thread for automatic reconnection attempts"""
    logger.info("Auto-reconnection thread STARTED and running")
    
    while not app_state.thread_stop_flag:
        try:
            logger.info("Auto-reconnection thread cycle beginning...")
            current_time = datetime.now()
            machines_to_reconnect = []
            
            # Get machines data from the cached version
            if hasattr(app_state, 'machines_data_cache') and app_state.machines_data_cache:
                machines = app_state.machines_data_cache.get("machines", [])
                logger.info(f"Auto-reconnection found {len(machines)} machines in cache")
                
                for machine in machines:
                    machine_id = machine.get("id")
                    machine_ip = machine.get("selected_ip") or machine.get("ip")
                    machine_status = machine.get("status", "Unknown")
                    
                    logger.info(f"Checking machine {machine_id}: IP={machine_ip}, Status={machine_status}, Connected={machine_id in machine_connections}")
                    
                    # Skip if machine doesn't have an IP
                    if not machine_ip:
                        logger.info(f"Skipping machine {machine_id} - no IP address")
                        continue
                    
                    # Skip if machine is already connected
                    if machine_id in machine_connections and machine_connections[machine_id].get('connected', False):
                        logger.info(f"Skipping machine {machine_id} - already connected")
                        # Reset reconnection state for connected machines
                        if machine_id in reconnection_state:
                            del reconnection_state[machine_id]
                        continue
                    
                    # Only reconnect to machines that should be connected but aren't
                    if machine_status in ["Connection Lost", "Connection Error", "Offline", "Disconnected", "UNKNOWN", "WARNING"]:
                        machines_to_reconnect.append((machine_id, machine_ip))
                        logger.info(f"Added machine {machine_id} to reconnection queue")
                    else:
                        logger.info(f"Skipping machine {machine_id} - status '{machine_status}' not in reconnection list")
                
                logger.info(f"Total machines queued for reconnection: {len(machines_to_reconnect)}")
            else:
                logger.warning("No machines cache available for auto-reconnection")
            
            # Process reconnection attempts
            for machine_id, machine_ip in machines_to_reconnect:
                # Initialize reconnection state if needed
                if machine_id not in reconnection_state:
                    reconnection_state[machine_id] = {
                        'last_attempt': None,
                        'attempt_count': 0,
                        'next_attempt_delay': 10  # Start with 10 second delay
                    }
                
                state = reconnection_state[machine_id]
                
                # Check if it's time to attempt reconnection
                should_attempt = False
                if state['last_attempt'] is None:
                    should_attempt = True
                elif (current_time - state['last_attempt']).total_seconds() >= state['next_attempt_delay']:
                    should_attempt = True
                
                if should_attempt:
                    # Attempt reconnection
                    try:
                        logger.info(f"Auto-reconnection attempt #{state['attempt_count'] + 1} for machine {machine_id} at {machine_ip}")
                        
                        # Create a new event loop for this connection attempt
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        
                        try:
                            # Use the existing connect function with timeout
                            connection_success = loop.run_until_complete(
                                connect_and_monitor_machine(machine_ip, machine_id, "Satake.EvoRGB.1")
                            )
                            
                            if connection_success:
                                logger.info(f"✓ Auto-reconnection successful for machine {machine_id}")
                                # Reset reconnection state on success
                                if machine_id in reconnection_state:
                                    del reconnection_state[machine_id]
                                
                                # Start update thread if not running
                                if app_state.update_thread is None or not app_state.update_thread.is_alive():
                                    app_state.thread_stop_flag = False
                                    app_state.update_thread = Thread(target=opc_update_thread)
                                    app_state.update_thread.daemon = True
                                    app_state.update_thread.start()
                                    logger.info("Restarted OPC update thread after auto-reconnection")
                            else:
                                # Update reconnection state for next attempt
                                state['last_attempt'] = current_time
                                state['attempt_count'] += 1
                                
                                # Exponential backoff with max delay of 60 seconds
                                state['next_attempt_delay'] = min(60, 10 * (2 ** min(state['attempt_count'] - 1, 3)))
                                
                                logger.debug(f"✗ Auto-reconnection failed for machine {machine_id}, next attempt in {state['next_attempt_delay']} seconds")
                        
                        finally:
                            loop.close()
                    
                    except Exception as e:
                        logger.debug(f"Auto-reconnection error for machine {machine_id}: {e}")
                        state['last_attempt'] = current_time
                        state['attempt_count'] += 1
                        state['next_attempt_delay'] = min(60, 10 * (2 ** min(state['attempt_count'] - 1, 3)))
        
        except Exception as e:
            logger.error(f"Error in auto-reconnection thread: {e}")
        
        # Sleep for 10 seconds between reconnection cycles
        time.sleep(10)
    
    logger.info("Auto-reconnection thread stopped")

async def connect_and_monitor_machine_with_timeout(ip_address, machine_id, server_name=None, timeout=10):
    """Connect to a specific machine with timeout for auto-reconnection"""
    try:
        server_url = f"opc.tcp://{ip_address}:4840"
        
        # Create client with shorter timeout for auto-reconnection
        client = Client(server_url)
        client.set_session_timeout(timeout * 1000)  # Set timeout in milliseconds
        
        if server_name:
            client.application_uri = f"urn:{server_name}"
        
        # Connect with timeout
        client.connect()
        
        # Quick tag discovery (fewer tags for faster reconnection)
        machine_tags = {}
        
        # Only connect to essential tags for auto-reconnection (faster)
        essential_tags = [t for t in FAST_UPDATE_TAGS if t in KNOWN_TAGS]
        
        for tag_name in essential_tags:
            if tag_name in KNOWN_TAGS:
                node_id = KNOWN_TAGS[tag_name]
                try:
                    node = client.get_node(node_id)
                    value = node.get_value()
                    
                    tag_data = TagData(tag_name)
                    tag_data.add_value(value)
                    machine_tags[tag_name] = {
                        'node': node,
                        'data': tag_data
                    }
                except Exception:
                    continue  # Skip failed tags during auto-reconnection
        
        # If we got at least some tags, consider it a successful connection
        if machine_tags:
            # Do full tag discovery in background after successful connection
            asyncio.create_task(complete_tag_discovery(client, machine_id, machine_tags))
            
            # Store the connection
            machine_connections[machine_id] = {
                'client': client,
                'tags': machine_tags,
                'ip': ip_address,
                'connected': True,
                'last_update': datetime.now(),
                'failure_count': 0
            }
            
            return True
        else:
            client.disconnect()
            return False
            
    except Exception as e:
        logger.debug(f"Auto-reconnection failed for machine {machine_id} at {ip_address}: {e}")
        return False

async def complete_tag_discovery(client, machine_id, existing_tags):
    """Complete tag discovery in background after successful auto-reconnection"""
    try:
        # Discover remaining tags
        for tag_name, node_id in KNOWN_TAGS.items():
            if tag_name not in existing_tags and tag_name in FAST_UPDATE_TAGS:
                try:
                    node = client.get_node(node_id)
                    value = node.get_value()
                    
                    tag_data = TagData(tag_name)
                    tag_data.add_value(value)
                    existing_tags[tag_name] = {
                        'node': node,
                        'data': tag_data
                    }
                except Exception:
                    continue
        
        logger.info(f"Completed tag discovery for auto-reconnected machine {machine_id}: {len(existing_tags)} tags")
        
    except Exception as e:
        logger.debug(f"Error in background tag discovery for machine {machine_id}: {e}")




# FIND this callback (the machine dashboard update):



# Callback to update the saved IP list display








# Callback 2: Save theme when user changes it

















# Global dictionary to store connections to all added machines
machine_connections = {}

async def connect_and_discover_machine_tags(ip_address, machine_id, server_name=None):
    """Connect to a specific machine and discover its tags (one-time setup)"""
    try:
        server_url = f"opc.tcp://{ip_address}:4840"
        logger.info(f"Connecting to machine {machine_id} at {ip_address} for tag discovery...")
        
        # Create client for this machine
        client = Client(server_url)
        
        # Set application name - same as main connection
        if server_name:
            client.application_uri = f"urn:{server_name}"
        
        # Connect to server
        client.connect()
        logger.info(f"Connected successfully to machine {machine_id} at {ip_address}")
        
        # Discover tags using the exact same logic as main connection
        machine_tags = {}
        
        # First, try to connect to all known tags explicitly
        for tag_name, node_id in KNOWN_TAGS.items():
            if tag_name not in FAST_UPDATE_TAGS:
                continue
            try:
                node = client.get_node(node_id)
                value = node.get_value()
                
                # Create TagData object for this tag (same as main connection)
                tag_data = TagData(tag_name)
                tag_data.add_value(value)
                machine_tags[tag_name] = {
                    'node': node,
                    'data': tag_data
                }
                logger.info(f"Successfully connected to known tag: {tag_name} = {value}")
            except Exception as e:
                logger.debug(f"Could not connect to known tag {tag_name} on machine {machine_id}: {e}")
        
        # Then do recursive browsing for additional tags (same as main connection)
        root = client.get_root_node()
        objects = client.get_objects_node()
        
        # Function to recursively browse nodes
        async def browse_nodes(node, level=0, max_level=3):
            if level > max_level:
                return
                
            try:
                children = node.get_children()
                for child in children:
                    try:
                        name = child.get_browse_name().Name
                        node_class = child.get_node_class()
                        
                        if node_class == ua.NodeClass.Variable:
                            try:
                                if name in machine_tags or name not in FAST_UPDATE_TAGS:
                                    continue
                                    
                                value = child.get_value()
                                tag_data = TagData(name)
                                tag_data.add_value(value)
                                machine_tags[name] = {
                                    'node': child,
                                    'data': tag_data
                                }
                            except Exception:
                                pass
                        
                        await browse_nodes(child, level + 1, max_level)
                    except Exception:
                        pass
            except Exception:
                pass
        
        await browse_nodes(objects, 0, 2)
        
        logger.info(f"Total tags discovered on machine {machine_id}: {len(machine_tags)}")
        
        # Store the connection info for continuous updates
        machine_connections[machine_id] = {
            'client': client,
            'tags': machine_tags,
            'ip': ip_address,
            'connected': True,
            'last_update': datetime.now()
        }
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to connect to machine {machine_id} at {ip_address}: {e}")
        return False

# Modified helper function to maintain persistent connections for continuous updates
async def connect_and_monitor_machine(ip_address, machine_id, server_name=None):
    """Connect to a specific machine and maintain connection for continuous monitoring"""
    try:
        server_url = f"opc.tcp://{ip_address}:4840"
        logger.info(f"Establishing persistent connection to machine {machine_id} at {ip_address}...")
        
        # Create persistent client for this machine
        client = Client(server_url)
        
        # Set application name - same as main connection
        if server_name:
            client.application_uri = f"urn:{server_name}"
            logger.info(f"Setting application URI to: {client.application_uri}")
        
        # Connect to server
        client.connect()
        logger.info(f"Connected successfully to machine {machine_id} at {ip_address}")
        
        # Discover tags using the exact same logic as main connection
        machine_tags = {}
        
        # First, try to connect to all known tags explicitly
        logger.info(f"Discovering tags on machine {machine_id}...")
        for tag_name, node_id in KNOWN_TAGS.items():
            if tag_name not in FAST_UPDATE_TAGS:
                continue
            try:
                node = client.get_node(node_id)
                value = node.get_value()
                
                # Create TagData object for this tag (same as main connection)
                tag_data = TagData(tag_name)
                tag_data.add_value(value)
                machine_tags[tag_name] = {
                    'node': node,
                    'data': tag_data
                }
                logger.info(f"Successfully connected to known tag: {tag_name} = {value}")
            except Exception as e:
                logger.warning(f"Could not connect to known tag {tag_name} on machine {machine_id}: {e}")
        
        # Then do recursive browsing for additional tags
        root = client.get_root_node()
        objects = client.get_objects_node()
        
        # Function to recursively browse nodes - same as main discover_tags()
        async def browse_nodes(node, level=0, max_level=3):
            if level > max_level:
                return
                
            try:
                children = node.get_children()
                for child in children:
                    try:
                        name = child.get_browse_name().Name
                        node_class = child.get_node_class()
                        
                        # If it's a variable, add it to our tags (if not already added)
                        if node_class == ua.NodeClass.Variable:
                            try:
                                # Skip if name already exists from known tags
                                if name in machine_tags or name not in FAST_UPDATE_TAGS:
                                    continue
                                    
                                value = child.get_value()
                                logger.debug(f"Found additional tag: {name} = {value}")
                                
                                tag_data = TagData(name)
                                tag_data.add_value(value)
                                machine_tags[name] = {
                                    'node': child,
                                    'data': tag_data
                                }
                            except Exception:
                                pass
                        
                        # Continue browsing deeper
                        await browse_nodes(child, level + 1, max_level)
                    except Exception:
                        pass
            except Exception:
                pass
        
        # Start browsing from objects node
        await browse_nodes(objects, 0, 2)
        
        logger.info(f"Total tags discovered on machine {machine_id}: {len(machine_tags)}")
        
        # Store the connection and tags for continuous monitoring
        machine_connections[machine_id] = {
            'client': client,
            'tags': machine_tags,
            'ip': ip_address,
            'connected': True,
            'last_update': datetime.now()
        }
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to connect to machine {machine_id} at {ip_address}: {e}")
        return False

# Helper function to find the lowest available machine ID
def get_next_available_machine_id(machines_data):
    """Find the lowest available machine ID"""
    machines = machines_data.get("machines", [])
    existing_ids = {machine["id"] for machine in machines}
    
    # Find the lowest available ID starting from 1
    next_id = 1
    while next_id in existing_ids:
        next_id += 1
    
    return next_id

# Enhanced render function with customizable floor names
def render_floor_machine_layout_with_customizable_names(machines_data, floors_data, ip_addresses_data, additional_image_data, current_dashboard, active_machine_id=None, app_mode_data=None, lang=DEFAULT_LANGUAGE):
    """Render layout with customizable floor names and save functionality"""
    
    # CRITICAL: Only render on machine dashboard. When the new dashboard is not
    # active the container does not exist, so prevent the update entirely to
    # avoid ReferenceError in Dash.
    if current_dashboard != "new":
        raise PreventUpdate
    
    if not floors_data or not machines_data:
        return html.Div("Loading...")
    
    floors = floors_data.get("floors", [])
    selected_floor_id = floors_data.get("selected_floor", "all")
    machines = machines_data.get("machines", [])
    
    # Create IP options
    ip_options = []
    if ip_addresses_data and "addresses" in ip_addresses_data:
        for item in ip_addresses_data["addresses"]:
            if isinstance(item, dict) and "ip" in item and "label" in item:
                ip_options.append({"label": item["label"], "value": item["ip"]})
    
    if not ip_options:
        ip_options = [{"label": "Default (192.168.0.125)", "value": "192.168.0.125"}]
    
    # Filter machines for selected floor
    if selected_floor_id == "all":
        selected_floor_machines = machines
    else:
        selected_floor_machines = [m for m in machines if m["floor_id"] == selected_floor_id]

    # ------------------------------------------------------------------
    # Calculate aggregated production totals for machines in view
    # ------------------------------------------------------------------
    def _to_float(val):
        try:
            return float(str(val).replace(",", ""))
        except Exception:
            return 0.0

    total_capacity = 0.0
    total_accepts = 0.0
    total_rejects = 0.0
    capacity_values = []
    for _m in selected_floor_machines:
        prod = (_m.get("operational_data") or {}).get("production", {})
        if not isinstance(prod, dict):
            capacity = 0.0
            accepts = 0.0
            rejects = 0.0
        else:
            # Use the formatted values that are displayed on the machine cards
            # but fall back to the raw values when the formatted ones are not
            # present (e.g. in unit tests).
            capacity = _to_float(prod.get("capacity_formatted", prod.get("capacity")))
            accepts = _to_float(prod.get("accepts_formatted", prod.get("accepts")))
            rejects = _to_float(prod.get("rejects_formatted", prod.get("rejects")))
        capacity_values.append(capacity)
        total_capacity += capacity
        total_accepts += accepts
        total_rejects += rejects

    mode = "demo"
    if isinstance(app_mode_data, dict) and "mode" in app_mode_data:
        mode = app_mode_data.get("mode", "demo")

    machine_count = len(selected_floor_machines)

    if mode == "historical" and machine_count > 0:
        # When showing historical data, display the average across the
        # machines currently in view rather than the sum.
        total_capacity /= machine_count
        total_accepts /= machine_count
        total_rejects /= machine_count

    weight_pref = load_weight_preference()
    total_capacity_fmt = f"{total_capacity:,.0f}"
    total_accepts_fmt = f"{total_accepts:,.0f}"
    total_rejects_fmt = f"{total_rejects:,.0f}"

    
    # LEFT SIDEBAR BUTTONS (FIXED) - same as before
    is_all_selected = selected_floor_id == "all"
    
    # Style for "Show All Machines" button
    all_button_style = {
        "backgroundColor": "#007bff" if is_all_selected else "#696969",
        "color": "white" if is_all_selected else "black",
        "border": "2px solid #28a745" if is_all_selected else "1px solid #dee2e6",
        "cursor": "pointer",
        "borderRadius": "0.375rem"
    }
    
    # Create left sidebar buttons in the specified order
    left_sidebar_buttons = []
    
    # 1. CORPORATE LOGO (at the top)
    has_additional_image = additional_image_data and 'image' in additional_image_data
    
    if has_additional_image:
        logo_section = html.Div([
            html.Img(
                src=additional_image_data['image'],
                style={
                    'maxWidth': '100%',
                    'maxHeight': '120px',
                    'objectFit': 'contain',
                    'margin': '0 auto',
                    'display': 'block'
                }
            )
        ], className="text-center mb-3", style={'minHeight': '120px', 'height': 'auto', 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center'})
    else:
        logo_section = html.Div([
            html.Div(
                "No corporate logo loaded",
                className="text-center text-muted small",
                style={'minHeight': '120px', 'height': 'auto', 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center'}
            )
        ], className="mb-3")
    
    left_sidebar_buttons.append(logo_section)
    
    # 2. Show All Machines button
    left_sidebar_buttons.append(
        dbc.Button(tr("show_all_machines", lang),
                  id={"type": "floor-tile", "index": "all"},
                  n_clicks=0,
                  style=all_button_style,
                  className="mb-3 w-100 floor-tile-btn",
                  size="lg")
    )
    
    # 3. Add individual floor buttons (with delete and edit buttons)
    for floor in floors:
        floor_id = floor["id"]
        floor_name = floor["name"]
        is_editing = floor.get("editing", False)
        is_selected = floor_id == selected_floor_id and selected_floor_id != "all"
        
        floor_style = {
            "backgroundColor": "#007bff" if is_selected else "#696969",
            "color": "white" if is_selected else "black",
            "border": "2px solid #007bff" if is_selected else "1px solid #dee2e6",
            "cursor": "pointer",
            "borderRadius": "0.375rem"
        }
        
        # Create floor button content with edit functionality
        if is_editing:
            floor_button_content = dbc.InputGroup([
                # Delete button (always visible even when editing)
                dbc.Button(
                    "×",
                    id={"type": "delete-floor-btn", "index": floor_id},
                    color="danger",
                    size="sm",
                    className="delete-floor-btn delete-floor-btn-inline",
                    style={
                        "fontSize": "0.8rem"
                    },
                    title=f"Delete {floor_name}"
                ),
                dbc.Input(
                    id={"type": "floor-name-input", "index": floor_id},
                    value=floor_name,
                    size="sm",
                    style={"fontSize": "0.9rem"}
                ),
                dbc.Button("✓", id={"type": "save-floor-name-btn", "index": floor_id}, 
                          color="success", size="sm", style={"padding": "0.25rem 0.5rem"}),
                dbc.Button("✗", id={"type": "cancel-floor-name-btn", "index": floor_id}, 
                          color="secondary", size="sm", style={"padding": "0.25rem 0.5rem"})
            ])
        else:
            floor_button_content = dbc.Row([
                # Delete button column
                dbc.Col([
                    dbc.Button(
                        "×",
                        id={"type": "delete-floor-btn", "index": floor_id},
                        color="danger",
                        size="md",
                        className="delete-floor-btn",
                        style={
                            "fontSize": "1rem"
                        },
                        title=f"Delete {floor_name}"
                    )
                ], width=1, className="pe-1"),
                
                # Floor button column
                dbc.Col([
                    dbc.Button(floor_name, id={"type": "floor-tile", "index": floor_id}, n_clicks=0,
                              style=floor_style, className="w-100 floor-tile-btn", size="lg")
                ], width=9, className="px-1"),
                
                # Edit button column
                dbc.Col([
                    dbc.Button("✏️", id={"type": "edit-floor-name-btn", "index": floor_id},
                              color="light", size="lg", className="w-100 edit-floor-name-btn")
                ], width=2, className="ps-1")
            ], className="g-0 align-items-center")
        
        left_sidebar_buttons.append(
            html.Div(floor_button_content, className="mb-2")
        )
    
    # 4. Add Floor button
    left_sidebar_buttons.append(
        dbc.Button(tr("add_floor", lang),
                  id="add-floor-btn",
                  color="secondary",
                  className="mb-2 w-100",
                  size="lg")
    )
    
    # 5. Total Machines Online Card
    connected_count = sum(1 for m in machines if m["id"] in machine_connections and machine_connections[m["id"]].get('connected', False))
    total_count = len(machines)

    left_sidebar_buttons.append(
        dbc.Card([
            dbc.CardBody([
                html.Div(tr("total_machines_online", lang),
                        style={"fontSize": "1.2rem", "textAlign": "center"},
                        className="text-muted mb-1"),
                html.Div(f"{connected_count} / {total_count}", 
                        style={
                            "fontSize": "4.8rem", 
                            "fontWeight": "bold", 
                            "lineHeight": "1.2", 
                            "textAlign": "center",
                            "fontFamily": NUMERIC_FONT
                        })

            ], className="p-2")
        ], className="mb-2 machine-card-disconnected")
    )

    # 6. Machine Image
    left_sidebar_buttons.append(
        html.Div([
            html.Img(
                src=app.get_asset_url("EnpresorMachine.png"),
                style={
                    'width': '100%',
                    'maxWidth': '100%',
                    'maxHeight': '700px',
                    'objectFit': 'contain',
                    'margin': '0 auto',
                    'display': 'block'
                }
            )
        ], className="text-center mb-2")
    )

    
    
    
    # Add save status to sidebar
    left_sidebar_buttons.append(
        html.Div(id="save-status", className="text-success small text-center mt-3")
    )
    
    # RIGHT SIDE CONTENT (DYNAMIC) - UPDATED to use new card function
    right_content = []
    
    # Add header card showing current selection
    if selected_floor_id == "all":
        header_text = tr("all_machines_label", lang)
    else:
        # Find the floor name
        floor_name = f"Floor {selected_floor_id}"  # Default
        if floors_data and floors_data.get("floors"):
            for floor in floors_data["floors"]:
                if floor["id"] == selected_floor_id:
                    floor_name = floor["name"]
                    break
        header_text = floor_name

    # Add the header card
    right_content.append(
        dbc.Card(
            dbc.CardBody(
                html.Div(
                    header_text,
                    className="text-center mb-0 floor-header-text",
                ),
                className="p-2 d-flex align-items-center justify-content-center",
                style={"height": HEADER_CARD_HEIGHT},
            ),
            className="mb-1 machine-card-disconnected",
        )
    )
    
    # Show machines based on selection
    if selected_floor_id == "all":
        # Show all machines
        if machines:
            right_content.append(
                dbc.Row([
                    dbc.Col([
                        create_enhanced_machine_card_with_selection(
                            machine, ip_options, floors_data,
                            is_all_view=True,
                            is_active=(machine['id'] == active_machine_id),
                            lang=lang
                        )

                    ], xs=6, md=4)
                    for machine in selected_floor_machines
                ])

            )
            right_content.append(
                dbc.Card(
                    dbc.CardBody(
                        html.Div(
                            [

                            html.Span(
                                tr("total_production_label", lang),
                                className="fw-bold",
                                style={"fontSize": "1.2rem"},
                            ),
                            html.Span(
                                f"{total_capacity_fmt} {capacity_unit_label(weight_pref)}",
                                style={"fontFamily": NUMERIC_FONT, "fontSize": "2.5rem"},
                            ),
                            html.Span(
                                tr("accepts_label", lang),
                                className="fw-bold ms-3",
                                style={"fontSize": "1.2rem"},
                            ),
                            html.Span(
                                f"{total_accepts_fmt} {capacity_unit_label(weight_pref, False)}",
                                style={"fontFamily": NUMERIC_FONT, "fontSize": "2.5rem"},
                            ),
                            html.Span(
                                tr("rejects_label", lang),
                                className="fw-bold ms-3",
                                style={"fontSize": "1.2rem"},
                            ),
                            html.Span(
                                f"{total_rejects_fmt} {capacity_unit_label(weight_pref, False)}",
                                style={"fontFamily": NUMERIC_FONT, "fontSize": "2.5rem"},
                            ),
                            ],
                            className="d-flex justify-content-around",

                        )
                    ),
                    className="mt-2 bg-primary text-white",
                )
            )
        else:
            right_content.append(html.Div("No machines added yet", className="text-center text-muted py-4"))
    else:
        # Show machines for selected floor
        if selected_floor_machines:
            right_content.append(
                dbc.Row([
                    dbc.Col([
                        create_enhanced_machine_card_with_selection(
                            machine, ip_options, floors_data,
                            is_active=(machine['id'] == active_machine_id),
                            lang=lang
                        )

                    ], xs=6, md=4)
                    for machine in selected_floor_machines
                ])

            )
            right_content.append(
                dbc.Card(
                    dbc.CardBody(
                        html.Div(
                            [

                                html.Span(
                                    tr("total_production_label", lang),
                                    className="fw-bold",
                                    style={"fontSize": "1.2rem"},
                                ),
                                html.Span(
                                    f"{total_capacity_fmt} {capacity_unit_label(weight_pref)}",
                                    style={"fontFamily": NUMERIC_FONT, "fontSize": "2.5rem"},
                                ),
                                html.Span(
                                    tr("accepts_label", lang),
                                    className="fw-bold ms-3",
                                    style={"fontSize": "1.2rem"},
                                ),
                                html.Span(
                                    f"{total_accepts_fmt} {capacity_unit_label(weight_pref, False)}",
                                    style={"fontFamily": NUMERIC_FONT, "fontSize": "2.5rem"},
                                ),
                                html.Span(
                                    tr("rejects_label", lang),
                                    className="fw-bold ms-3",
                                    style={"fontSize": "1.2rem"},
                                ),
                                html.Span(
                                    f"{total_rejects_fmt} {capacity_unit_label(weight_pref, False)}",
                                    style={"fontFamily": NUMERIC_FONT, "fontSize": "2.5rem"},
                                ),
                            ],
                            className="d-flex justify-content-around",

                        )
                    ),
                    className="mt-2 bg-primary text-white",
                )
            )
            right_content.append(
                dbc.Button(tr("add_machine", lang), id="add-machine-btn", color="success", size="sm", className="mt-2")
            )
        elif selected_floor_id != "all":
            # Selected floor but no machines
            right_content.append(
                html.Div("No machines on this floor", className="text-center text-muted py-4")
            )
            right_content.append(
                dbc.Card(
                    dbc.CardBody(
                        html.Div(
                            [

                                html.Span(
                                    tr("total_production_label", lang),
                                    className="fw-bold",
                                    style={"fontSize": "1.2rem"},
                                ),
                                html.Span(
                                    f"{total_capacity_fmt} {capacity_unit_label(weight_pref)}",
                                    style={"fontFamily": NUMERIC_FONT, "fontSize": "2.5rem"},
                                ),
                                html.Span(
                                    tr("accepts_label", lang),
                                    className="fw-bold ms-3",
                                    style={"fontSize": "1.2rem"},
                                ),
                                html.Span(
                                    f"{total_accepts_fmt} {capacity_unit_label(weight_pref, False)}",
                                    style={"fontFamily": NUMERIC_FONT, "fontSize": "2.5rem"},
                                ),
                                html.Span(
                                    tr("rejects_label", lang),
                                    className="fw-bold ms-3",
                                    style={"fontSize": "1.2rem"},
                                ),
                                html.Span(
                                    f"{total_rejects_fmt} {capacity_unit_label(weight_pref, False)}",
                                    style={"fontFamily": NUMERIC_FONT, "fontSize": "2.5rem"},
                                ),
                            ],
                            className="d-flex justify-content-around",

                        )
                    ),
                    className="mt-2 bg-primary text-white",
                )
            )
            right_content.append(
                dbc.Button(tr("add_machine", lang), id="add-machine-btn", color="success", size="sm", className="mt-1")
            )
    
    # MAIN LAYOUT: Fixed left sidebar + dynamic right content
    return dbc.Row([
        # LEFT SIDEBAR (FIXED)
        dbc.Col([
            html.Div(left_sidebar_buttons)
        ], width=3, style={"alignSelf": "flex-start"}),
        
        # RIGHT CONTENT (DYNAMIC)
        dbc.Col([
            html.Div(right_content)
        ], width=9)
    ])


# Start auto-reconnection thread when app starts
def start_auto_reconnection():
    """Start the auto-reconnection thread"""
    if not hasattr(app_state, 'reconnection_thread') or not app_state.reconnection_thread.is_alive():
        app_state.reconnection_thread = Thread(target=auto_reconnection_thread)
        app_state.reconnection_thread.daemon = True
        app_state.reconnection_thread.start()
        logger.info("Started auto-reconnection thread")






# Replace create_enhanced_machine_card_main_pattern entirely with the new function


def create_enhanced_machine_card_with_selection(machine, ip_options, floors_data=None, is_all_view=False, is_active=False, lang=DEFAULT_LANGUAGE):
    """Create machine card with selection capability and new layout"""
    machine_id = machine['id']

    demo_mode = machine.get("demo_mode", False)
    
    # FIXED: Check connection status - be more inclusive of connected states
    machine_status = machine.get('status', 'Unknown')
    is_actually_connected = (
        machine_id in machine_connections
        and machine_connections[machine_id].get('connected', False)
        and machine_status not in ['Connection Lost', 'Connection Error', 'Offline', 'Unknown', 'Disconnected']
    ) or demo_mode
    
    # DEBUG: Add logging to see what's happening
    logger.debug(f"Machine {machine_id}: status='{machine_status}', in_connections={machine_id in machine_connections}, is_connected={is_actually_connected}")
    
    # Card styling based on connection status AND selection status - Use CSS classes with !important
    if is_active:
        # Active machine gets a special highlighted style (blue border to show it's selected)
        if is_actually_connected:
            card_class = "mb-2 machine-card-active-connected"
        else:
            card_class = "mb-2 machine-card-active-disconnected"
    elif is_actually_connected:
        # Connected but not active - green background
        card_class = "mb-2 machine-card-connected"
    else:
        # Disconnected - light grey background
        card_class = "mb-2 machine-card-disconnected"

    # Base style for positioning
    card_style = {
        "position": "relative",
        "cursor": "pointer",
        "transition": "all 0.2s ease-in-out",
        "flexWrap": "wrap"
    }
    
    # Get operational data ONLY if actually connected
    operational_data = machine.get("operational_data") if is_actually_connected else None
    
    # CREATE A CLICKABLE OVERLAY DIV
    clickable_overlay = html.Div(
        "",  # Empty content
        id={"type": "machine-card-click", "index": machine_id},
        n_clicks=0,
        style={
            "position": "absolute",
            "top": "0",
            "left": "0",
            "right": "0",
            "bottom": "0",
            "zIndex": "1",
            "cursor": "pointer",
            "backgroundColor": "transparent"
        },
        title=f"Click to select Machine {machine_id}"
    )

    # Get data for display
    if is_actually_connected and operational_data:
        # Extract operational data
        preset_info = operational_data.get('preset', {})
        preset_num = preset_info.get('number') if isinstance(preset_info, dict) else operational_data.get('preset_number')
        preset_name = preset_info.get('name') if isinstance(preset_info, dict) else operational_data.get('preset_name')
        
        if preset_num is not None and preset_name:
            preset_display = f"{preset_num} {preset_name}"
        elif preset_num is not None:
            preset_display = str(preset_num)
        else:
            preset_display = "N/A"
        
        # Get status info
        status_info = operational_data.get('status', {})
        machine_status_display = status_info.get('text') if isinstance(status_info, dict) else operational_data.get('status_text', 'Unknown')
        
        # Get feeder info
        feeder_info = operational_data.get('feeder', {})
        feeder_display = feeder_info.get('text') if isinstance(feeder_info, dict) else operational_data.get('feeder_status', 'Unknown')
        if feeder_display in ['Running', 'Stopped']:
            feeder_display_translated = tr('running_state', lang) if feeder_display == 'Running' else tr('stopped_state', lang)
        else:
            feeder_display_translated = feeder_display
        
        # Get production info
        production_info = operational_data.get('production', {})
        if isinstance(production_info, dict):
            capacity = production_info.get('capacity_formatted', '0')
            accepts = production_info.get('accepts_formatted', '0')
            rejects = production_info.get('rejects_formatted', '0')
            diagnostic = production_info.get('diagnostic_counter', '0')
        else:
            capacity = operational_data.get('capacity', '0')
            accepts = operational_data.get('accepts', '0')
            rejects = operational_data.get('rejects', '0')
            diagnostic = operational_data.get('diagnostic_counter', '0')
            
        # Connection status for display
        connection_status_display = "Demo" if demo_mode else "Connected"
        
    else:
        # Not connected - use default values
        preset_display = "N/A"
        machine_status_display = "Unknown"
        feeder_display = "Unknown"
        feeder_display_translated = feeder_display
        capacity = "0"
        accepts = "0"
        rejects = "0"
        diagnostic = "0"
        connection_status_display = "Not Connected"

    # Check for feeder running status and create triangle indicator
    triangle_indicator = None
    if is_actually_connected and operational_data:
        if feeder_display == "Running":
            triangle_indicator = html.Div([
                html.Div(
                    "",  # Empty div styled as triangle
                    style={
                        "width": "0",
                        "height": "0",
                        "borderLeft": "15px solid transparent",
                        "borderRight": "15px solid transparent", 
                        "borderTop": "20px solid #15FF00",  # Green triangle
                        "margin": "5px auto 0 auto"
                    }
                )
            ], className="text-center")

    return dbc.Card([
        # CLICKABLE OVERLAY
        clickable_overlay,
        
        dbc.CardBody([
            # Header row with machine title and delete button
            dbc.Row([
                dbc.Col([
                    html.H6(f"{tr('machine_label', lang)} {machine_id}", className="text-center mb-2")
                ], width=10),
                dbc.Col([
                    dbc.Button(
                        "×",
                        id={"type": "delete-machine-btn", "index": machine_id},
                        color="danger",
                        size="sm",
                        className="p-1",
                        style={
                            "fontSize": "0.8rem",
                            "width": "25px",
                            "height": "25px",
                            "borderRadius": "50%",
                            "lineHeight": "1",
                            "position": "relative",
                            "zIndex": "2"
                        },
                        title=f"Delete Machine {machine_id}"
                    )
                ], width=2, className="text-end")
            ], className="mb-0"),
            
            # Two-column layout (top section)
            dbc.Row([
                # Left column - Machine selection and basic info
                dbc.Col([
                    # Machine Selection Dropdown
                    html.Div([
                        html.Small(tr("select_machine_label", lang), className="mb-1 d-block"),
                        dcc.Dropdown(
                            id={"type": "machine-ip-dropdown", "index": machine_id},
                            options=ip_options,
                            value=machine.get('selected_ip', ip_options[0]['value'] if ip_options else None),
                            placeholder="Select Machine",
                            clearable=False,
                            className="machine-card-dropdown",
                            style={
                                "color": "black",
                                "position": "relative",
                                "zIndex": "2",
                                "width": "100%"
                            }
                        ),
                    ], className="mb-0"),
                    
                    # Connection Status with color
                    html.Div([
                        html.Small(
                            f"({connection_status_display})", 
                            className="d-block mb-1",
                            style={
                                "color": "#007bff" if is_actually_connected else "#dc3545",  # Blue if connected, Red if not
                                "fontSize": "1.2rem",
                                "fontWeight": "bold"
                            }
                        )
                    ]),
                    
                    # Model
                    html.Div([
                        html.Small(tr("model_label", lang), className="fw-bold", style={"fontSize": "1.2rem"}),
                        html.Small(machine.get('model', 'N/A'), style={"fontSize": "1.2rem"})
                    ], className="mb-1"),
                    
                    # Serial
                    html.Div([
                        html.Small(tr("serial_number_label", lang), className="fw-bold", style={"fontSize": "1.2rem"}),
                        html.Small(machine.get('serial', 'N/A'), style={"fontSize": "1.2rem"})
                    ], className="mb-0"),
                    
                ], md=6, sm=12),
                
                # Right column - Preset, Status, Feeder
                dbc.Col([
                    # Preset
                    html.Div([
                        html.Small(tr("preset_label", lang).upper(), className="fw-bold d-block", style={"fontSize": "1.2rem"}),
                        html.Small(preset_display, style={"fontSize": "1.5rem", "color": "#1100FF"})
                    ], className="mb-0"),
                    
                    # Machine Status with color coding
                    html.Div([
                        html.Small(tr("machine_status_label", lang), className="fw-bold d-block", style={"fontSize": "1.2rem"}),
                        html.Small(
                            tr(
                                'good_status' if machine_status_display == 'GOOD' else
                                'warning_status' if machine_status_display == 'WARNING' else
                                'fault_status' if machine_status_display == 'FAULT' else machine_status_display,
                                lang
                            ) if machine_status_display in ['GOOD','WARNING','FAULT'] else machine_status_display,
                            style={
                                "fontSize": "1.5rem",
                                "fontWeight": "bold",
                                "color": (
                                    "#15FF00" if machine_status_display == "GOOD" else  # Dark Green
                                    "#ffc107" if machine_status_display == "WARNING" else  # Orange
                                    "#dc3545" if machine_status_display == "FAULT" else  # Red
                                    "#6c757d"  # Dark Grey for Unknown/other
                                )
                            }
                        )
                    ], className="mb-0"),
                    
                    # Feeder with color coding and blinking indicator
                    html.Div([
                        html.Small(tr("feeder_label", lang), className="fw-bold d-block", style={"fontSize": "1.2rem"}),
                        html.Div([
                            html.Small(
                                feeder_display_translated,
                                style={
                                    "fontSize": "1.5rem",
                                    "fontWeight": "bold",
                                    "color": (
                                        "#15FF00" if feeder_display == "Running" else  # Dark Green
                                        "#6c757d"  # Dark Grey for Stopped/other
                                    )
                                }
                            ),
                            # Blinking neon green indicator when running
                            html.Span(
                                "●", 
                                style={
                                    "fontSize": "25px",
                                    "color": "#00ff00",  # Neon green
                                    "marginLeft": "8px",
                                    "animation": "blink 1s infinite"
                                }
                            ) if feeder_display == "Running" else html.Span()
                        ], style={"display": "flex", "alignItems": "center"})
                    ], className="mb-0"),
                    
                ], md=6, sm=12)
            ], className="mb-0"),
            
            # Production Data Section (center)
            html.Div([


                #html.Div("Production Data:", className="text-center fw-bold mb-0", style={"fontSize": "1.2rem"}),
                html.Div(
                    f"{capacity} {capacity_unit_label(load_weight_preference())}",
                    className="text-center production-data",
                    style={"fontSize": "2.6rem", "fontWeight": "bold","fontFamily": NUMERIC_FONT}
                )
            ], className="mb-0"),

            
            # Bottom section - Accepts, Rejects, Diag Count
            dbc.Row([
                # Accepts (left)
                dbc.Col([

                    html.Div(tr("accepts_label", lang), className="fw-bold text-center", style={"fontSize": "1.2rem"}),
                    html.Div(accepts, className="text-center", style={"fontSize": "1.9rem", "fontWeight": "bold","fontFamily": NUMERIC_FONT})

                ], md=6, sm=12),
                
                # Rejects and Diag Count (right)
                dbc.Col([

                    html.Div(tr("rejects_label", lang), className="fw-bold text-center", style={"fontSize": "1.2rem"}),
                    html.Div(rejects, className="text-center", style={"fontSize": "1.9rem", "fontWeight": "bold","fontFamily": NUMERIC_FONT}),
                    #html.Div("Diag Count:", className="fw-bold text-center mt-0", style={"fontSize": "0.65rem"}),

                    #html.Div(diagnostic, className="text-center", style={"fontSize": "0.8rem"})
                ], md=6, sm=12)
            ], className="mb-0"),


            
            # Add triangle indicator at the bottom if feeder is running
            triangle_indicator if triangle_indicator else html.Div()
        ], style={"position": "relative"})
    ],
    className=card_class,
    style=card_style
)



# Callback to handle floor deletion with auto-save



def get_machine_operational_data(machine_id):
    """Get operational data for a specific machine with enhanced real-time capability"""
    if machine_id not in machine_connections or not machine_connections[machine_id]['connected']:
        logger.info(f"DEBUG: Machine {machine_id} not connected or not in connections")
        return None
    
    connection_info = machine_connections[machine_id]
    tags = connection_info['tags']
    
    # Add debugging for localhost
    logger.info(f"DEBUG: Getting operational data for machine {machine_id}")
    logger.info(f"DEBUG: Available tags: {len(tags)}")
    
    # Tag definitions (same as section 2)
    PRESET_NUMBER_TAG = "Status.Info.PresetNumber"
    PRESET_NAME_TAG = "Status.Info.PresetName"
    GLOBAL_FAULT_TAG = "Status.Faults.GlobalFault"
    GLOBAL_WARNING_TAG = "Status.Faults.GlobalWarning"
    FEEDER_TAG_PREFIX = "Status.Feeders."
    FEEDER_TAG_SUFFIX = "IsRunning"
    MODEL_TAG = "Status.Info.Type"
    
    # Production tags
    CAPACITY_TAG = "Status.ColorSort.Sort1.Throughput.KgPerHour.Current"
    REJECTS_TAG = "Status.ColorSort.Sort1.Total.Percentage.Current"
    OPM_TAG = "Status.ColorSort.Sort1.Throughput.ObjectPerMin.Current"
    COUNTER_TAG = "Status.ColorSort.Sort1.DefectCount{}.Rate.Current"
    
    # NEW: Diagnostic counter tag
    DIAGNOSTIC_COUNTER_TAG = "Diagnostic.Counter"
    
    # Get preset information with current values
    preset_number = None
    preset_name = None
    
    if PRESET_NUMBER_TAG in tags:
        raw_value = tags[PRESET_NUMBER_TAG]["data"].latest_value
        if raw_value is not None:
            preset_number = raw_value
            logger.info(f"DEBUG: Preset number: {preset_number}")
            
    if PRESET_NAME_TAG in tags:
        raw_value = tags[PRESET_NAME_TAG]["data"].latest_value
        if raw_value is not None:
            preset_name = raw_value
            logger.info(f"DEBUG: Preset name: {preset_name}")
    
    # Get current status information
    has_fault = False
    has_warning = False
    
    if GLOBAL_FAULT_TAG in tags:
        raw_value = tags[GLOBAL_FAULT_TAG]["data"].latest_value
        has_fault = bool(raw_value) if raw_value is not None else False
        logger.info(f"DEBUG: Has fault: {has_fault}")
        
    if GLOBAL_WARNING_TAG in tags:
        raw_value = tags[GLOBAL_WARNING_TAG]["data"].latest_value
        has_warning = bool(raw_value) if raw_value is not None else False
        logger.info(f"DEBUG: Has warning: {has_warning}")
    
    # Determine status
    if has_fault:
        status_text = "FAULT"
    elif has_warning:
        status_text = "WARNING"
    else:
        status_text = "GOOD"
    
    logger.info(f"DEBUG: Status: {status_text}")
    
    # Get feeder status (check model type for number of feeders)
    model_type = None
    if MODEL_TAG in tags:
        model_type = tags[MODEL_TAG]["data"].latest_value
        logger.info(f"DEBUG: Model type: {model_type}")
    
    show_all_feeders = True if model_type != "RGB400" else False
    max_feeder = 4 if show_all_feeders else 2
    
    feeder_running = False
    for feeder_num in range(1, max_feeder + 1):
        tag_name = f"{FEEDER_TAG_PREFIX}{feeder_num}{FEEDER_TAG_SUFFIX}"
        if tag_name in tags:
            raw_value = tags[tag_name]["data"].latest_value
            if bool(raw_value) if raw_value is not None else False:
                feeder_running = True
                break
    
    feeder_text = "Running" if feeder_running else "Stopped"
    logger.info(f"DEBUG: Feeder status: {feeder_text}")
    
    # Get current production data
    total_capacity = 0
    reject_percentage = 0
    
    if CAPACITY_TAG in tags:
        capacity_value = tags[CAPACITY_TAG]["data"].latest_value
        if capacity_value is not None:
            pref = load_weight_preference()
            total_capacity = convert_capacity_from_kg(capacity_value, pref)
            logger.info(f"DEBUG: Capacity: {total_capacity}")
    
    reject_count = 0
    for i in range(1, 13):
        tname = COUNTER_TAG.format(i)
        if tname in tags:
            val = tags[tname]["data"].latest_value
            if val is not None:
                reject_count += val

    opm = 0
    if OPM_TAG in tags:
        opm_val = tags[OPM_TAG]["data"].latest_value
        if opm_val is not None:
            opm = opm_val
    
    # Get current diagnostic counter
    diagnostic_counter = 0
    if DIAGNOSTIC_COUNTER_TAG in tags:
        diagnostic_value = tags[DIAGNOSTIC_COUNTER_TAG]["data"].latest_value
        if diagnostic_value is not None:
            diagnostic_counter = diagnostic_value
            logger.info(f"DEBUG: Diagnostic counter: {diagnostic_counter}")
    
    # Calculate production values
    reject_pct = (reject_count / opm) if opm else 0
    rejects = total_capacity * reject_pct
    reject_percentage = reject_pct * 100
    accepts = total_capacity - rejects
    if accepts < 0:
        accepts = 0
    
    # Calculate percentages
    total = accepts + rejects
    accepts_percent = (accepts / total * 100) if total > 0 else 0
    rejects_percent = (rejects / total * 100) if total > 0 else 0
    
    # Format values with current timestamp influence
    capacity_formatted = f"{total_capacity:,.0f}"
    accepts_formatted = f"{accepts:,.0f}"
    rejects_formatted = f"{rejects:,.0f}"
    accepts_percent_formatted = f"{accepts_percent:.1f}"
    rejects_percent_formatted = f"{rejects_percent:.1f}"
    diagnostic_counter_formatted = f"{diagnostic_counter:,.0f}"
    
    operational_data = {
        'preset': {
            'number': preset_number,
            'name': preset_name
        },
        'status': {
            'text': status_text
        },
        'feeder': {
            'text': feeder_text
        },
        'production': {
            'capacity_formatted': capacity_formatted,
            'accepts_formatted': accepts_formatted,
            'rejects_formatted': rejects_formatted,
            'accepts_percent': accepts_percent_formatted,
            'rejects_percent': rejects_percent_formatted,
            'diagnostic_counter': diagnostic_counter_formatted
        }
    }
    
    logger.info(f"DEBUG: Returning operational data: {operational_data}")
    return operational_data

# Enhanced callback for floor name editing

# Enhanced callback for adding floors with auto-save




# Add this callback for manual save button (optional)

# Enhanced callback for adding machines with auto-save






# Callback to handle floor selection



def handle_floor_selection_simple(n1, n2, n3, n4, n5, floors_data):
    """Handle floor tile selection using simple server callback"""
    ctx = callback_context
    if not ctx.triggered:
        return dash.no_update
    
    # Get which floor was clicked
    triggered_id = ctx.triggered[0]["prop_id"]
    
    # Extract floor number from ID like "floor-tile-1.n_clicks"
    if "floor-tile-" in triggered_id:
        floor_id = int(triggered_id.split("floor-tile-")[1].split(".")[0])
        floors_data["selected_floor"] = floor_id
        return floors_data
    
    return dash.no_update


# Enhanced callback for machine IP selection with auto-save
    



# ######### UPDATE COUNTS SECTION ##############
# Callback to populate the Update Counts modal
# First, add the new tags to the KNOWN_TAGS dictionary at the top of your file:

KNOWN_TAGS = {
    # ... existing tags ...
    
    # Test weight settings tags - ADD THESE
    "Settings.ColorSort.TestWeightValue": "ns=2;s=Settings.ColorSort.TestWeightValue",
    "Settings.ColorSort.TestWeightCount": "ns=2;s=Settings.ColorSort.TestWeightCount",
    
    # ... rest of existing tags ...
}

# Updated callback for section-1-1b with OPC UA tag reading and writing
# Option 1: Add a "Refresh from OPC" button and modify the callback logic


# Callback to handle the pause/resume button





# Add a new callback to handle saving to OPC UA tags


# Update the save_user_inputs callback to mark when changes are made in live mode

# First, let's modify the section_1_2 callback to use the counter data




def startup_auto_connect_machines():
    """Automatically connect to all machines on startup"""
    try:
        # Load saved machines data
        floors_data, machines_data = load_floor_machine_data()
        
        if not machines_data or not machines_data.get("machines"):
            logger.info("No machines found for auto-connection")
            return
        
        machines = machines_data.get("machines", [])
        connected_count = 0
        
        logger.info(f"Attempting to auto-connect to {len(machines)} machines on startup...")
        
        for machine in machines:
            machine_id = machine.get("id")
            machine_ip = machine.get("selected_ip") or machine.get("ip")
            
            if not machine_ip:
                logger.info(f"Skipping machine {machine_id} - no IP address configured")
                continue
            
            if machine_id in machine_connections:
                logger.info(f"Machine {machine_id} already connected, skipping")
                continue
            
            try:
                logger.info(f"Auto-connecting to machine {machine_id} at {machine_ip}...")
                
                # Create a new event loop for this thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    # Use the existing connect function with proper async handling
                    connection_success = loop.run_until_complete(
                        connect_and_monitor_machine(machine_ip, machine_id, "Satake.EvoRGB.1")
                    )
                    
                    if connection_success:
                        logger.info(f"✓ Successfully auto-connected to machine {machine_id}")
                        connected_count += 1
                    else:
                        logger.warning(f"✗ Failed to auto-connect to machine {machine_id} - connection returned False")
                        
                except Exception as conn_error:
                    logger.warning(f"✗ Failed to auto-connect to machine {machine_id}: {conn_error}")
                finally:
                    loop.close()
                    
            except Exception as e:
                logger.error(f"Error in connection setup for machine {machine_id}: {e}")
        
        logger.info(f"Startup auto-connection complete: {connected_count}/{len(machines)} machines connected")
        
        # Start the main update thread if any machines connected
        try:
            floors_data, machines_data = load_floor_machine_data()
            if machines_data:
                app_state.machines_data_cache = machines_data
                logger.info(f"Populated machines cache with {len(machines_data.get('machines', []))} machines for auto-reconnection")
        except Exception as e:
            logger.error(f"Error populating machines cache: {e}")

        # Start the main update thread if any machines connected
        if connected_count > 0:
            if app_state.update_thread is None or not app_state.update_thread.is_alive():
                app_state.thread_stop_flag = False
                app_state.update_thread = Thread(target=opc_update_thread)
                app_state.update_thread.daemon = True
                app_state.update_thread.start()
                logger.info("Started OPC update thread for auto-connected machines")
        else:
            logger.info("No machines connected - auto-reconnection thread will handle retry attempts")
            
    except Exception as e:
        logger.error(f"Error in startup auto-connection: {e}")

def delayed_startup_connect():
    """Run startup auto-connection after a delay to avoid blocking app startup"""
    import time
    time.sleep(3)  # Wait 3 seconds for app to fully start
    startup_auto_connect_machines()


def create_matched_height_gauges(app_state_data, app_mode, mode, show_all_gauges=True):
    """Create vertical speed gauges matched to status box height with labels below"""
    import plotly.graph_objects as go
    
    # Define colors for running/stopped status
    green_color = "#28a745"  # Green
    gray_color = "#6c757d"   # Gray
    border_color = "#343a40"  # Dark color for borders
    
    # Determine number of feeders to show based on show_all_gauges parameter
    num_feeders = 4 if show_all_gauges else 2
    
    # Initialize arrays for gauge data
    x_positions = list(range(1, num_feeders + 1))  # Positions for the gauges
    values = []                  # Feed rate values
    colors = []                  # Colors based on running status
    
    # Process data for each feeder (only up to num_feeders)
    for i in range(1, num_feeders + 1):
        # Default values
        is_running = False
        feed_rate = 0
        
        # For demo mode
        if mode == "demo":
            is_running = True
            feed_rate = 90
        # For disconnected mode
        elif not app_state_data.get("connected", False):
            is_running = False
            feed_rate = 0
        # For live mode - FIXED to use proper app_state reference
        else:
            # Get running status
            running_tag = f"Status.Feeders.{i}IsRunning"
            if running_tag in app_state.tags:
                is_running = bool(app_state.tags[running_tag]["data"].latest_value)
            else:
                logger.debug(f"  - {running_tag} not found in app_state.tags")
                
            # Get feed rate
            rate_tag = f"Status.Feeders.{i}Rate"
            if rate_tag in app_state.tags:
                try:
                    raw_value = app_state.tags[rate_tag]["data"].latest_value
                    
                    if raw_value is None:
                        feed_rate = 0
                    elif isinstance(raw_value, (int, float)):
                        feed_rate = raw_value
                    elif isinstance(raw_value, str):
                        feed_rate = float(raw_value)
                    else:
                        feed_rate = 0
                        
                except Exception as e:
                    feed_rate = 0
            else:
                logger.debug(f"  - {rate_tag} not found in app_state.tags")
        
        # Store the feed rate and color
        values.append(feed_rate)
        colors.append(green_color if is_running else gray_color)
    
    # Create figure
    fig = go.Figure()

    # Add background containers with prominent borders
    fig.add_trace(go.Bar(
        x=x_positions,
        y=[100] * num_feeders,  # Full height for all feeders
        width=0.9,  # Width of bars
        marker=dict(
            color="rgba(248, 249, 250, 0.5)",  # Very light gray with transparency
            line=dict(color=border_color, width=2)  # Prominent border
        ),
        showlegend=False,
        hoverinfo='none'
    ))
    
    # Add labels below each gauge with dynamic font color for dark mode
    for i in range(num_feeders):
        # Get the x position for this gauge
        x_pos = x_positions[i]
        
        # Add label below each gauge with class for dark mode styling
        fig.add_annotation(
            x=x_pos,
            y=-15,  # Position below the gauge
            text=f"{tr(f'feeder_{i+1}', lang)} Rate",
            showarrow=False,
            font=dict(size=11, color="black"),  # Default color for light mode
            align="center",
            xanchor="center",
            yanchor="top"
        )

    # Add value bars
    fig.add_trace(go.Bar(
        x=x_positions,
        y=values,
        width=0.87,  # Slightly narrower to show background border
        marker=dict(color=colors),
        text=[f"{v}%" for v in values],
        textposition='inside',
        textfont=dict(color='white', size=11),  # Smaller font for compact layout
        hoverinfo='text',
        hovertext=[f"{tr(f'feeder_{i}', lang)}: {values[i-1]}%" for i in range(1, num_feeders + 1)],
        showlegend=False
    ))
    
    # Adjust the x-axis range based on number of feeders
    x_range = [0.45, num_feeders + 0.55] if num_feeders == 2 else [0.45, 4.5]
    
    # Update layout
    fig.update_layout(
        barmode='overlay',  # Overlay the background and value bars
        xaxis=dict(
            showticklabels=False,
            showgrid=False,
            zeroline=True,
            range=x_range  # Dynamic range based on number of feeders
        ),
        yaxis=dict(
            range=[-30, 110],  # Adjusted range to accommodate labels below
            showticklabels=False,  # Hide the y-axis tick labels
            showgrid=False,  # Hide the grid
            zeroline=True   # Show the zero line
        ),
        margin=dict(l=0, r=0, t=0, b=30),  # Increased bottom margin for labels
        height=95,  # Increased height to accommodate labels
        paper_bgcolor='rgba(0, 0, 0, 0)',
        plot_bgcolor='rgba(0, 0, 0, 0)',
        showlegend=False  # Explicitly hide legend
    )
    
    return fig


def create_feeder_rate_boxes(app_state_data, app_mode, mode, show_all_gauges=True):
    """Return a row of boxes showing feeder rates with running state colors."""
    num_feeders = 4 if show_all_gauges else 2

    boxes = []
    for i in range(1, num_feeders + 1):
        is_running = False
        feed_rate = 0

        if mode == "demo":
            is_running = True
            feed_rate = 90
        elif not app_state_data.get("connected", False):
            is_running = False
            feed_rate = 0
        else:
            running_tag = f"Status.Feeders.{i}IsRunning"
            if running_tag in app_state.tags:
                is_running = bool(app_state.tags[running_tag]["data"].latest_value)

            rate_tag = f"Status.Feeders.{i}Rate"
            if rate_tag in app_state.tags:
                try:
                    raw_value = app_state.tags[rate_tag]["data"].latest_value
                    if raw_value is None:
                        feed_rate = 0
                    elif isinstance(raw_value, (int, float)):
                        feed_rate = raw_value
                    elif isinstance(raw_value, str):
                        feed_rate = float(raw_value)
                except Exception:
                    feed_rate = 0

        bg_color = "#28a745" if is_running else "#6c757d"
        box = html.Div(
            f"Feeder {i}: {feed_rate}%",
            style={
                "backgroundColor": bg_color,
                "color": "white",
                "padding": "0.25rem 0.5rem",
                "borderRadius": "0.25rem",
                "fontSize": "1.3rem",
            },
        )
        boxes.append(box)

    # Allow wrapping so that the boxes don't overflow on narrow screens
    return html.Div(boxes, className="d-flex flex-wrap gap-2")











######BAR CHART###############
import math

# Initialize counter history with zeros so the dashboard starts from a
# predictable baseline instead of random demo data
previous_counter_values = [0] * 12

# Global variables for threshold settings



# Update the section 5-2 callback to include the threshold settings button and modal


############################### Sensitivity Trend Graph########################


# Callback for section 7-2



# Function to create display settings form
def create_display_settings_form(lang=_initial_lang):
    """Create a form for display settings"""
    if lang is None:
        lang = load_language_preference()
    global display_settings
    
    form_items = []
    
    # Define color dictionary (same as used in the graph)
    counter_colors = {
        1: "green",       # Blue
        2: "lightgreen",      # Green
        3: "orange",     # Orange
        4: "blue",      # Black
        5: "#f9d70b",    # Yellow
        6: "magenta",    # Magenta
        7: "cyan",       # Cyan
        8: "red",        # Red
        9: "purple",
        10: "brown",
        11: "gray",
        12: "lightblue"
    }
    
    # Create a styled switch for each counter
    for i in range(1, 13):
        # Get the color for this counter
        color = counter_colors.get(i, "black")
        
        form_items.append(
            dbc.Row([
                # Counter label with matching color
                dbc.Col(
                    html.Div(
                        f"{tr('sensitivity_label', lang)} {i}:",
                        className="fw-bold",
                        style={"color": color}
                    ),
                    width=4
                ),
                # Use a switch instead of a checkbox for better visibility
                dbc.Col(
                    dbc.Switch(
                        id={"type": "display-enabled", "index": i},
                        value=display_settings.get(i, True),  # Default to True if not in settings
                        label=tr("display_button", lang),
                    ),
                    width=8
                ),
            ], className="mb-2")
        )
    
    # Add header
    header = html.Div(
        tr("display_settings_header", lang),
        className="mb-3 fw-bold"
    )
    
    # Return the form with header
    return html.Div([header] + form_items)




# Callback to open/close the display settings modal and save settings

# Callback to open/close the production rate units modal

# Callback to process the uploaded image


# Callback to open/close the Update Counts modal



# ---------------------------------------------------------------------------
# Keep a global copy of the current application mode
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Metric logging every minute
# ---------------------------------------------------------------------------

register_callbacks(app)

# Main entry point
if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description="Run the OPC dashboard")

        def env_bool(name: str, default: bool) -> bool:
            val = os.getenv(name)
            if val is None:
                return default
            try:
                return bool(strtobool(val))
            except ValueError:
                return default

        open_browser_default = env_bool("OPEN_BROWSER", True)
        debug_default = env_bool("DEBUG", True)

        parser.add_argument(
            "--open-browser",
            dest="open_browser",
            action="store_true",
            default=open_browser_default,
            help="Automatically open the web browser (default: %(default)s)",
        )
        parser.add_argument(
            "--no-open-browser",
            dest="open_browser",
            action="store_false",
            help="Do not open the web browser automatically",
        )
        parser.add_argument(
            "--debug",
            dest="debug",
            action="store_true",
            default=debug_default,
            help="Run the app in debug mode (default: %(default)s)",
        )
        parser.add_argument(
            "--no-debug",
            dest="debug",
            action="store_false",
            help="Disable debug mode",
        )

        args = parser.parse_args()

        logger.info("Starting dashboard application...")

        logger.info("Initializing auto-connect logic...")
        autoconnect.initialize_autoconnect()

        saved_image = load_saved_image()
        if saved_image:
            # Set the saved image to the app's initial state
            #app.layout.children[-1].children["additional-image-store"].data = saved_image
            logger.info("Loaded saved custom image")

        # Get local IP address for network access
        import socket
        def get_ip():
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                # Doesn't need to be reachable
                s.connect(('10.255.255.255', 1))
                IP = s.getsockname()[0]
            except Exception:
                IP = '127.0.0.1'
            finally:
                s.close()
            return IP
        
        local_ip = get_ip()
        
        # Print access URLs
        print("\nDashboard Access URLs:")
        print(f"  Local access:    http://127.0.0.1:8050/")
        print(f"  Network access:  http://{local_ip}:8050/")
        print("\nPress Ctrl+C to exit the application\n")
        
        # Optionally open the dashboard in a browser window
        if args.open_browser:
            import webbrowser
            import threading

            def open_browser():
                import time
                time.sleep(1.5)
                webbrowser.open_new("http://127.0.0.1:8050/")

            threading.Thread(target=open_browser).start()

        # Start the Dash app
        app.run(debug=False, use_reloader=False, host='0.0.0.0', port=8050)
        
    except KeyboardInterrupt:
        # Disconnect on exit
        print("\nShutting down...")
        if app_state.connected:
            run_async(disconnect_from_server())
        print("Disconnected from server")
        print("Goodbye!")
