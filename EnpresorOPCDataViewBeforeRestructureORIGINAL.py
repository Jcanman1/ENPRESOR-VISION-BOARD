
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

      
#from dash import callback_context, no_update
try:
    from dash.exceptions import PreventUpdate
except Exception:  # pragma: no cover - optional dependency
    class PreventUpdate(Exception):
        pass

logging.getLogger('opcua').setLevel(logging.WARNING)  # Turn off OPC UA debug logs
logging.getLogger('opcua.client.ua_client').setLevel(logging.WARNING)
logging.getLogger('opcua.uaprotocol').setLevel(logging.WARNING)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger().handlers.clear()  # Remove default console handler
logger = logging.getLogger(__name__)

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

# Define known tags instead of discovering them
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

# Tags that are updated on every cycle in live mode.  These are the tags used
# throughout the dashboard callbacks for real time display.
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
        # Create a data directory if it doesn't exist
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
        action = "\u2191"  # Up arrow for increase
    elif new_value < old_value:
        action = "\u2193"  # Down arrow for decrease
    else:
        action = "\u2192"  # Right arrow for unchanged values
    
    # Create log entry
    entry = {
        "tag": tag_name,
        "action": action,
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
    machine_control_log = machine_control_log[:100]  # Keep only most recent 100

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
    machine_control_log = machine_control_log[:100]

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

# TagData class to store tag history
class TagData:
    def __init__(self, name, max_points=1000):
        self.name = name
        self.max_points = max_points
        self.timestamps = []
        self.values = []
        self.latest_value = None
        
    def add_value(self, value, timestamp=None):
        """Add a new value to the tag history"""
        if timestamp is None:
            timestamp = datetime.now()
        
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
    """Background thread for OPC UA updates - with machines data caching"""
    loop = get_event_loop()
    iteration = 0

    while not app_state.thread_stop_flag:
        try:
            iteration += 1
            # Cache machines data for auto-reconnection thread
            # This will be updated by callbacks and read by auto-reconnection
            
            # Update tags for ALL connected machines
            for machine_id, connection_info in list(machine_connections.items()):
                if connection_info.get('connected', False):
                    try:
                        # Update tags for this machine
                        for tag_name, node_info in connection_info['tags'].items():
                            if current_app_mode == "live":
                                if tag_name not in FAST_UPDATE_TAGS:
                                    continue
                            else:
                                if (
                                    tag_name not in FAST_UPDATE_TAGS
                                    and (iteration - 1) % SLOW_UPDATE_EVERY != 0
                                ):
                                    continue
                            try:
                                value = node_info['node'].get_value()
                                node_info['data'].add_value(value)

                                if tag_name in MONITORED_RATE_TAGS:
                                    prev_val = prev_values[machine_id][tag_name]
                                    if (
                                        prev_val is not None
                                        and value is not None
                                        and value != prev_val
                                    ):
                                        friendly = MONITORED_RATE_TAGS[tag_name]
                                        add_control_log_entry(
                                            friendly,
                                            prev_val,
                                            value,
                                            machine_id=machine_id,
                                        )
                                    prev_values[machine_id][tag_name] = value

                                if tag_name in SENSITIVITY_ACTIVE_TAGS:
                                    prev_act = prev_active_states[machine_id][tag_name]
                                    if (
                                        prev_act is not None
                                        and value is not None
                                        and bool(value) != bool(prev_act)
                                    ):
                                        sens_num = SENSITIVITY_ACTIVE_TAGS[tag_name]
                                        add_activation_log_entry(
                                            sens_num,
                                            bool(value),
                                            machine_id=machine_id,
                                        )
                                    prev_active_states[machine_id][tag_name] = value
                            except Exception as e:
                                logger.debug(
                                    f"Error updating tag {tag_name} for machine {machine_id}: {e}"
                                )
                        
                        connection_info['last_update'] = datetime.now()
                        
                    except Exception as e:
                        logger.warning(f"Error updating machine {machine_id}: {e}")
                        connection_info['connected'] = False
            
            # Update app_state for active machine
            if active_machine_id and active_machine_id in machine_connections:
                connection_info = machine_connections[active_machine_id]
                if connection_info.get('connected', False):
                    app_state.tags = connection_info['tags']
                    app_state.connected = True
                    app_state.last_update_time = connection_info['last_update']
                    app_state.client = connection_info['client']
                else:
                    app_state.connected = False
                    app_state.tags = {}
            else:
                app_state.connected = False
                app_state.tags = {}
                
        except Exception as e:
            logger.error(f"Error in OPC update thread: {e}")
            
        time.sleep(1)

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

def create_threshold_settings_form():
    """Create a form for threshold settings"""
    form_rows = []
    
    # Create row for each counter
    for i in range(1, 13):
        settings = threshold_settings[i]
        
        form_rows.append(
            dbc.Row([
                # Counter label
                dbc.Col(html.Div(f"Sensitivity {i}:", className="fw-bold"), width=2),
                                                
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
            dbc.Col(html.Div("Email Notifications:", className="fw-bold"), width=2),
            
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
        html.Div(id="threshold-form-container", children=create_threshold_settings_form())
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
connection_controls = dbc.Card(
    dbc.CardBody([
        dbc.Row([
            # Active Machine Info (replacing IP dropdown section)
            dbc.Col([
                html.Div([
                    html.Span(tr("active_machine_label", _initial_lang), id="active-machine-label", className="fw-bold small me-1"),
                    html.Span(id="active-machine-display", className="small"),
                ], className="mt-1"),
            ], width={"xs":3, "md":3}, className="px-1"),
            
            # Status (keep this)
            dbc.Col([
                html.Div([
                    html.Span(tr("status_label", _initial_lang), id="status-label", className="fw-bold small me-1"),
                    html.Span(tr("no_machine_selected", _initial_lang), id="connection-status", className="text-warning small"),
                ], className="mt-1 ms-2"),
            ], width={"xs":2, "md":2}, className="px-1"),
            
            # Mode Selector (keep this)
            dbc.Col([
                dcc.Dropdown(
                    id="mode-selector",
                    options=[
                        {"label": tr("live_mode_option", _initial_lang), "value": "live"},
                        {"label": tr("demo_mode_option", _initial_lang), "value": "demo"},
                        {"label": tr("historical_mode_option", _initial_lang), "value": "historical"},
                    ],
                    value="live",  # Default to live mode
                    clearable=False,
                    searchable=False,
                    className="small p-0",
                    style={"min-width": "80px"}
                ),
            ], width={"xs":1, "md":1}, className="px-1"),
            
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
                                    tr("export_data"),
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
            ], width={"xs":4, "md":4}, className="px-1"),  # Increased width since we removed other elements

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
            ], label="Display"),
            
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
            ], label="System"),

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
            ], label="Email Setup"),
            
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
            ], label="About"),
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
        
        # Create data directory if it doesn't exist
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

        # Add ALL section placeholders as hidden elements
        html.Div([
            html.Div(id="section-1-1", children=[], style={"display": "none"}),
            html.Div(id="section-1-2", children=[], style={"display": "none"}),
            html.Div(id="section-2", children=[], style={"display": "none"}),
            html.Div(id="section-3-1", children=[], style={"display": "none"}),
            html.Div(id="section-3-2", children=[], style={"display": "none"}),
            html.Div(id="section-4", children=[], style={"display": "none"}),
            html.Div(id="section-5-1", children=[], style={"display": "none"}),
            html.Div(id="section-5-2", children=[], style={"display": "none"}),
            html.Div(id="section-6-1", children=[], style={"display": "none"}),
            html.Div(id="section-6-2", children=[], style={"display": "none"}),
            html.Div(id="section-7-1", children=[], style={"display": "none"}),
            html.Div(id="section-7-2", children=[], style={"display": "none"}),
        ])
    ])







def render_new_dashboard():
    return _render_new_dashboard()


def render_main_dashboard():
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
dcc.Store(id="ip-addresses-store", data=initial_ip_addresses),
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
    dcc.Store(id="app-mode",                data={"mode": "live"}),
    # Store used only to trigger the callback that updates the global
    # ``current_app_mode`` variable.
    dcc.Store(id="app-mode-tracker"),
    dcc.Store(id="ip-addresses-store",      data=load_ip_addresses()),
    dcc.Store(id="additional-image-store",  data=load_saved_image()),
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

    # ─── Title bar + Dashboard-toggle button ───────────────────────────────
    html.Div([
        html.H3(id="dashboard-title", children=tr("dashboard_title"), className="m-0"),
        dbc.Button(tr("switch_dashboards"),
                   id="new-dashboard-btn",
                   color="light", size="sm", className="ms-2"),
        dbc.Button(tr("generate_report"),
                   id="generate-report-btn",
                   color="light", size="sm", className="ms-2"),
        dcc.Download(id="report-download"),
    ], className="d-flex justify-content-between align-items-center bg-primary text-white p-2 mb-2"),

    # ─── Connection controls (always visible) ──────────────────────────────
    connection_controls,

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
            html.Div(id="upload-status")
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


# Create a client-side callback to handle theme switching
app.clientside_callback(
    """
    function(theme) {
        console.log('Theme callback triggered with:', theme);
        
        // Get root document element
        const root = document.documentElement;
        
        // Define theme colors
        const themeColors = {
            light: {
                backgroundColor: "#f0f0f0",
                cardBackgroundColor: "#ffffff",
                textColor: "#212529",
                borderColor: "rgba(0,0,0,0.125)",
                chartBackgroundColor: "rgba(255,255,255,0.9)"
            },
            dark: {
                backgroundColor: "#202124",
                cardBackgroundColor: "#2d2d30",
                textColor: "#e8eaed",
                borderColor: "rgba(255,255,255,0.125)",
                chartBackgroundColor: "rgba(45,45,48,0.9)"
            }
        };
        
        // Apply selected theme
        if (theme === "dark") {
            // Dark mode
            root.style.setProperty("--bs-body-bg", themeColors.dark.backgroundColor);
            root.style.setProperty("--bs-body-color", themeColors.dark.textColor);
            root.style.setProperty("--bs-card-bg", themeColors.dark.cardBackgroundColor);
            root.style.setProperty("--bs-card-border-color", themeColors.dark.borderColor);
            root.style.setProperty("--chart-bg", themeColors.dark.chartBackgroundColor);
            
            // Add dark-mode class to body for additional CSS targeting
            document.body.classList.add("dark-mode");
            document.body.classList.remove("light-mode");
            
            // Store theme preference in localStorage
            localStorage.setItem("satake-theme", "dark");
        } else {
            // Light mode (default)
            root.style.setProperty("--bs-body-bg", themeColors.light.backgroundColor);
            root.style.setProperty("--bs-body-color", themeColors.light.textColor);
            root.style.setProperty("--bs-card-bg", themeColors.light.cardBackgroundColor);
            root.style.setProperty("--bs-card-border-color", themeColors.light.borderColor);
            root.style.setProperty("--chart-bg", themeColors.light.chartBackgroundColor);
            
            // Add light-mode class to body for additional CSS targeting
            document.body.classList.add("light-mode");
            document.body.classList.remove("dark-mode");
            
            // Store theme preference in localStorage
            localStorage.setItem("satake-theme", "light");
        }
        
        // Update all Plotly charts with new theme
        if (window.Plotly) {
            const plots = document.querySelectorAll('.js-plotly-plot');
            plots.forEach(plot => {
                try {
                    const bgColor = theme === "dark" ? themeColors.dark.chartBackgroundColor : themeColors.light.chartBackgroundColor;
                    const textColor = theme === "dark" ? themeColors.dark.textColor : themeColors.light.textColor;
                    
                    Plotly.relayout(plot, {
                        'paper_bgcolor': bgColor,
                        'plot_bgcolor': bgColor,
                        'font.color': textColor
                    });
                } catch (e) {
                    console.error('Error updating Plotly chart:', e);
                }
            });
            
            // Special handling for feeder gauges - update annotation colors specifically
            const feederGauge = document.getElementById('feeder-gauges-graph');
            if (feederGauge && feederGauge.layout && feederGauge.layout.annotations) {
                try {
                    const labelColor = theme === "dark" ? themeColors.dark.textColor : themeColors.light.textColor;
                    
                    // Update annotation colors (feed rate labels)
                    const updatedAnnotations = feederGauge.layout.annotations.map(annotation => ({
                        ...annotation,
                        font: {
                            ...annotation.font,
                            color: labelColor
                        }
                    }));
                    
                    // Apply the updated annotations
                    Plotly.relayout(feederGauge, {
                        'annotations': updatedAnnotations
                    });
                    
                    console.log('Updated feeder gauge label colors for', theme, 'mode');
                } catch (e) {
                    console.error('Error updating feeder gauge labels:', e);
                }
            }
        }
        
        return theme;
    }
    """,
    Output("theme-selector", "value", allow_duplicate=True),
    Input("theme-selector", "value"),
    prevent_initial_call=True
)




@app.callback(
    Output("dashboard-content", "children"),
    Input("current-dashboard", "data")
)
def render_dashboard(which):
    if which == "new":
        return render_new_dashboard()  # Now includes hidden placeholders
    else:
        return render_main_dashboard()
    
@app.callback(
    Output("current-dashboard", "data"),
    Input("new-dashboard-btn", "n_clicks"),
    State("current-dashboard", "data"),
    prevent_initial_call=False
)
def manage_dashboard(n_clicks, current):
    # On first load n_clicks is None → show the new dashboard
    if n_clicks is None:
        return "new"

    # On every actual click, flip between “main” and “new”
    return "new" if current == "main" else "main"

@app.callback(
    Output("export-data-button", "disabled"),
    [Input("status-update-interval", "n_intervals")],
    [State("active-machine-store", "data")]
)
def update_export_button(n_intervals, active_machine_data):
    """Enable or disable the export button based on connection state."""

    active_machine_id = active_machine_data.get("machine_id") if active_machine_data else None
    is_connected = (
        active_machine_id
        and active_machine_id in machine_connections
        and machine_connections[active_machine_id].get("connected", False)
    )

    return not is_connected


@app.callback(
    Output("export-download", "data"),
    [Input("export-data-button", "n_clicks")],
    [State("active-machine-store", "data")],
    prevent_initial_call=True,
)
def export_all_tags(n_clicks, active_machine_data):
    """Perform full tag discovery and export when the button is clicked."""
    if not n_clicks:
        raise PreventUpdate

    active_machine_id = active_machine_data.get("machine_id") if active_machine_data else None
    if (
        not active_machine_id
        or active_machine_id not in machine_connections
        or not machine_connections[active_machine_id].get("connected", False)
    ):
        raise PreventUpdate

    pause_update_thread()
    client = machine_connections[active_machine_id]["client"]
    all_tags = run_async(discover_all_tags(client))
    csv_string = generate_csv_string(all_tags)
    resume_update_thread()

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    return {
        "content": csv_string,
        "filename": f"satake_data_export_{timestamp_str}.csv",
    }


@app.callback(
    Output("report-download", "data"),
    Input("generate-report-btn", "n_clicks"),
    prevent_initial_call=True,
)
def generate_report_callback(n_clicks):
    """Generate a PDF report when the button is clicked.

    ``generate_report.fetch_last_24h_metrics`` now returns a dictionary of
    machine ids mapped to their historical data.  The callback simply passes
    this structure to ``build_report``.
    """
    if not n_clicks:
        raise PreventUpdate

    data = generate_report.fetch_last_24h_metrics()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        generate_report.build_report(data, tmp.name)
        with open(tmp.name, "rb") as f:
            pdf_bytes = f.read()

    pdf_b64 = base64.b64encode(pdf_bytes).decode()
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    return {
        "content": pdf_b64,
        "filename": f"production_report_{timestamp_str}.pdf",
        "type": "application/pdf",
        "base64": True,
    }


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


@app.callback(
    [Output("delete-confirmation-modal", "is_open"),
     Output("delete-pending-store", "data"),
     Output("delete-item-details", "children")],
    [Input({"type": "delete-floor-btn", "index": ALL}, "n_clicks"),
     Input({"type": "delete-machine-btn", "index": ALL}, "n_clicks"),
     Input("cancel-delete-btn", "n_clicks"),
     Input("close-delete-modal", "n_clicks")],
    [State("delete-confirmation-modal", "is_open"),
     State({"type": "delete-floor-btn", "index": ALL}, "id"),
     State({"type": "delete-machine-btn", "index": ALL}, "id"),
     State("floors-data", "data"),
     State("machines-data", "data")],
    prevent_initial_call=True
)
def handle_delete_confirmation_modal(floor_delete_clicks, machine_delete_clicks, cancel_clicks, close_clicks,
                                   is_open, floor_ids, machine_ids, floors_data, machines_data):
    """Handle opening and closing the delete confirmation modal"""
    ctx = callback_context
    if not ctx.triggered:
        return dash.no_update, dash.no_update, dash.no_update
    
    triggered_prop = ctx.triggered[0]["prop_id"]
    
    # Handle cancel or close buttons
    if "cancel-delete-btn" in triggered_prop or "close-delete-modal" in triggered_prop:
        if cancel_clicks or close_clicks:
            return False, {"type": None, "id": None, "name": None}, ""
    
    # Handle floor delete button clicks
    elif '"type":"delete-floor-btn"' in triggered_prop:
        for i, clicks in enumerate(floor_delete_clicks):
            if clicks and i < len(floor_ids):
                floor_id = floor_ids[i]["index"]
                
                # Find floor name
                floor_name = f"Floor {floor_id}"
                if floors_data and floors_data.get("floors"):
                    for floor in floors_data["floors"]:
                        if floor["id"] == floor_id:
                            floor_name = floor["name"]
                            break
                
                # Count machines on this floor
                machine_count = 0
                if machines_data and machines_data.get("machines"):
                    machine_count = len([m for m in machines_data["machines"] if m.get("floor_id") == floor_id])
                
                # Create confirmation message
                if machine_count > 0:
                    details = html.Div([
                        html.P(f'Floor: "{floor_name}"', className="fw-bold mb-1"),
                        html.P(f"This will also delete {machine_count} machine(s) on this floor.", 
                              className="text-warning small"),
                        html.P("This action cannot be undone.", className="text-danger small")
                    ])
                else:
                    details = html.Div([
                        html.P(f'Floor: "{floor_name}"', className="fw-bold mb-1"),
                        html.P("This action cannot be undone.", className="text-danger small")
                    ])
                
                return True, {"type": "floor", "id": floor_id, "name": floor_name}, details
    
    # Handle machine delete button clicks  
    elif '"type":"delete-machine-btn"' in triggered_prop:
        for i, clicks in enumerate(machine_delete_clicks):
            if clicks and i < len(machine_ids):
                machine_id = machine_ids[i]["index"]
                
                # Find machine name/details
                current_lang = load_language_preference()
                machine_name = f"{tr('machine_label', current_lang)} {machine_id}"
                machine_details = ""
                if machines_data and machines_data.get("machines"):
                    for machine in machines_data["machines"]:
                        if machine["id"] == machine_id:
                            serial = machine.get("serial", "Unknown")
                            ip = machine.get("ip", "Unknown")
                            if serial != "Unknown":
                                machine_details = f"Serial: {serial}"
                            if ip != "Unknown":
                                if machine_details:
                                    machine_details += f" | IP: {ip}"
                                else:
                                    machine_details = f"IP: {ip}"
                            break
                
                # Create confirmation message
                details = html.Div([
                    html.P(f"{tr('machine_label', current_lang)}: \"{machine_name}\"", className="fw-bold mb-1"),
                    html.P(machine_details, className="small mb-1") if machine_details else html.Div(),
                    html.P("This action cannot be undone.", className="text-danger small")
                ])
                
                return True, {"type": "machine", "id": machine_id, "name": machine_name}, details
    
    return dash.no_update, dash.no_update, dash.no_update


@app.callback(
    [Output("system-settings-save-status", "children", allow_duplicate=True),
     Output("weight-preference-store", "data", allow_duplicate=True)],
    [Input("save-system-settings", "n_clicks")],
    [State("auto-connect-switch", "value"),
     State("ip-addresses-store", "data"),
     State("capacity-units-selector", "value"),
     State("custom-unit-name", "value"),
     State("custom-unit-weight", "value")],
    prevent_initial_call=True
)
def save_system_settings(n_clicks, auto_connect, ip_addresses,
                         unit_value, custom_name, custom_weight):
    """Save system settings including IP addresses"""
    if not n_clicks:
        return dash.no_update, dash.no_update
    
    # Save system settings
    system_settings = {
        "auto_connect": auto_connect
    }
    
    # Save system settings to file
    try:
        with open('system_settings.json', 'w') as f:
            json.dump(system_settings, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving system settings: {e}")
        return "Error saving system settings", dash.no_update
    
    # Save IP addresses to file - make sure we're getting the full data structure
    try:
        with open('ip_addresses.json', 'w') as f:
            json.dump(ip_addresses, f, indent=4)
        logger.info(f"Saved IP addresses: {ip_addresses}")
    except Exception as e:
        logger.error(f"Error saving IP addresses: {e}")
        return "Error saving IP addresses", dash.no_update

    # Save weight preference
    pref_data = dash.no_update
    if unit_value != "custom":
        save_weight_preference(unit_value, "", 1.0)
        pref_data = {"unit": unit_value, "label": "", "value": 1.0}
    elif custom_name and custom_weight:
        save_weight_preference("custom", custom_name, float(custom_weight))
        pref_data = {"unit": "custom", "label": custom_name,
                     "value": float(custom_weight)}

    return "Settings saved successfully", pref_data


@app.callback(
    [Output("email-settings-save-status", "children"),
     Output("email-settings-store", "data", allow_duplicate=True)],
    Input("save-email-settings", "n_clicks"),
    [State("smtp-server-input", "value"),
     State("smtp-port-input", "value"),
     State("smtp-username-input", "value"),
     State("smtp-password-input", "value"),
     State("smtp-sender-input", "value")],
    prevent_initial_call=True
)
def save_email_settings_callback(n_clicks, server, port, username, password, sender):
    """Save SMTP email credentials from the settings modal."""
    if not n_clicks:
        return dash.no_update, dash.no_update

    settings = {
        "smtp_server": server or DEFAULT_EMAIL_SETTINGS["smtp_server"],
        "smtp_port": int(port) if port else DEFAULT_EMAIL_SETTINGS["smtp_port"],
        "smtp_username": username or "",
        "smtp_password": password or "",
        "from_address": sender or DEFAULT_EMAIL_SETTINGS["from_address"],
    }

    success = save_email_settings(settings)
    if success:
        global email_settings
        email_settings = settings
        return "Email settings saved", settings
    return "Error saving email settings", dash.no_update

# Callback to open/close the settings modal
@app.callback(
    Output("settings-modal", "is_open"),
    [
        Input("settings-button", "n_clicks"),
        Input("close-settings", "n_clicks"),
        # REMOVE Input("save-system-settings", "n_clicks"), to prevent closing on save
    ],
    [State("settings-modal", "is_open")],
    prevent_initial_call=True
)
def toggle_settings_modal(settings_clicks, close_clicks, is_open):
    """Toggle the settings modal"""
    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update
        
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
    
    if trigger_id == "settings-button" and settings_clicks:
        return not is_open
    elif trigger_id == "close-settings" and close_clicks:
        return False
    # REMOVED the case for "save-system-settings"
    
    return is_open

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

@app.callback(
    [Output("ip-addresses-store", "data"),
     Output("new-ip-input", "value"),
     Output("new-ip-label", "value"),
     Output("system-settings-save-status", "children")],
    [Input("add-ip-button", "n_clicks")],
    [State("new-ip-input", "value"),
     State("new-ip-label", "value"),
     State("ip-addresses-store", "data")],
    prevent_initial_call=True
)


# REPLACE with this enhanced validation:
def add_ip_address(n_clicks, new_ip, new_label, current_data):
    """Add a new IP address to the stored list"""
    if not n_clicks or not new_ip or not new_ip.strip():
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update
    
    # Use a default label if none provided
    if not new_label or not new_label.strip():
        current_lang = load_language_preference()
        new_label = f"{tr('machine_label', current_lang)} {len(current_data.get('addresses', [])) + 1}"
    
    # Enhanced IP validation to allow localhost formats
    new_ip = new_ip.strip().lower()
    
    # Check for valid localhost formats
    localhost_formats = [
        "localhost",
        "127.0.0.1",
        "::1"  # IPv6 localhost
    ]
    
    is_valid_ip = False
    
    # Check if it's a localhost format
    if new_ip in localhost_formats:
        is_valid_ip = True
        # Normalize localhost to 127.0.0.1 for consistency
        if new_ip == "localhost":
            new_ip = "127.0.0.1"
    else:
        # Check for regular IPv4 format
        ip_parts = new_ip.split('.')
        if len(ip_parts) == 4:
            try:
                # Validate each part is a number between 0-255
                if all(part.isdigit() and 0 <= int(part) <= 255 for part in ip_parts):
                    is_valid_ip = True
            except ValueError:
                pass
        
        # Check for hostname format (letters, numbers, dots, hyphens)
        import re
        hostname_pattern = r'^[a-zA-Z0-9.-]+$'
        if re.match(hostname_pattern, new_ip) and len(new_ip) > 0:
            is_valid_ip = True
    
    if not is_valid_ip:
        return dash.no_update, "", dash.no_update, "Invalid IP address, hostname, or localhost format"
    
    # Get current addresses or initialize empty list
    addresses = current_data.get("addresses", []) if current_data else []
    
    # Check if IP already exists
    ip_already_exists = any(item["ip"] == new_ip for item in addresses)
    if ip_already_exists:
        return dash.no_update, "", dash.no_update, "IP address already exists"
    
    # Add the new IP with label
    addresses.append({"ip": new_ip, "label": new_label})
    
    # Return updated data and clear the inputs
    return {"addresses": addresses}, "", "", "IP address added successfully"


@app.callback(
    [
        Output("connection-status", "children"),
        Output("connection-status", "className"),
        Output("active-machine-display", "children"),
        Output("active-machine-label", "children"),
        Output("status-label", "children"),
    ],
    [
        Input("status-update-interval", "n_intervals"),
        Input("active-machine-store", "data"),
        Input("language-preference-store", "data"),
    ],
    [
        State("machines-data", "data"),
        State("app-state", "data"),
    ],
    prevent_initial_call=False  # Allow initial call to set default state
)
def update_connection_status_display(n_intervals, active_machine_data, lang, machines_data, app_state_data):
    """Update the connection status and active machine display"""
    
    # Get active machine ID
    active_machine_id = active_machine_data.get("machine_id") if active_machine_data else None
    
    if not active_machine_id:
        # No machine selected
        return tr("no_machine_selected", lang), "text-warning small", "None", tr("active_machine_label", lang), tr("status_label", lang)
    
    # Find the active machine details
    machine_info = None
    if machines_data and machines_data.get("machines"):
        for machine in machines_data["machines"]:
            if machine["id"] == active_machine_id:
                machine_info = machine
                break
    
    if not machine_info:
        return "Machine not found", "text-danger small", f"{tr('machine_label', lang)} {active_machine_id} (not found)", tr("active_machine_label", lang), tr("status_label", lang)
    
    # Check if this machine is actually connected
    is_connected = (active_machine_id in machine_connections and 
                   machine_connections[active_machine_id].get('connected', False))
    
    # Create machine display text
    serial = machine_info.get('serial', 'Unknown')
    if serial != 'Unknown':
        machine_display = f"{tr('machine_label', lang)} {active_machine_id} (S/N: {serial})"
    else:
        machine_display = f"{tr('machine_label', lang)} {active_machine_id}"
    
    # Determine status
    if is_connected:
        status_text = tr("connected_status", lang)
        status_class = "text-success small"
    else:
        status_text = tr("disconnected_status", lang)
        status_class = "text-warning small"
    return status_text, status_class, machine_display, tr("active_machine_label", lang), tr("status_label", lang)

# FIND this callback (the machine dashboard update):
@app.callback(
    Output("machines-data", "data", allow_duplicate=True),
    [Input("status-update-interval", "n_intervals"),
     Input("historical-time-index", "data"),
     Input("app-mode", "data")],
    [State("machines-data", "data"),
     State("production-data-store", "data"),
     State("weight-preference-store", "data")],
    prevent_initial_call=True,
)
def update_machine_dashboard_data(n_intervals, time_state, app_mode, machines_data, production_data, weight_pref):
    """Update machine data on every interval.

    In live mode this checks connection status and pulls fresh values from the
    OPC server.  When running in demo mode we synthesize values matching the
    main dashboard so that all machine cards show changing production data.
    """
    
    if not machines_data or not machines_data.get("machines"):
        return dash.no_update

    machines = machines_data.get("machines", [])
    updated = False

    mode = "demo"
    if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
        mode = app_mode["mode"]

    if mode == "historical":
        hours = time_state.get("hours", 24) if isinstance(time_state, dict) else 24
        for machine in machines:
            machine_id = machine.get("id")
            hist = get_historical_data(timeframe=f"{hours}h", machine_id=machine_id)
            cap_vals = hist.get("capacity", {}).get("values", [])
            acc_vals = hist.get("accepts", {}).get("values", [])
            rej_vals = hist.get("rejects", {}).get("values", [])
            cap_avg_lbs = sum(cap_vals)/len(cap_vals) if cap_vals else 0
            acc_avg_lbs = sum(acc_vals)/len(acc_vals) if acc_vals else 0
            rej_avg_lbs = sum(rej_vals)/len(rej_vals) if rej_vals else 0
            cap_avg = convert_capacity_from_lbs(cap_avg_lbs, weight_pref)
            acc_avg = convert_capacity_from_lbs(acc_avg_lbs, weight_pref)
            rej_avg = convert_capacity_from_lbs(rej_avg_lbs, weight_pref)
            prod = {
                "capacity_formatted": f"{cap_avg:,.0f}",
                "accepts_formatted": f"{acc_avg:,.0f}",
                "rejects_formatted": f"{rej_avg:,.0f}",
                "diagnostic_counter": (machine.get("operational_data") or {}).get("production", {}).get("diagnostic_counter", "0"),
            }
            if not machine.get("operational_data"):
                machine["operational_data"] = {"preset": {}, "status": {}, "feeder": {}, "production": prod}
            else:
                machine["operational_data"].setdefault("production", {})
                machine["operational_data"]["production"].update(prod)
        machines_data["machines"] = machines
        return machines_data
    

    if mode == "demo":
        now_str = datetime.now().strftime("%H:%M:%S")
        new_machines = []

        pref = load_weight_preference()

        for machine in machines:
            m = machine.copy()
            demo_lbs = random.uniform(47000, 53000)
            cap = convert_capacity_from_kg(demo_lbs / 2.205, pref)
            rej_pct = random.uniform(4.0, 6.0)
            rej = cap * (rej_pct / 100.0)
            acc = cap - rej

            counters = [random.randint(10, 180) for _ in range(12)]

            m["serial"] = m.get("serial", f"DEMO_{m.get('id')}")
            m["status"] = "DEMO"
            m["model"] = m.get("model", "Enpresor")
            m["last_update"] = now_str
            m["operational_data"] = {
                "preset": {"number": 1, "name": "Demo"},
                "status": {"text": "DEMO"},
                "feeder": {"text": "Running"},
                "production": {
                    "capacity_formatted": f"{cap:,.0f}",
                    "accepts_formatted": f"{acc:,.0f}",
                    "rejects_formatted": f"{rej:,.0f}",
                    "diagnostic_counter": "0",
                    "capacity": cap,
                    "accepts": acc,
                    "rejects": rej,
                },
            }
            m["demo_counters"] = counters
            m["demo_mode"] = True
            new_machines.append(m)

        machines_data = machines_data.copy()
        machines_data["machines"] = new_machines
        return machines_data


    # Update ALL machines that should be connected
    for machine in machines:
        machine_id = machine.get("id")
        machine.pop("demo_mode", None)

        if machine_id not in machine_connections or not machine_connections.get(machine_id, {}).get('connected', False):
            if machine.get("status") != "Offline":
                machine["status"] = "Offline"
                machine["last_update"] = "Never"
                machine["operational_data"] = None
                updated = True
            continue

        if machine_id in machine_connections:
            try:
                connection_info = machine_connections[machine_id]
                
                # Check if connection is still alive by trying to read a simple tag
                is_still_connected = False
                if connection_info.get('connected', False):
                    try:
                        # Try to read the Alive tag or any reliable tag to test connection
                        alive_tag = "Alive"
                        test_successful = False

                        if alive_tag in connection_info['tags']:
                            # Try to read the value - if this fails, connection is dead
                            test_value = connection_info['tags'][alive_tag]['node'].get_value()
                            test_successful = True
                        else:
                            # If no Alive tag, try the first available tag
                            for tag_name, tag_info in connection_info['tags'].items():
                                try:
                                    test_value = tag_info['node'].get_value()
                                    test_successful = True
                                    break  # Success, stop trying other tags
                                except:
                                    continue  # Try next tag

                        if test_successful:
                            is_still_connected = True
                            # Reset failure counter on success
                            connection_info['failure_count'] = 0
                        else:
                            raise Exception("No tags could be read")

                    except Exception as e:
                        logger.warning(f"Machine {machine_id} connection test failed: {e}")
                        failure_count = connection_info.get('failure_count', 0) + 1
                        connection_info['failure_count'] = failure_count
                        if failure_count >= FAILURE_THRESHOLD:
                            is_still_connected = False
                            # Mark the connection as dead after repeated failures
                            connection_info['connected'] = False
                        else:
                            # Keep connection alive until threshold reached
                            is_still_connected = True
                
                # Update machine status based on actual connection test
                if is_still_connected:
                    # Connection is good - update with fresh data
                    basic_data = get_machine_current_data(machine_id)
                    operational_data = get_machine_operational_data(machine_id)
                    
                    machine["serial"] = basic_data["serial"]
                    machine["status"] = basic_data["status"]  # This should be "GOOD" for connected machines
                    machine["model"] = basic_data["model"]
                    machine["last_update"] = basic_data["last_update"]
                    machine["operational_data"] = operational_data
                    
                    # IMPORTANT: Ensure status is set to something that indicates connection
                    if machine["status"] in ["Unknown", "Offline", "Connection Lost", "Connection Error"]:
                        machine["status"] = "GOOD"  # Force good status for connected machines
                    
                    updated = True
                    
                else:
                    # Connection is dead - update status to reflect this
                    machine["status"] = "Connection Lost"
                    machine["last_update"] = "Connection Lost"
                    machine["operational_data"] = None
                    updated = True
                    
                    # Clean up the dead connection
                    try:
                        if connection_info.get('client'):
                            connection_info['client'].disconnect()
                    except:
                        pass  # Ignore errors when disconnecting dead connection
                    
                    # Remove from connections
                    del machine_connections[machine_id]
                    logger.info(f"Removed dead connection for machine {machine_id}")
                    
            except Exception as e:
                logger.error(f"Error monitoring machine {machine_id}: {e}")
                # Mark machine as having connection error
                machine["status"] = "Connection Error"
                machine["last_update"] = "Error"
                machine["operational_data"] = None
                updated = True
                
                # Clean up the problematic connection
                if machine_id in machine_connections:
                    try:
                        if machine_connections[machine_id].get('client'):
                            machine_connections[machine_id]['client'].disconnect()
                    except:
                        pass
                    del machine_connections[machine_id]
    
    if updated:
        machines_data["machines"] = machines
        return machines_data
    
    return dash.no_update



# Callback to update the saved IP list display
@app.callback(
    Output("saved-ip-list", "children"),
    [Input("ip-addresses-store", "data")]
)
def update_saved_ip_list(ip_data):
    """Update the list of saved IPs displayed in settings"""
    if not ip_data or "addresses" not in ip_data or not ip_data["addresses"]:
        return html.Div("No IP addresses saved", className="text-muted fst-italic")
    
    # Create a list item for each saved IP
    ip_items = []
    for item in ip_data["addresses"]:
        ip = item["ip"]
        label = item["label"]
        # Display format for the list: "Label: IP"
        display_text = f"{label}: {ip}"
        
        ip_items.append(
            dbc.Row([
                dbc.Col(display_text, width=9),
                dbc.Col(
                    dbc.Button(
                        "×", 
                        id={"type": "delete-ip-button", "index": ip},  # Still use IP as index for deletion
                        color="danger",
                        size="sm",
                        className="py-0 px-2"
                    ),
                    width=3,
                    className="text-end"
                )
            ], className="mb-2 border-bottom pb-2")
        )
    
    return html.Div(ip_items)

@app.callback(
    [Output("current-dashboard", "data", allow_duplicate=True),
     Output("active-machine-store", "data"),
     Output("app-state", "data", allow_duplicate=True)],
    [Input({"type": "machine-card-click", "index": ALL}, "n_clicks")],
    [State("machines-data", "data"),
     State("active-machine-store", "data"),
     State("app-state", "data"),
     State({"type": "machine-card-click", "index": ALL}, "id")],
    prevent_initial_call=True
)
def handle_machine_selection(card_clicks, machines_data, active_machine_data, app_state_data, card_ids):
    """Handle machine card clicks and switch to main dashboard"""
    global active_machine_id, machine_connections, app_state
    
    ctx = callback_context
    if not ctx.triggered:
        return dash.no_update, dash.no_update, dash.no_update
    
    # Find which card was clicked
    triggered_prop = ctx.triggered[0]["prop_id"]
    machine_id = None
    
    if '"type":"machine-card-click"' in triggered_prop:
        for i, clicks in enumerate(card_clicks):
            if clicks and i < len(card_ids):
                machine_id = card_ids[i]["index"]
                break
    
    if machine_id is None:
        return dash.no_update, dash.no_update, dash.no_update
    
    # Set this machine as the active machine
    active_machine_id = machine_id
    logger.info(f"Selected machine {machine_id} as active machine")
    
    # Check if the machine is connected
    if machine_id in machine_connections and machine_connections[machine_id].get('connected', False):
        # Machine is connected - set up app_state to point to this machine's data
        connection_info = machine_connections[machine_id]
        
        app_state.client = connection_info['client']
        app_state.tags = connection_info['tags']
        app_state.connected = True
        app_state.last_update_time = connection_info.get('last_update', datetime.now())
        
        # Start/restart the update thread if not running
        if app_state.update_thread is None or not app_state.update_thread.is_alive():
            app_state.thread_stop_flag = False
            app_state.update_thread = Thread(target=opc_update_thread)
            app_state.update_thread.daemon = True
            app_state.update_thread.start()
            logger.info("Started OPC update thread for active machine")
        
        logger.info(f"Switched to connected machine {machine_id} - {len(app_state.tags)} tags available")
        app_state_data["connected"] = True
        
    else:
        # Machine not connected
        app_state.client = None
        app_state.tags = {}
        app_state.connected = False
        app_state.last_update_time = None
        
        logger.info(f"Switched to disconnected machine {machine_id}")
        app_state_data["connected"] = False
    
    # Return to main dashboard with selected machine
    return "main", {"machine_id": machine_id}, app_state_data

@app.callback(
    Output("machines-data", "data", allow_duplicate=True),
    [Input({"type": "machine-connect-btn", "index": ALL}, "n_clicks")],
    [State("machines-data", "data"),
     State({"type": "machine-ip-dropdown", "index": ALL}, "value"),
     State({"type": "machine-connect-btn", "index": ALL}, "id"),
     State("server-name-input", "value")],
    prevent_initial_call=True
)
def handle_machine_connect_disconnect(n_clicks_list, machines_data, ip_values, button_ids, server_name):
    """Handle connect/disconnect - separate from updates like main dashboard"""
    
    if not any(n_clicks_list) or not button_ids:
        return dash.no_update
    
    # Find which button was clicked
    triggered_idx = None
    for i, clicks in enumerate(n_clicks_list):
        if clicks is not None and clicks > 0:
            triggered_idx = i
            break
    
    if triggered_idx is None:
        return dash.no_update
    
    machine_id = button_ids[triggered_idx]["index"]
    selected_ip = ip_values[triggered_idx] if triggered_idx < len(ip_values) else None
    
    if not selected_ip:
        return dash.no_update
    
    machines = machines_data.get("machines", [])
    is_connected = machine_id in machine_connections and machine_connections[machine_id]['connected']
    
    if is_connected:
        # DISCONNECT
        try:
            if machine_id in machine_connections:
                machine_connections[machine_id]['client'].disconnect()
                del machine_connections[machine_id]
                logger.info(f"Disconnected machine {machine_id}")
            
            for machine in machines:
                if machine["id"] == machine_id:
                    machine["status"] = "Offline"
                    machine["last_update"] = "Disconnected"
                    machine["operational_data"] = None
                    break
                    
        except Exception as e:
            logger.error(f"Error disconnecting machine {machine_id}: {e}")
    
    else:
        # CONNECT
        try:
            connection_success = run_async(connect_and_monitor_machine(selected_ip, machine_id, server_name))
            
            if connection_success:
                machine_data = get_machine_current_data(machine_id)
                operational_data = get_machine_operational_data(machine_id)
                
                for machine in machines:
                    if machine["id"] == machine_id:
                        machine["ip"] = selected_ip
                        machine["selected_ip"] = selected_ip
                        machine["serial"] = machine_data["serial"]
                        machine["status"] = machine_data["status"]
                        machine["model"] = machine_data["model"]
                        machine["last_update"] = machine_data["last_update"]
                        machine["operational_data"] = operational_data
                        break
                        
                logger.info(f"Successfully connected machine {machine_id}")
                
                # IMPORTANT: Start the update thread if it's not running
                if app_state.update_thread is None or not app_state.update_thread.is_alive():
                    app_state.thread_stop_flag = False
                    app_state.update_thread = Thread(target=opc_update_thread)
                    app_state.update_thread.daemon = True
                    app_state.update_thread.start()
                    logger.info("Started OPC update thread for all machines")
                
            else:
                logger.error(f"Failed to connect machine {machine_id}")
                
        except Exception as e:
            logger.error(f"Error connecting machine {machine_id}: {e}")
    
    machines_data["machines"] = machines
    return machines_data

@app.callback(
    Output("delete-ip-trigger", "data"),
    [Input({"type": "delete-ip-button", "index": ALL}, "n_clicks")],
    [State({"type": "delete-ip-button", "index": ALL}, "id")],
    prevent_initial_call=True
)
def handle_delete_button(n_clicks_list, button_ids):
    """Capture which delete button was clicked"""
    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update
    
    # Get which button was clicked by finding the button with a non-None click value
    triggered_idx = None
    for i, clicks in enumerate(n_clicks_list):
        if clicks is not None:
            triggered_idx = i
            break
    
    if triggered_idx is None:
        return dash.no_update
    
    # Get the corresponding button id
    button_id = button_ids[triggered_idx]
    ip_to_delete = button_id["index"]  # This is already a dictionary, no need for json.loads
    
    # Return the IP to delete
    return {"ip": ip_to_delete, "timestamp": time.time()}

@app.callback(
    [Output("ip-addresses-store", "data", allow_duplicate=True),
     Output("delete-result", "children")],
    [Input("delete-ip-trigger", "data")],
    [State("ip-addresses-store", "data")],
    prevent_initial_call=True
)
def delete_ip_address(trigger_data, current_data):
    """Delete an IP address from the stored list"""
    if not trigger_data or "ip" not in trigger_data:
        return dash.no_update, dash.no_update
    
    ip_to_delete = trigger_data["ip"]
    
    # Get current addresses
    addresses = current_data.get("addresses", []) if current_data else []
    
    # Find the item to delete by IP
    found = False
    for i, item in enumerate(addresses):
        if item["ip"] == ip_to_delete:
            # Get the label for the message
            label = item["label"]
            # Remove the item
            addresses.pop(i)
            message = f"Deleted {label} ({ip_to_delete})"
            found = True
            break
    
    if not found:
        message = "IP address not found"
    
    # Return updated data
    return {"addresses": addresses}, message



@app.callback(
    Output("theme-selector", "value"),
    [Input("auto-connect-trigger", "data")],
    prevent_initial_call=False
)
def load_initial_theme(trigger):
    """Load theme preference from file on startup"""
    theme = load_theme_preference()
    logger.info(f"Loading initial theme: {theme}")
    return theme

# Callback 2: Save theme when user changes it
@app.callback(
    Output("theme-save-status", "children"),  # Create a hidden div for this output
    [Input("theme-selector", "value")],
    prevent_initial_call=True
)
def save_theme_on_change(theme_value):
    """Save theme preference when user changes it"""
    if theme_value:
        save_theme_preference(theme_value)
        logger.info(f"Saved theme preference: {theme_value}")
    return ""  # Return empty string since this is just for the side effect


@app.callback(
    [Output("capacity-units-selector", "value"),
     Output("custom-unit-name", "value"),
     Output("custom-unit-weight", "value")],
    [Input("auto-connect-trigger", "data")],
    prevent_initial_call=False,
)
def load_initial_capacity_units(trigger):
    pref = load_weight_preference()
    return pref.get("unit", "lb"), pref.get("label", ""), pref.get("value", 1.0)


@app.callback(
    [Output("custom-unit-name", "style"),
     Output("custom-unit-weight", "style")],
    [Input("capacity-units-selector", "value")],
    prevent_initial_call=False,
)
def toggle_custom_unit_fields(unit_value):
    if unit_value == "custom":
        return {"display": "block"}, {"display": "block"}
    return {"display": "none"}, {"display": "none"}


@app.callback(
    Output("weight-preference-store", "data"),
    [Input("capacity-units-selector", "value"),
     Input("custom-unit-name", "value"),
     Input("custom-unit-weight", "value")],
    prevent_initial_call=True,
)
def save_capacity_units(unit_value, custom_name, custom_weight):
    if unit_value != "custom":
        save_weight_preference(unit_value, "", 1.0)
        return {"unit": unit_value, "label": "", "value": 1.0}
    if custom_name and custom_weight:
        save_weight_preference("custom", custom_name, float(custom_weight))
        return {"unit": "custom", "label": custom_name, "value": float(custom_weight)}
    # If custom selected but fields incomplete, don't update
    return dash.no_update


@app.callback(
    Output("language-selector", "value"),
    [Input("auto-connect-trigger", "data")],
    prevent_initial_call=False,
)
def load_initial_language(trigger):
    return load_language_preference()


@app.callback(
    Output("language-preference-store", "data"),
    [Input("language-selector", "value")],
    prevent_initial_call=True,
)
def save_language(value):
    if value:
        save_language_preference(value)
        return value
    return dash.no_update



@app.callback(
    Output("dashboard-title", "children"),
    [Input("active-machine-store", "data"),
     Input("current-dashboard", "data"),
     Input("language-preference-store", "data")],
    [State("machines-data", "data")],
    prevent_initial_call=True
)
def update_dashboard_title(active_machine_data, current_dashboard, lang, machines_data):
    """Update dashboard title to show active machine"""
    base_title = tr("dashboard_title", lang)
    
    if current_dashboard == "main" and active_machine_data and active_machine_data.get("machine_id"):
        machine_id = active_machine_data["machine_id"]
        
        # Find machine details
        machine_name = f"{tr('machine_label', lang)} {machine_id}"
        if machines_data and machines_data.get("machines"):
            for machine in machines_data["machines"]:
                if machine["id"] == machine_id:
                    serial = machine.get("serial", "Unknown")
                    if serial != "Unknown":
                        machine_name = f"{tr('machine_label', lang)} {machine_id} (S/N: {serial})"
                    break
        
        return f"{base_title} - {machine_name}"
    
    return base_title



@app.callback(
    [Output("threshold-modal-header", "children"),
     Output("display-modal-header", "children"),
     Output("display-modal-description", "children"),
     Output("close-threshold-settings", "children"),
     Output("save-threshold-settings", "children"),
     Output("close-display-settings", "children"),
     Output("save-display-settings", "children"),
     Output("production-rate-units-header", "children"),
     Output("close-production-rate-units", "children"),
     Output("save-production-rate-units", "children"),
     Output("settings-modal-header", "children"),
     Output("update-counts-header", "children"),
     Output("close-update-counts", "children"),
     Output("upload-modal-header", "children"),
     Output("close-upload-modal", "children"),
     Output("delete-confirmation-header", "children"),
     Output("delete-warning", "children"),
     Output("cancel-delete-btn", "children"),
     Output("confirm-delete-btn", "children"),
     Output("close-settings", "children"),
     Output("add-floor-btn", "children"),
    Output("export-data-button", "children"),
    Output("new-dashboard-btn", "children"),
     Output("color-theme-label", "children"),
    Output("theme-selector", "options"),
    Output("capacity-units-label", "children"),
    Output("language-label", "children"),
    Output("language-selector", "options"),
    Output("mode-selector", "options"),
    Output("system-configuration-title", "children"),
     Output("auto-connect-label", "children"),
     Output("add-machine-ip-label", "children"),
     Output("smtp-email-configuration-title", "children"),
     Output("smtp-server-label", "children"),
     Output("smtp-port-label", "children"),
     Output("smtp-username-label", "children"),
     Output("smtp-password-label", "children"),
     Output("smtp-from-label", "children"),
     Output("save-email-settings", "children"),
     Output("production-rate-unit-selector", "options")],
    [Input("language-preference-store", "data")]
)
def refresh_text(lang):
    return (
        tr("threshold_settings_title", lang),
        tr("display_settings_title", lang),
        tr("display_settings_header", lang),
        tr("close", lang),
        tr("save_changes", lang),
        tr("close", lang),
        tr("save_changes", lang),
        tr("production_rate_units_title", lang),
        tr("close", lang),
        tr("save", lang),
        tr("system_settings_title", lang),
        tr("update_counts_title", lang),
        tr("close", lang),
        tr("upload_image_title", lang),
        tr("close", lang),
        tr("confirm_deletion_title", lang),
        tr("delete_warning", lang),
        tr("cancel", lang),
        tr("yes_delete", lang),
        tr("close", lang),
        tr("add_floor", lang),
        tr("export_data", lang),
        tr("switch_dashboards", lang),
        tr("color_theme_label", lang),
        [
            {"label": tr("light_mode_option", lang), "value": "light"},
            {"label": tr("dark_mode_option", lang), "value": "dark"},
        ],
        tr("capacity_units_label", lang),
        tr("language_label", lang),
        [
            {"label": tr("english_option", lang), "value": "en"},
            {"label": tr("spanish_option", lang), "value": "es"},
            {"label": tr("japanese_option", lang), "value": "ja"},
        ],
        [
            {"label": tr("live_mode_option", lang), "value": "live"},
            {"label": tr("demo_mode_option", lang), "value": "demo"},
            {"label": tr("historical_mode_option", lang), "value": "historical"},
        ],
        tr("system_configuration_title", lang),
        tr("auto_connect_label", lang),
        tr("add_machine_ip_label", lang),
        tr("smtp_email_configuration_title", lang),
        tr("smtp_server_label", lang),
        tr("port_label", lang),
        tr("username_label", lang),
        tr("password_label", lang),
        tr("from_address_label", lang),
        tr("save_email_settings", lang),
        [
            {"label": tr("objects_per_min", lang), "value": "objects"},
            {"label": tr("capacity", lang), "value": "capacity"},
        ],
    )

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
    MACHINE_IMAGE_BASE64 = "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"  # Replace with actual base64

    left_sidebar_buttons.append(
        html.Div([
            html.Img(
                src=f"data:image/png;base64,{MACHINE_IMAGE_BASE64}",
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
                dbc.Button("Add Machine", id="add-machine-btn", color="success", size="sm", className="mt-2")
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
                dbc.Button("Add Machine", id="add-machine-btn", color="success", size="sm", className="mt-1")
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

@app.callback(
    Output("hidden-machines-cache", "data"),
    [Input("machines-data", "data")],
    prevent_initial_call=True
)
def cache_machines_data(machines_data):
    """Cache machines data for auto-reconnection thread"""
    if machines_data:
        app_state.machines_data_cache = machines_data
        logger.debug(f"Cached machines data: {len(machines_data.get('machines', []))} machines")
    return machines_data

# Start auto-reconnection thread when app starts
def start_auto_reconnection():
    """Start the auto-reconnection thread"""
    if not hasattr(app_state, 'reconnection_thread') or not app_state.reconnection_thread.is_alive():
        app_state.reconnection_thread = Thread(target=auto_reconnection_thread)
        app_state.reconnection_thread.daemon = True
        app_state.reconnection_thread.start()
        logger.info("Started auto-reconnection thread")


@app.callback(
    Output("floor-machine-container", "children"),
    [Input("machines-data", "data"),
     Input("floors-data", "data"),
     Input("ip-addresses-store", "data"),
     Input("additional-image-store", "data"),
     Input("current-dashboard", "data"),
     Input("active-machine-store", "data"),
     Input("app-mode", "data"),
     Input("language-preference-store", "data")],
    prevent_initial_call=True
)
def render_floor_machine_layout_enhanced_with_selection(machines_data, floors_data, ip_addresses_data, additional_image_data, current_dashboard, active_machine_data, app_mode_data, lang):
    """Enhanced render with machine selection capability"""
    
    # CRITICAL: Only render on machine dashboard
    if current_dashboard != "new":
        raise PreventUpdate
    
    # ADD THIS CHECK: Prevent re-render if only machine status/operational data changed
    ctx = callback_context
    if ctx.triggered:
        trigger_id = ctx.triggered[0]["prop_id"]
        if "machines-data" in trigger_id:
            # Check if any floor is currently being edited
            if floors_data and floors_data.get("floors"):
                for floor in floors_data["floors"]:
                    if floor.get("editing", False):
                        # A floor is being edited, don't re-render
                        return dash.no_update
    
    # Rest of the function continues as normal...
    active_machine_id = active_machine_data.get("machine_id") if active_machine_data else None
    
    return render_floor_machine_layout_with_customizable_names(
        machines_data,
        floors_data,
        ip_addresses_data,
        additional_image_data,
        current_dashboard,
        active_machine_id,
        app_mode_data,
        lang,
    )




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
@app.callback(
    [Output("floors-data", "data", allow_duplicate=True),
     Output("machines-data", "data", allow_duplicate=True),
     Output("delete-confirmation-modal", "is_open", allow_duplicate=True)],
    [Input("confirm-delete-btn", "n_clicks")],
    [State("delete-pending-store", "data"),
     State("floors-data", "data"),
     State("machines-data", "data")],
    prevent_initial_call=True
)
def execute_confirmed_deletion(confirm_clicks, pending_delete, floors_data, machines_data):
    """Execute the deletion after user confirms"""
    global machine_connections
    
    if not confirm_clicks or not pending_delete or pending_delete.get("type") is None:
        return dash.no_update, dash.no_update, dash.no_update
    
    delete_type = pending_delete.get("type")
    delete_id = pending_delete.get("id")
    
    if delete_type == "floor":
        # Execute floor deletion (your existing floor deletion logic)
        floors = floors_data.get("floors", [])
        machines = machines_data.get("machines", [])
        
        # Find the floor to delete
        floor_found = False
        floor_name = None
        updated_floors = []
        
        for floor in floors:
            if floor["id"] == delete_id:
                floor_found = True
                floor_name = floor.get("name", f"Floor {delete_id}")
                logger.info(f"Deleting floor: {floor_name}")
            else:
                updated_floors.append(floor)
        
        if not floor_found:
            logger.warning(f"Floor {delete_id} not found for deletion")
            return dash.no_update, dash.no_update, False
        
        # Find machines on this floor and disconnect them
        machines_on_floor = [m for m in machines if m.get("floor_id") == delete_id]
        machines_to_keep = [m for m in machines if m.get("floor_id") != delete_id]
        
        # Disconnect machines on this floor
        for machine in machines_on_floor:
            machine_id = machine["id"]
            try:
                if machine_id in machine_connections:
                    if machine_connections[machine_id].get('connected', False):
                        client = machine_connections[machine_id].get('client')
                        if client:
                            client.disconnect()
                        logger.info(f"Disconnected machine {machine_id} before floor deletion")
                    del machine_connections[machine_id]
                    logger.info(f"Removed machine {machine_id} from connections")
            except Exception as e:
                logger.error(f"Error disconnecting machine {machine_id} during floor deletion: {e}")
        
        # Update data structures
        floors_data["floors"] = updated_floors
        machines_data["machines"] = machines_to_keep
        
        # Update selected floor if needed
        if floors_data.get("selected_floor") == delete_id:
            floors_data["selected_floor"] = "all" if updated_floors else 1
            logger.info(f"Changed selected floor to {floors_data['selected_floor']} after deletion")
        
        # Auto-save
        try:
            save_success = save_floor_machine_data(floors_data, machines_data)
            if save_success:
                logger.info(f"Successfully deleted floor '{floor_name}' with {len(machines_on_floor)} machines and saved layout")
            else:
                logger.warning(f"Floor '{floor_name}' deleted but layout save failed")
        except Exception as e:
            logger.error(f"Error saving layout after deleting floor '{floor_name}': {e}")
        
        return floors_data, machines_data, False
        
    elif delete_type == "machine":
        # Execute machine deletion (your existing machine deletion logic)
        machines = machines_data.get("machines", [])
        
        # Find and remove the machine
        machine_found = False
        updated_machines = []
        
        for machine in machines:
            if machine["id"] == delete_id:
                machine_found = True
                
                # Disconnect the machine if connected
                try:
                    if delete_id in machine_connections:
                        if machine_connections[delete_id].get('connected', False):
                            client = machine_connections[delete_id].get('client')
                            if client:
                                client.disconnect()
                            logger.info(f"Disconnected machine {delete_id} before deletion")
                        del machine_connections[delete_id]
                        logger.info(f"Removed machine {delete_id} from connections")
                except Exception as e:
                    logger.error(f"Error disconnecting machine {delete_id}: {e}")
                
                logger.info(f"Deleted machine {delete_id}: {machine.get('name', 'Unknown')}")
            else:
                updated_machines.append(machine)
        
        if not machine_found:
            logger.warning(f"Machine {delete_id} not found for deletion")
            return dash.no_update, dash.no_update, False
        
        # Update machines data
        machines_data["machines"] = updated_machines
        
        # Auto-save
        try:
            save_success = save_floor_machine_data(floors_data, machines_data)
            if save_success:
                logger.info(f"Successfully deleted machine {delete_id} and saved layout")
            else:
                logger.warning(f"Machine {delete_id} deleted but layout save failed")
        except Exception as e:
            logger.error(f"Error saving layout after deleting machine {delete_id}: {e}")
        
        return dash.no_update, machines_data, False
    
    return dash.no_update, dash.no_update, False



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
    
    if REJECTS_TAG in tags:
        reject_percentage_value = tags[REJECTS_TAG]["data"].latest_value
        if reject_percentage_value is not None:
            reject_percentage = reject_percentage_value
            logger.info(f"DEBUG: Reject %: {reject_percentage}")
    
    # Get current diagnostic counter
    diagnostic_counter = 0
    if DIAGNOSTIC_COUNTER_TAG in tags:
        diagnostic_value = tags[DIAGNOSTIC_COUNTER_TAG]["data"].latest_value
        if diagnostic_value is not None:
            diagnostic_counter = diagnostic_value
            logger.info(f"DEBUG: Diagnostic counter: {diagnostic_counter}")
    
    # Calculate production values
    rejects = (reject_percentage / 100.0) * total_capacity if total_capacity > 0 else 0
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
@app.callback(
    Output("floors-data", "data", allow_duplicate=True),
    [Input({"type": "edit-floor-name-btn", "index": ALL}, "n_clicks"),
     Input({"type": "save-floor-name-btn", "index": ALL}, "n_clicks"),
     Input({"type": "cancel-floor-name-btn", "index": ALL}, "n_clicks")],
    [State({"type": "floor-name-input", "index": ALL}, "value"),
     State({"type": "edit-floor-name-btn", "index": ALL}, "id"),
     State({"type": "save-floor-name-btn", "index": ALL}, "id"),
     State({"type": "cancel-floor-name-btn", "index": ALL}, "id"),
     State("floors-data", "data")],  # REMOVED machines-data from here
    prevent_initial_call=True
)
def handle_floor_name_editing(edit_clicks, save_clicks, cancel_clicks, input_values, 
                             edit_ids, save_ids, cancel_ids, floors_data):  # REMOVED machines_data parameter
    """Handle floor name editing with auto-save"""
    ctx = callback_context
    if not ctx.triggered:
        return dash.no_update

    trigger_prop = ctx.triggered[0]["prop_id"]
    
    # Parse which button was clicked and which floor
    if '"type":"save-floor-name-btn"' in trigger_prop:
        # Find which save button was clicked
        for i, clicks in enumerate(save_clicks or []):
            if clicks and i < len(save_ids):
                floor_id = save_ids[i]["index"]
                new_name = input_values[i] if i < len(input_values or []) else None
                
                if new_name and new_name.strip():
                    # Update the floor name
                    floors = floors_data.get("floors", [])
                    for floor in floors:
                        if floor["id"] == floor_id:
                            floor["name"] = new_name.strip()
                            floor["editing"] = False
                            break
                    
                    floors_data["floors"] = floors
                    
                    # Auto-save the layout (get machines_data fresh)
                    _, machines_data = load_floor_machine_data()
                    if machines_data is None:
                        machines_data = {"machines": [], "next_machine_id": 1}
                    save_floor_machine_data(floors_data, machines_data)
                    logger.info(f"Floor {floor_id} renamed to '{new_name.strip()}' and saved")
                    
                    return floors_data
                break
    
    elif '"type":"edit-floor-name-btn"' in trigger_prop:
        # Find which edit button was clicked
        for i, clicks in enumerate(edit_clicks or []):
            if clicks and i < len(edit_ids):
                floor_id = edit_ids[i]["index"]
                
                # Set editing mode for this floor
                floors = floors_data.get("floors", [])
                for floor in floors:
                    if floor["id"] == floor_id:
                        floor["editing"] = True
                        break
                
                floors_data["floors"] = floors
                return floors_data
                break
    
    elif '"type":"cancel-floor-name-btn"' in trigger_prop:
        # Find which cancel button was clicked
        for i, clicks in enumerate(cancel_clicks or []):
            if clicks and i < len(cancel_ids):
                floor_id = cancel_ids[i]["index"]
                
                # Cancel editing mode for this floor
                floors = floors_data.get("floors", [])
                for floor in floors:
                    if floor["id"] == floor_id:
                        floor["editing"] = False
                        break
                
                floors_data["floors"] = floors
                return floors_data
                break
    
    return dash.no_update

# Enhanced callback for adding floors with auto-save
@app.callback(
    Output("floors-data", "data", allow_duplicate=True),
    [Input("add-floor-btn", "n_clicks")],
    [State("floors-data", "data"),
     State("machines-data", "data")],
    prevent_initial_call=True
)
def add_new_floor_with_save(n_clicks, floors_data, machines_data):
    """Add a new floor with auto-save"""
    if not n_clicks:
        return dash.no_update
    
    floors = floors_data.get("floors", [])
    next_floor_number = len(floors) + 1
    
    # Ordinal suffixes
    def get_ordinal_suffix(n):
        if 10 <= n % 100 <= 20:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
        return f"{n}{suffix}"
    
    new_floor = {
        "id": next_floor_number,
        "name": f"{get_ordinal_suffix(next_floor_number)} Floor",
        "editing": False
    }
    
    floors.append(new_floor)
    floors_data["floors"] = floors
    
    # Auto-save the layout
    save_floor_machine_data(floors_data, machines_data)
    logger.info(f"Added new floor: {new_floor['name']} and saved layout")
    
    return floors_data  

@app.callback(
    Output("save-status", "children"),
    [Input("add-floor-btn", "n_clicks"),
     Input({"type": "save-floor-name-btn", "index": ALL}, "n_clicks"),
     Input({"type": "delete-floor-btn", "index": ALL}, "n_clicks")],
    prevent_initial_call=True
)
def show_floor_save_status(add_clicks, save_clicks, delete_clicks):
    """Show save status only when floors are actually modified"""
    if add_clicks or any(save_clicks or []) or any(delete_clicks or []):
        current_time = datetime.now().strftime("%H:%M:%S")
        return f"✓ Saved at {current_time}"
    return ""

@app.callback(
    Output("save-status", "children", allow_duplicate=True),
    [Input("add-machine-btn", "n_clicks"),
     Input({"type": "machine-ip-dropdown", "index": ALL}, "value")],
    prevent_initial_call=True
)
def show_machine_save_status(add_single, ip_values):  # Removed add_multiple parameter
    """Show save status only when machines are added or IP changed"""
    ctx = callback_context
    if not ctx.triggered:
        return ""
    
    trigger_id = ctx.triggered[0]["prop_id"]
    
    # Only show save status for actual button clicks or IP changes
    if "add-machine-btn" in trigger_id or "machine-ip-dropdown" in trigger_id:
        current_time = datetime.now().strftime("%H:%M:%S")
        return f"✓ Saved at {current_time}"
    return ""

@app.callback(
    Output("save-status", "children", allow_duplicate=True),
    [Input("confirm-delete-btn", "n_clicks")],
    prevent_initial_call=True
)
def show_delete_save_status(confirm_clicks):
    """Show save status only when items are actually deleted"""
    if confirm_clicks:
        current_time = datetime.now().strftime("%H:%M:%S")
        return f"✓ Saved at {current_time}"
    return ""

# Add this callback for manual save button (optional)
@app.callback(
    Output("save-status", "children", allow_duplicate=True),
    [Input("manual-save-btn", "n_clicks")],
    [State("floors-data", "data"),
     State("machines-data", "data")],
    prevent_initial_call=True
)
def manual_save_layout(n_clicks, floors_data, machines_data):
    """Manual save button callback"""
    if not n_clicks:
        return dash.no_update
    
    success = save_floor_machine_data(floors_data, machines_data)
    current_time = datetime.now().strftime("%H:%M:%S")
    
    if success:
        return f"✓ Manually saved at {current_time}"
    else:
        return f"✗ Save failed at {current_time}"

# Enhanced callback for adding machines with auto-save
@app.callback(
    Output("machines-data", "data", allow_duplicate=True),
    [Input("add-machine-btn", "n_clicks")],
    [State("machines-data", "data"),
     State("floors-data", "data")],
    prevent_initial_call=True
)
def add_new_machine_with_save(n_clicks, machines_data, floors_data):
    """Add a new blank machine to the selected floor with auto-save"""
    if not n_clicks:
        return dash.no_update
    
    machines = machines_data.get("machines", [])
    next_machine_id = get_next_available_machine_id(machines_data)  # Use helper function
    selected_floor_id = floors_data.get("selected_floor", "all")
    if selected_floor_id == "all":
        floors = floors_data.get("floors", [])
        selected_floor_id = floors[0]["id"] if floors else 1
    
    new_machine = {
        "id": next_machine_id,
        "floor_id": selected_floor_id,
        "name": f"{tr('machine_label', load_language_preference())} {next_machine_id}",
        "ip": None,
        "serial": "Unknown",
        "status": "Offline",
        "model": "Unknown",
        "last_update": "Never"
    }
    
    machines.append(new_machine)
    machines_data["machines"] = machines
    # Remove the next_machine_id update since we're using the helper function
    
    # Auto-save the layout
    save_floor_machine_data(floors_data, machines_data)
    logger.info(f"Added new machine {next_machine_id} to floor {selected_floor_id} and saved layout")
    
    return machines_data






# Callback to handle floor selection
@app.callback(
    Output("floors-data", "data", allow_duplicate=True),
    [Input({"type": "floor-tile", "index": ALL}, "n_clicks")],
    [State("floors-data", "data")],
    prevent_initial_call=True
)
def handle_floor_selection_dynamic(n_clicks_list, floors_data):
    """Handle floor tile selection dynamically"""
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_list):
        return dash.no_update
    
    # Find which floor was clicked
    triggered_prop = ctx.triggered[0]["prop_id"]
    
    # Extract floor ID from the triggered property
    if "floor-tile" in triggered_prop:
        import json
        import re
        
        # Extract the JSON part before .n_clicks
        json_match = re.search(r'\{[^}]+\}', triggered_prop)
        if json_match:
            try:
                button_id = json.loads(json_match.group())
                selected_floor_id = button_id["index"]
                
                # Update the selected floor
                floors_data["selected_floor"] = selected_floor_id
                return floors_data
            except (json.JSONDecodeError, KeyError):
                pass
    
    return dash.no_update



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
@app.callback(
    Output("machines-data", "data", allow_duplicate=True),
    [Input({"type": "machine-ip-dropdown", "index": ALL}, "value")],
    [State("machines-data", "data"),
     State("floors-data", "data"),
     State({"type": "machine-ip-dropdown", "index": ALL}, "id")],
    prevent_initial_call=True
)
def update_machine_selected_ip_with_save(ip_values, machines_data, floors_data, dropdown_ids):
    """Update the selected IP for each machine when dropdown changes with auto-save"""
    if not ip_values or not dropdown_ids:
        return dash.no_update
    
    machines = machines_data.get("machines", [])
    changes_made = False
    
    # Update selected IP for each machine
    for i, ip_value in enumerate(ip_values):
        if i < len(dropdown_ids) and ip_value:
            machine_id = dropdown_ids[i]["index"]
            
            # Find and update the machine
            for machine in machines:
                if machine["id"] == machine_id:
                    if machine.get("selected_ip") != ip_value:
                        machine["selected_ip"] = ip_value
                        changes_made = True
                        logger.info(f"Updated machine {machine_id} IP selection to {ip_value}")
                    break
    
    if changes_made:
        machines_data["machines"] = machines
        
        # Auto-save the layout
        save_floor_machine_data(floors_data, machines_data)
        logger.info("Machine IP selections saved")
        
        return machines_data
    
    return dash.no_update
    


@app.callback(
    [
        Output("section-1-1", "children"),
        Output("production-data-store", "data"),
    ],


    [
        Input("status-update-interval", "n_intervals"),
        Input("current-dashboard", "data"),
        Input("historical-time-index", "data"),
        Input("historical-data-cache", "data"),
    ],
    [
        State("app-state", "data"),
        State("app-mode", "data"),
        State("production-data-store", "data"),
        State("weight-preference-store", "data"),
        State("language-preference-store", "data"),
    ],


    prevent_initial_call=True
)



def update_section_1_1(n, which, state_data, historical_data, app_state_data, app_mode, production_data, weight_pref, lang):

    """Update section 1-1 with capacity information and update shared production data"""

    # only run when we’re in the “main” dashboard
    if which != "main":
        #print("DEBUG: Preventing update for section-1-1")
        raise PreventUpdate

    global previous_counter_values
    

    # Tag definitions - Easy to update when actual tag names are available
    CAPACITY_TAG = "Status.ColorSort.Sort1.Throughput.KgPerHour.Current"
    ACCEPTS_TAG = "Status.Production.Accepts"  # Not used in live mode calculation
    REJECTS_TAG = "Status.ColorSort.Sort1.Total.Percentage.Current"

    # Determine if we're in Live or Demo mode
    mode = "demo"  # Default to demo mode
    if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
        mode = app_mode["mode"]

    # Only update values if:
    # 1. We're in demo mode (always update with new random values)
    # 2. We're in live mode and connected (update from tags)
    if mode == "live" and app_state_data.get("connected", False):
        # Live mode: get values from OPC UA tags
        total_capacity = 0

        # Get total capacity first
        if CAPACITY_TAG in app_state.tags:
            capacity_value = app_state.tags[CAPACITY_TAG]["data"].latest_value
            if capacity_value is not None:
                total_capacity = convert_capacity_from_kg(capacity_value, weight_pref)
            else:
                total_capacity = 0

        # Rejects come from section 5-2 counter totals
        reject_count = sum(previous_counter_values) if previous_counter_values else 0
        rejects = convert_capacity_from_kg(reject_count * 46, weight_pref)

        # Calculate accepts as total_capacity minus rejects
        accepts = total_capacity - rejects
        
        # Ensure accepts doesn't go negative (safety check)
        if accepts < 0:
            accepts = 0
        
        # Update the shared data store
        production_data = {
            "capacity": total_capacity,
            "accepts": accepts,
            "rejects": rejects
        }
        


    elif mode == "historical":
        hours = state_data.get("hours", 24) if isinstance(state_data, dict) else 24
        hist = (
            historical_data if isinstance(historical_data, dict) and "capacity" in historical_data
            else get_historical_data(timeframe=f"{hours}h")
        )
        cap_vals = hist.get("capacity", {}).get("values", [])
        acc_vals = hist.get("accepts", {}).get("values", [])
        rej_vals = hist.get("rejects", {}).get("values", [])

        total_capacity_lbs = sum(cap_vals) / len(cap_vals) if cap_vals else 0
        total_capacity = convert_capacity_from_lbs(total_capacity_lbs, weight_pref)

        reject_count = sum(previous_counter_values) if previous_counter_values else 0
        rejects = convert_capacity_from_kg(reject_count * 46, weight_pref)

        accepts = total_capacity - rejects
        if accepts < 0:
            accepts = 0

        production_data = {
            "capacity": total_capacity,
            "accepts": accepts,
            "rejects": rejects,
        }

    elif mode == "demo":

        # Demo mode: generate realistic random capacity value
        demo_lbs = random.uniform(47000, 53000)
        total_capacity = convert_capacity_from_kg(demo_lbs / 2.205, weight_pref)

        # Rejects come from section 5-2 counter totals
        reject_count = sum(previous_counter_values) if previous_counter_values else 0
        rejects = convert_capacity_from_kg(reject_count * 46, weight_pref)

        # Calculate accepts as the difference
        accepts = total_capacity - rejects

        # Update the shared data store
        production_data = {
            "capacity": total_capacity,
            "accepts": accepts,
            "rejects": rejects
        }
    else:
        # If not live+connected or demo, use existing values from the store
        total_capacity = production_data.get("capacity", 50000)
        accepts = production_data.get("accepts", 47500)
        rejects = production_data.get("rejects", 2500)
    
    # Calculate percentages
    total = accepts + rejects
    accepts_percent = (accepts / total * 100) if total > 0 else 0
    rejects_percent = (rejects / total * 100) if total > 0 else 0
    
    # Format values with commas for thousands separator and limited decimal places
    total_capacity_formatted = f"{total_capacity:,.0f}"
    accepts_formatted = f"{accepts:,.0f}"
    rejects_formatted = f"{rejects:,.0f}"
    accepts_percent_formatted = f"{accepts_percent:.1f}"
    rejects_percent_formatted = f"{rejects_percent:.1f}"
    
    # Define styles for text

    base_style = {"fontSize": "1.6rem", "lineHeight": "1.6rem", "fontFamily": NUMERIC_FONT}
    label_style = {"fontWeight": "bold", "fontSize": "1.6rem"}
    incoming_style = {"color": "blue", "fontSize": "2.4rem", "fontFamily": NUMERIC_FONT}
    accepts_style = {"color": "green", "fontSize": "1.8rem", "fontFamily": NUMERIC_FONT}
    rejects_style = {"color": "red", "fontSize": "1.8rem", "fontFamily": NUMERIC_FONT}

    
    # Create the section content
    section_content = html.Div([
        # Title with mode indicator
        dbc.Row([
            dbc.Col(html.H6(tr("production_capacity_title", lang), className="text-left mb-2"), width=8),
            dbc.Col(
                dbc.Button(
                    tr("update_counts_title", lang),
                    id="open-update-counts",
                    color="primary",
                    size="sm",
                    className="float-end"
                ),
                width=4
            )
        ]),
        
        # Capacity data
        html.Div([
            html.Span(tr("capacity", lang) + ": ", style=label_style),
            html.Br(),
            html.Span(
                f"{total_capacity_formatted} {capacity_unit_label(weight_pref)}",
                style={**incoming_style, "marginLeft": "20px"},
            ),
        ], className="mb-2", style=base_style),
        
        html.Div([
            html.Span(tr("accepts", lang) + ": ", style=label_style),
            html.Br(),
            html.Span(
                f"{accepts_formatted} {capacity_unit_label(weight_pref, False)} ",
                style={**accepts_style,"marginLeft":"20px"},
            ),
            html.Span(f"({accepts_percent_formatted}%)", style=accepts_style),
        ], className="mb-2", style=base_style),
        
        html.Div([
            html.Span(tr("rejects", lang) + ": ", style=label_style),
            html.Br(),
            html.Span(
                f"{rejects_formatted} {capacity_unit_label(weight_pref, False)} ",
                style={**rejects_style,"marginLeft":"20px"},
            ),
            html.Span(f"({rejects_percent_formatted}%)", style=rejects_style),
        ], className="mb-2", style=base_style),
    ], className="p-1")
    
    return section_content, production_data

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

@app.callback(
    Output("update-counts-modal-body", "children"),
    [Input("status-update-interval", "n_intervals"),
     Input("current-dashboard",       "data"), 
     Input("opc-pause-state", "data")],
    [State("app-state", "data"),
     State("app-mode", "data"),
     State("user-inputs", "data"),
     State("language-preference-store", "data")],
    prevent_initial_call=True
)
def update_section_1_1b_with_manual_pause(n,which, pause_state, app_state_data, app_mode, user_inputs, lang):
    """Update section 1-1b with manual pause/resume system"""
    # only run when we’re in the “main” dashboard
    if which != "main":
        raise PreventUpdate
    
    # Tag definitions for live mode
    WEIGHT_TAG = "Settings.ColorSort.TestWeightValue"
    COUNT_TAG = "Settings.ColorSort.TestWeightCount"
    UNITS_TAG = "Status.Production.Units"
    
    # Default values
    default_weight = 500.0
    default_count = 1000
    default_unit = "lb"
    
    # Determine if we're in Live or Demo mode
    mode = "demo"
    if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
        mode = app_mode["mode"]
    
    # Check if OPC reading is paused
    is_paused = pause_state.get("paused", False)
    
    # Initialize values
    weight_value = default_weight
    count_value = default_count
    unit_value = default_unit
    opc_weight = None
    opc_count = None
    reading_status = "N/A"
    
    if mode == "live" and app_state_data.get("connected", False):
        # Always read the current OPC values for display in the status line
        if WEIGHT_TAG in app_state.tags:
            tag_value = app_state.tags[WEIGHT_TAG]["data"].latest_value
            if tag_value is not None:
                opc_weight = float(tag_value)
                
        if COUNT_TAG in app_state.tags:
            tag_value = app_state.tags[COUNT_TAG]["data"].latest_value
            if tag_value is not None:
                opc_count = int(tag_value)
                
        if UNITS_TAG in app_state.tags:
            tag_value = app_state.tags[UNITS_TAG]["data"].latest_value
            if tag_value is not None:
                unit_value = tag_value
        
        # Decide what values to use based on pause state
        if is_paused:
            # OPC reading is paused - use user inputs if available, otherwise use last known OPC values
            if user_inputs:
                weight_value = user_inputs.get("weight", opc_weight or default_weight)
                count_value = user_inputs.get("count", opc_count or default_count)
                unit_value = user_inputs.get("units", unit_value)
            else:
                # No user inputs yet, use current OPC values as starting point
                weight_value = opc_weight if opc_weight is not None else default_weight
                count_value = opc_count if opc_count is not None else default_count
            reading_status = "⏸ Paused (Manual)"
        else:
            # OPC reading is active - always use current OPC values
            weight_value = opc_weight if opc_weight is not None else default_weight
            count_value = opc_count if opc_count is not None else default_count
            reading_status = "▶ Reading from OPC"
            
        logger.info(f"Live mode: Paused={is_paused} | OPC W={opc_weight}, C={opc_count} | Using W={weight_value}, C={count_value}")
    else:
        # Demo mode or not connected - use user inputs or defaults
        if user_inputs:
            weight_value = user_inputs.get("weight", default_weight)
            count_value = user_inputs.get("count", default_count)
            unit_value = user_inputs.get("units", default_unit)
        reading_status = "Demo mode" if mode == "demo" else "Not connected"
    
    return html.Div([
        # Title
        html.H6(tr("update_counts_title", lang), className="mb-0 text-center small"),
        
        # Show current OPC values and reading status in live mode
        html.Div([
            #html.Small(
            #    f"OPC: W={opc_weight if opc_weight is not None else 'N/A'}, "
            #    f"C={opc_count if opc_count is not None else 'N/A'} | "
            #    f"Status: {reading_status}", 
            #    className="text-info"
            #)
        ], className="mb-1 text-center") if mode == "live" and app_state_data.get("connected", False) else html.Div(),
        
        # Controls container 
        html.Div([
            # Units row
            dbc.Row([
                dbc.Col(
                    html.Label(tr("units_label", lang), className="fw-bold pt-0 text-end small"),
                    width=3,
                ),
                dbc.Col(
                    dcc.Dropdown(
                        id="unit-selector",
                        options=[
                            {"label": "oz", "value": "oz"},
                            {"label": "lb", "value": "lb"},
                            {"label": "g", "value": "g"},
                            {"label": "kg", "value": "kg"}
                        ],
                        value=unit_value,
                        clearable=False,
                        style={"width": "100%", "fontSize": "0.8rem"}
                    ),
                    width=9,
                ),
            ], className="mb-1"),
            
            # Weight row
            dbc.Row([
                dbc.Col(
                    html.Label(tr("weight_label", lang), className="fw-bold pt-0 text-end small"),
                    width=3,
                ),
                dbc.Col(
                    dbc.Input(
                        id="weight-input",
                        type="number",
                        min=0,
                        step=1,
                        value=weight_value,
                        style={"width": "100%", "height": "1.4rem"}
                    ),
                    width=9,
                ),
            ]),

            # Count row
            dbc.Row([
                dbc.Col(
                    html.Label(tr("count_label", lang), className="fw-bold pt-0 text-end small"),
                    width=3,
                ),
                dbc.Col(
                    dbc.Input(
                        id="count-input",
                        type="number",
                        min=0,
                        step=1,
                        value=count_value,
                        style={"width": "100%", "height": "1.4rem"}
                    ),
                    width=9,
                ),
            ], className="mb-1"),
            
            # Two button row - same width, half each
            dbc.Row([
                dbc.Col(width=3),  # Empty space to align with inputs
                dbc.Col([
                    dbc.ButtonGroup([
                        dbc.Button(
                            tr("pause_opc_read_button", lang) if not is_paused else tr("resume_opc_read_button", lang),
                            id="toggle-opc-pause",
                            color="warning" if not is_paused else "success",
                            size="sm",
                            style={"fontSize": "0.65rem", "padding": "0.2rem 0.3rem"}
                        ),
                        dbc.Button(
                            tr("save_to_opc_button", lang),
                            id="save-count-settings",
                            color="primary",
                            size="sm",
                            style={"fontSize": "0.65rem", "padding": "0.2rem 0.3rem"}
                        )
                    ], className="w-100")
                ], width=9)
            ]),
            
            # Notification area
            dbc.Row([
                dbc.Col(width=3),
                dbc.Col(
                    html.Div(id="save-counts-notification", className="small text-success mt-1"),
                    width=9
                )
            ])
        ]),
    ], className="p-1 ps-2")

# Callback to handle the pause/resume button
@app.callback(
    Output("opc-pause-state", "data"),
    [Input("toggle-opc-pause", "n_clicks")],
    [State("opc-pause-state", "data"),
     State("app-mode", "data")],
    prevent_initial_call=True
)
def toggle_opc_pause(n_clicks, current_pause_state, app_mode):
    """Toggle OPC reading pause state"""
    if not n_clicks:
        return dash.no_update
    
    # Only allow pausing in live mode
    mode = "demo"
    if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
        mode = app_mode["mode"]
    
    if mode != "live":
        return dash.no_update
    
    # Toggle the pause state
    current_paused = current_pause_state.get("paused", False)
    new_paused = not current_paused
    
    logger.info(f"OPC reading {'paused' if new_paused else 'resumed'} by user")
    
    return {"paused": new_paused}




@app.callback(
    Output("user-inputs", "data", allow_duplicate=True),
    [Input("mode-selector", "value")],
    [State("user-inputs", "data")],
    prevent_initial_call=True
)
def clear_inputs_on_mode_switch(mode, current_inputs):
    """Clear user inputs when switching to live mode"""
    if mode == "live":
        logger.info("Switched to live mode - clearing user inputs")
        return {}  # Clear all user inputs
    return dash.no_update

# Add a new callback to handle saving to OPC UA tags
@app.callback(
    [Output("save-counts-notification", "children"),
     Output("opc-pause-state", "data", allow_duplicate=True)],
    [Input("save-count-settings", "n_clicks")],
    [State("weight-input", "value"),
     State("count-input", "value"),
     State("unit-selector", "value"),
     State("app-state", "data"),
     State("app-mode", "data"),
     State("opc-pause-state", "data")],
    prevent_initial_call=True
)
def save_and_resume_opc_reading(n_clicks, weight_value, count_value, unit_value, 
                               app_state_data, app_mode, pause_state):
    """Save the count settings to OPC UA tags and resume OPC reading"""
    if not n_clicks:
        return dash.no_update, dash.no_update
    
    # Tag definitions for writing
    WEIGHT_TAG = "Settings.ColorSort.TestWeightValue"
    COUNT_TAG = "Settings.ColorSort.TestWeightCount"
    
    # Determine if we're in Live mode
    mode = "demo"
    if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
        mode = app_mode["mode"]
    
    # Only write to tags in live mode when connected
    if mode == "live" and app_state_data.get("connected", False):
        try:
            success_messages = []
            error_messages = []
            
            # Write weight value to OPC UA tag
            if WEIGHT_TAG in app_state.tags and weight_value is not None:
                try:
                    app_state.tags[WEIGHT_TAG]["node"].set_value(float(weight_value))
                    success_messages.append(f"Weight: {weight_value}")
                    logger.info(f"Successfully wrote weight value {weight_value} to {WEIGHT_TAG}")
                except Exception as e:
                    error_messages.append(f"Weight write error: {str(e)}")
                    logger.error(f"Error writing weight value to {WEIGHT_TAG}: {e}")
            
            # Write count value to OPC UA tag
            if COUNT_TAG in app_state.tags and count_value is not None:
                try:
                    app_state.tags[COUNT_TAG]["node"].set_value(int(count_value))
                    success_messages.append(f"Count: {count_value}")
                    logger.info(f"Successfully wrote count value {count_value} to {COUNT_TAG}")
                except Exception as e:
                    error_messages.append(f"Count write error: {str(e)}")
                    logger.error(f"Error writing count value to {COUNT_TAG}: {e}")
            
            # Prepare notification message and resume reading if successful
            if success_messages and not error_messages:
                notification = f"✓ Saved: {', '.join(success_messages)} - OPC reading resumed"
                # Resume OPC reading after successful save
                resumed_state = {"paused": False}
                logger.info("OPC reading resumed after successful save")
                return notification, resumed_state
            elif success_messages and error_messages:
                notification = f"⚠ Partial: {', '.join(success_messages)}. Errors: {', '.join(error_messages)}"
                return notification, dash.no_update
            elif error_messages:
                notification = f"✗ Errors: {', '.join(error_messages)}"
                return notification, dash.no_update
            else:
                notification = "⚠ No OPC UA tags found for writing"
                return notification, dash.no_update
            
        except Exception as e:
            error_msg = f"✗ Save failed: {str(e)}"
            logger.error(f"Unexpected error saving count settings: {e}")
            return error_msg, dash.no_update
    
    else:
        # Not in live mode or not connected
        if mode == "demo":
            return "✓ Saved locally (Demo mode)", dash.no_update
        else:
            return "⚠ Not connected to OPC server", dash.no_update


# Update the save_user_inputs callback to mark when changes are made in live mode
@app.callback(
    Output("user-inputs", "data"),
    [
        Input("unit-selector", "value"),
        Input("weight-input", "value"),
        Input("count-input", "value")
    ],
    [State("user-inputs", "data"),
     State("app-mode", "data")],
    prevent_initial_call=True
)
def save_user_inputs_with_mode_tracking(units, weight, count, current_data, app_mode):
    """Save user input values when they change (with mode tracking)"""
    ctx = callback_context
    if not ctx.triggered:
        return current_data or {"units": "lb", "weight": 500.0, "count": 1000}
    
    # Get which input triggered the callback
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
    
    # Determine current mode
    mode = "demo"
    if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
        mode = app_mode["mode"]
    
    # Create a new data object with defaults if current_data is None
    new_data = current_data.copy() if current_data else {"units": "lb", "weight": 500.0, "count": 1000}
    
    # Mark if user made changes in live mode
    if mode == "live":
        new_data["live_mode_user_changed"] = True
        logger.info(f"User changed {trigger_id} in live mode")
    
    # Update the value that changed
    if trigger_id == "unit-selector" and units is not None:
        new_data["units"] = units
    elif trigger_id == "weight-input" and weight is not None:
        new_data["weight"] = weight
    elif trigger_id == "count-input" and count is not None:
        new_data["count"] = count
    
    return new_data

# First, let's modify the section_1_2 callback to use the counter data
@app.callback(
    Output("section-1-2", "children"),
    [Input("production-data-store", "data"),
     Input("status-update-interval", "n_intervals"),
     Input("current-dashboard", "data"),
     Input("historical-time-index", "data"),
     Input("historical-data-cache", "data")],
    [State("app-state", "data"),
     State("app-mode", "data")],
    prevent_initial_call=True
)
def update_section_1_2(production_data, n_intervals, which, state_data, historical_data, app_state_data, app_mode):

    """Update section 1-2 with side-by-side pie charts for accepts/rejects and reject breakdown
    using production data from section 1-1 and counter data from section 5-2"""
    
    # Only run when we're in the "main" dashboard
    if which != "main":
        raise PreventUpdate
        
    global previous_counter_values
    
    counter_colors = {
        1: "green",       # Blue
        2: "lightgreen",      # Green
        3: "orange",     # Orange
        4: "blue",      # Black
        5: "#f9d70b",    # Yellow (using hex to ensure visibility)
        6: "magenta",    # Magenta
        7: "cyan",       # Cyan
        8: "red",        # Red
        9: "purple",
        10: "brown",
        11: "gray",
        12: "lightblue"
    }

    # Extract data from the shared production data store
    total_capacity = production_data.get("capacity", 50000)
    accepts = production_data.get("accepts", 47500)
    rejects = production_data.get("rejects", 2500)
    
    # Calculate percentages for the first pie chart - exact same calculation as section 1-1
    total = accepts + rejects
    accepts_percent = (accepts / total * 100) if total > 0 else 0
    rejects_percent = (rejects / total * 100) if total > 0 else 0
    
    # Second chart data - Use the counter values for the reject breakdown
    # Ensure previous_counter_values has a predictable baseline
    if 'previous_counter_values' not in globals() or not previous_counter_values:
        # Start counters at zero instead of random demo values
        previous_counter_values = [0] * 12
    
    # Calculate the total of all counter values
    total_counter_value = sum(previous_counter_values)
    
    if total_counter_value > 0:
        # Create percentage breakdown for each counter relative to total rejects
        # Filter out counters with zero values and track their original counter numbers
        reject_counters = {}
        counter_indices = {}  # Track which counter number each entry corresponds to
        for i, value in enumerate(previous_counter_values):
            if value > 0:  # Only include counters with values greater than 0
                counter_name = f"Counter {i+1}"
                counter_number = i + 1  # Store the actual counter number
                # This counter's percentage of the total rejects
                counter_percent_of_rejects = (value / total_counter_value) * 100
                reject_counters[counter_name] = counter_percent_of_rejects
                counter_indices[counter_name] = counter_number
    else:
        # Fallback if counter values sum to zero - create empty dict
        reject_counters = {}
    
    # Create first pie chart - Accepts/Rejects ratio
    fig1 = go.Figure(data=[go.Pie(
        labels=['Accepts', 'Rejects'],
        values=[accepts_percent, rejects_percent],  # Use the exact percentages from section 1-1
        hole=.4,
        marker_colors=['green', 'red'],
        textinfo='percent',
        insidetextorientation='radial',
        rotation = 90
    )])

    # Update layout for first chart with centered title
    fig1.update_layout(
        title={
            'text': "Accept/Reject Ratio",
            'y': 0.99,
            'x': 0.5,
            'xanchor': 'center',
            'yanchor': 'top'
        },
        margin=dict(l=10, r=10, t=25, b=10),
        height=210,
        showlegend=False,  # Set showlegend to False to remove the legend
        plot_bgcolor='var(--chart-bg)',
        paper_bgcolor='var(--chart-bg)'
    )

    # Create second pie chart - Reject breakdown (only if we have non-zero data)
    if reject_counters:  # Only create chart if we have data
        # Extract data for the second pie chart
        labels = list(reject_counters.keys())
        values = list(reject_counters.values())
        # Use the correct counter numbers for colors instead of sequential indices
        colors = [counter_colors.get(counter_indices[label], "gray") for label in labels]

        # Create second pie chart - Reject breakdown
        fig2 = go.Figure(data=[go.Pie(
            labels=labels,
            values=values,
            hole=.4,
            marker_colors=colors,
            textinfo='percent',
            insidetextorientation='radial'
        )])

        # Update layout for second chart with centered title
        fig2.update_layout(
            title={
                'text': "Reject Percentages",
                'y': 0.99,
                'x': 0.5,
                'xanchor': 'center',
                'yanchor': 'top'
            },
            margin=dict(l=10, r=10, t=25, b=10),
            height=210,
            showlegend=False,  # Set showlegend to False to remove the legend
            plot_bgcolor='var(--chart-bg)',
            paper_bgcolor='var(--chart-bg)'
        )
        
        # Second chart content
        second_chart_content = dcc.Graph(
            figure=fig2,
            config={'displayModeBar': False, 'responsive': True},
            style={'width': '100%', 'height': '100%'}
        )
    else:
        # No data available - show placeholder
        second_chart_content = html.Div([
            html.Div("No Reject Data", className="text-center text-muted d-flex align-items-center justify-content-center h-100"),
        ], style={'minHeight': '200px', 'height': 'auto', 'border': '1px solid #dee2e6', 'borderRadius': '0.25rem'})
    
    # Return the layout with both charts side by side
    return html.Div([
        dbc.Row([
            # First chart
            dbc.Col(
                dcc.Graph(
                    figure=fig1,
                    config={'displayModeBar': False, 'responsive': True},
                    style={'width': '100%', 'height': '100%'}
                ),
                width=6
            ),
            
            # Second chart or placeholder
            dbc.Col(
                second_chart_content,
                width=6
            ),
        ]),
    ])

@app.callback(
    Output("user-inputs", "data", allow_duplicate=True),
    [Input("auto-connect-trigger", "data")],
    [State("user-inputs", "data")],
    prevent_initial_call=True
)
def initialize_user_inputs(trigger, current_data):
    """Initialize user inputs on page load if not already set"""
    if current_data:
        return dash.no_update
    return {"units": "lb", "weight": 500.0, "count": 1000}

@app.callback(
    Output("section-2", "children"),
    [Input("status-update-interval", "n_intervals"),
     Input("current-dashboard",       "data")],
    [State("app-state", "data"),
     State("app-mode", "data"),
     State("language-preference-store", "data")],
    prevent_initial_call=True
)
def update_section_2(n_intervals,which, app_state_data, app_mode, lang):
    """Update section 2 with three status boxes and feeder gauges"""
    
      # only run when we’re in the “main” dashboard
    if which != "main":
        raise PreventUpdate
        # or return [no_update, no_update]
    # Tag definitions
    PRESET_NUMBER_TAG = "Status.Info.PresetNumber"
    PRESET_NAME_TAG = "Status.Info.PresetName"
    GLOBAL_FAULT_TAG = "Status.Faults.GlobalFault"
    GLOBAL_WARNING_TAG = "Status.Faults.GlobalWarning"
    FEEDER_TAG_PREFIX = "Status.Feeders."
    FEEDER_TAG_SUFFIX = "IsRunning"
    MODEL_TAG = "Status.Info.Type"  # Added this tag to check model type
    
    # Determine if we're in Live or Demo mode
    mode = "demo"  # Default to demo mode
    if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
        mode = app_mode["mode"]
    
    # Define color styles for different states
    success_style = {"backgroundColor": "#28a745", "color": "white"}  # Green
    danger_style = {"backgroundColor": "#dc3545", "color": "white"}   # Red
    warning_style = {"backgroundColor": "#ffc107", "color": "black"}  # Yellow
    secondary_style = {"backgroundColor": "#6c757d", "color": "white"}  # Gray
    
    # Check model type to determine number of gauges to show
    show_all_gauges = True  # Default to showing all 4 gauges
    model_type = None
    
    # Define box styles based on mode
    if mode == "demo":
        # Demo mode - force green for all boxes
        preset_text = "1 Yellow CORN"
        preset_style = success_style
        
        status_text = "GOOD"
        status_style = success_style
        
        feeder_text = "Running"
        feeder_style = success_style
        
        # In demo mode, show all gauges
        show_all_gauges = True
        
    elif not app_state_data.get("connected", False):
        # Not connected - all gray
        preset_text = "Unknown"
        preset_style = secondary_style
        
        status_text = "Unknown"
        status_style = secondary_style
        
        feeder_text = "Unknown"
        feeder_style = secondary_style
        
        # When not connected, show all gauges
        show_all_gauges = True
        
    else:
        # Live mode - FIXED to properly access the global app_state
        preset_number = "N/A"
        preset_name = "N/A"
        
        # Check model type first to determine gauge visibility
        if MODEL_TAG in app_state.tags:
            model_type = app_state.tags[MODEL_TAG]["data"].latest_value
            if model_type == "RGB400":
                show_all_gauges = False  # Hide gauges 3 and 4
                #logger.info("Model type is RGB400 - hiding gauges 3 and 4")
            else:
                show_all_gauges = True
                #logger.info(f"Model type is {model_type} - showing all gauges")
        
        # Try to get preset information - FIXED to use proper app_state reference
        if PRESET_NUMBER_TAG in app_state.tags:
            preset_number = app_state.tags[PRESET_NUMBER_TAG]["data"].latest_value
            if preset_number is None:
                preset_number = "N/A"
            #logger.info(f"Retrieved preset number: {preset_number}")
                
        if PRESET_NAME_TAG in app_state.tags:
            preset_name = app_state.tags[PRESET_NAME_TAG]["data"].latest_value
            if preset_name is None:
                preset_name = "N/A"
            #logger.info(f"Retrieved preset name: {preset_name}")
                
        preset_text = f"{preset_number} {preset_name}"
        preset_style = success_style  # Default to green
        
        # Check fault and warning status - FIXED to use proper app_state reference
        has_fault = False
        has_warning = False
        
        if GLOBAL_FAULT_TAG in app_state.tags:
            has_fault = bool(app_state.tags[GLOBAL_FAULT_TAG]["data"].latest_value)
            
        if GLOBAL_WARNING_TAG in app_state.tags:
            has_warning = bool(app_state.tags[GLOBAL_WARNING_TAG]["data"].latest_value)
            
        # Set status text and style based on fault/warning
        if has_fault:
            status_text = "FAULT"
            status_style = danger_style
        elif has_warning:
            status_text = "WARNING"
            status_style = warning_style
        else:
            status_text = "GOOD"
            status_style = success_style

        if status_text in ("FAULT", "WARNING", "GOOD"):
            status_text = tr(f"{status_text.lower()}_status", lang)
            
        # Check feeder status - FIXED to use proper app_state reference
        feeder_running = False
        
        # Check only the appropriate number of feeders based on model
        max_feeder = 2 if not show_all_gauges else 4
        for feeder_num in range(1, max_feeder + 1):
            tag_name = f"{FEEDER_TAG_PREFIX}{feeder_num}{FEEDER_TAG_SUFFIX}"
            if tag_name in app_state.tags:
                if bool(app_state.tags[tag_name]["data"].latest_value):
                    feeder_running = True
                    break
                    
        if feeder_running:
            feeder_text = tr("running_state", lang)
            feeder_style = success_style
        else:
            feeder_text = tr("stopped_state", lang)
            feeder_style = secondary_style
        
        # Add debug logging for live mode
        logger.info(f"Live mode - Preset: {preset_text}, Status: {status_text}, Feeder: {feeder_text}")
    
    # Create the feeder rate boxes with conditional display
    feeder_boxes = create_feeder_rate_boxes(app_state_data, app_mode, mode, show_all_gauges)
    
    # Create the three boxes with explicit styling and add feeder gauges
    return html.Div([
        html.H5(tr("machine_status_title", lang), className="mb-2 text-left"),
        
        # Box 1 - Preset - Using inline styling instead of Bootstrap classes
        html.Div([
            html.Div([
                html.Div([
                    html.Span(tr("preset_label", lang) + " ", className="fw-bold"),
                    html.Span(preset_text),
                ], className="h7"),
            ], className="p-3"),
        ], className="mb-2", style={"borderRadius": "0.25rem", **preset_style}),
        
        # Box 2 - Status - Using inline styling
        html.Div([
            html.Div([
                html.Div([
                    html.Span(tr("status_label", lang) + " ", className="fw-bold"),
                    html.Span(status_text),
                ], className="h7"),
            ], className="p-3"),
        ], className="mb-2", style={"borderRadius": "0.25rem", **status_style}),
        
        # Box 3 - Feeders - Using inline styling
        html.Div([
            html.Div([
                html.Div([
                    html.Span(tr("feeders_label", lang) + " ", className="fw-bold"),
                    html.Span(feeder_text),
                ], className="h7"),
            ], className="p-3"),
        ], className="mb-2", style={"borderRadius": "0.25rem", **feeder_style}),

        # Row of feeder rate boxes
        feeder_boxes,
    ])


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


@app.callback(
    Output("section-3-1", "children"),
    [Input("status-update-interval", "n_intervals"),
     Input("current-dashboard",       "data")],
    [State("additional-image-store", "data"),
     State("language-preference-store", "data")],
    prevent_initial_call=True
)

def update_section_3_1(n_intervals,which, additional_image_data, lang):
    """Update section 3-1 with the Load Image button and additional image if loaded"""
    # Debug logging
    #logger.info(f"Image data in section-3-1: {'' if not additional_image_data else 'Data present'}")
    
    # only run when we’re in the “main” dashboard
    if which != "main":
        raise PreventUpdate
        # or return [no_update, no_update]
    # Check if additional image is loaded
    has_additional_image = additional_image_data and 'image' in additional_image_data
    
    # More debug logging
    #if has_additional_image:
    #    logger.info("Section 3-1: Image found in data store")
    #else:
    #    logger.info("Section 3-1: No image in data store")
    
    # Create the additional image section with auto-scaling
    if has_additional_image:
        additional_image_section = html.Div([
            html.Img(
                src=additional_image_data['image'],
                style={
                    'width': '100%',
                    'maxWidth': '100%',
                    'maxHeight': '130px',
                    'objectFit': 'contain',
                    'margin': '0 auto',
                    'display': 'block'
                }
            )
        ], className="text-center", style={'minHeight': '130px', 'height': 'auto', 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center'})
    else:
        additional_image_section = html.Div(
            "No custom image loaded",
            className="text-center text-muted",
            style={'minHeight': '130px', 'height': 'auto', 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center'}
        )
    
    return html.Div([
        # Title and Load button row
        dbc.Row([
            # Title
            dbc.Col(html.H5(tr("corporate_logo_title", lang), className="mb-0"), width=8),
            # Load button
            dbc.Col(
                dbc.Button(
                    tr("load_image_button", lang),
                    id="load-additional-image",
                    color="primary", 
                    size="sm",
                    className="float-end"
                ), 
                width=4
            ),
        ], className="mb-2 align-items-center"),
        
        # Additional image section with fixed height
        additional_image_section,
    ], style={'minHeight': '175px', 'height': 'auto'})  # Flexible height for section 3-1

@app.callback(
    Output("upload-modal", "is_open"),
    [Input("load-additional-image", "n_clicks"),
     Input("close-upload-modal", "n_clicks")],
    [State("upload-modal", "is_open")],
    prevent_initial_call=True
)
def toggle_upload_modal(load_clicks, close_clicks, is_open):
    """Toggle the upload modal when the Load Image button is clicked"""
    ctx = callback_context
    
    # If callback wasn't triggered, don't change the state
    if not ctx.triggered:
        return dash.no_update
        
    # Get the ID of the component that triggered the callback
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
    
    # If the Load Image button was clicked and modal is not already open, open it
    if trigger_id == "load-additional-image" and load_clicks and not is_open:
        return True
    
    # If the Close button was clicked and modal is open, close it
    elif trigger_id == "close-upload-modal" and close_clicks and is_open:
        return False
    
    # Otherwise, don't change the state
    return is_open

@app.callback(
    Output("section-3-2", "children"),
    [Input("status-update-interval", "n_intervals"),
     Input("current-dashboard",       "data")],
    [State("app-state", "data"),
     State("app-mode", "data"),
     State("language-preference-store", "data")],
    prevent_initial_call=True

)
def update_section_3_2(n_intervals,which, app_state_data, app_mode, lang):
    """Update section 3-2 with machine information and Satake logo"""

    # only run when we’re in the “main” dashboard
    if which != "main":
        raise PreventUpdate
        # or return [no_update, no_update]

    # Tag definitions for easy updating
    SERIAL_TAG = "Status.Info.Serial"
    MODEL_TAG = "Status.Info.Type"  # Added tag for model information
    
    # Determine if we're in Live or Demo mode
    mode = "demo"  # Default to demo mode
    if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
        mode = app_mode["mode"]
    
    if mode == "demo":
        # Demo mode values
        serial_number = "2025_1_4CH"
        status_text = "DEMO"
        model_text = "Enpresor RGB"
        last_update = "NOW"
        status_class = "text-success"
    else:
        # Live mode - use original code with model tag
        serial_number = "Unknown"
        if app_state_data.get("connected", False) and SERIAL_TAG in app_state.tags:
            serial_number = app_state.tags[SERIAL_TAG]["data"].latest_value or "Unknown"
        
        # Get the model from the Type tag when in Live mode
        model_text = "ENPRESOR RGB"  # Default model
        if app_state_data.get("connected", False) and MODEL_TAG in app_state.tags:
            model_from_tag = app_state.tags[MODEL_TAG]["data"].latest_value
            if model_from_tag:
                model_text = model_from_tag  # Use the model from the tag if available
        
        status_text = "Online" if app_state_data.get("connected", False) else "Offline"
        status_class = "text-success" if app_state_data.get("connected", False) else "text-secondary"
        last_update = app_state.last_update_time.strftime("%H:%M:%S") if app_state.last_update_time else "Never"
    
    return html.Div([
        # Title
        html.H5(tr("machine_info_title", lang), className="mb-2 text-center"),
        
        # Custom container with fixed height and auto-scaling image
        html.Div([
            # Logo container (left side)
            html.Div([
                html.Img(
                    src=f'data:image/png;base64,{SATAKE_LOGO}',
                    style={
                        'width': '100%',
                        'maxWidth': '100%',

                        'maxHeight': '200px',  # Increased maximum height

                        'objectFit': 'contain',
                        'margin': '0 auto',
                        'display': 'block'
                    }
                )
            ], className="machine-info-logo", style={
                'flex': '0 0 auto',

                'width': '45%',
                'maxWidth': '180px',
                'minHeight': '180px',  # Increased minimum height for logo container

                'display': 'flex',
                'alignItems': 'center',
                'justifyContent': 'center',
                'paddingRight': '15px'
            }),
            
            # Information container (right side)
            html.Div([
                html.Div([
                    html.Span(tr("serial_number_label", lang) + " ", className="fw-bold"),
                    html.Span(serial_number),
                ], className="mb-1"),
                
                html.Div([
                    html.Span(tr("status_label", lang) + " ", className="fw-bold"),
                    html.Span(status_text, className=status_class),
                ], className="mb-1"),
                
                html.Div([
                    html.Span(tr("model_label", lang) + " ", className="fw-bold"),
                    html.Span(model_text),
                ], className="mb-1"),
                
                html.Div([
                    html.Span(tr("last_update_label", lang) + " ", className="fw-bold"),
                    html.Span(last_update),
                ], className="mb-1"),
            ], style={
                'flex': '1',
                'paddingLeft': '30px',  # Increased left padding to shift text right more
                'borderLeft': '1px solid #eee',
                'marginLeft': '15px',
                'minHeight': '150px',  # Reduced minimum height for text container
                'display': 'flex',
                'flexDirection': 'column',
                'justifyContent': 'center'
            }),
        ], className="machine-info-container", style={
            'display': 'flex',
            'flexDirection': 'row',
            'alignItems': 'center',
            'flexWrap': 'wrap',
            'width': '100%',
            'minHeight': '150px'  # Reduced minimum height for the whole container
        }),
    ], style={'height': 'auto'})  # Allow section 3-2 height to adjust

@app.callback(
    Output("section-4", "children"),
    [Input("status-update-interval", "n_intervals"),
     Input("current-dashboard",       "data")],
    [State("app-state", "data"),
     State("app-mode", "data"),
     State("language-preference-store", "data")],
    prevent_initial_call=True
)
def update_section_4(n_intervals,which, app_state_data, app_mode, lang):
    """Update section 4 with the color sort primary list.

    Each sensitivity's number and name are displayed above its image.
    """
    # only run when we’re in the “main” dashboard
    if which != "main":
        raise PreventUpdate
        # or return [no_update, no_update]
    # Tag definitions for easy updating
    PRIMARY_ACTIVE_TAG_PREFIX = "Settings.ColorSort.Primary"
    PRIMARY_ACTIVE_TAG_SUFFIX = ".IsAssigned"
    PRIMARY_NAME_TAG_PREFIX = "Settings.ColorSort.Primary"
    PRIMARY_NAME_TAG_SUFFIX = ".Name"
    PRIMARY_IMAGE_TAG_PREFIX = "Settings.ColorSort.Primary"
    PRIMARY_IMAGE_TAG_SUFFIX = ".SampleImage"
    
    # Define colors for each primary number
    primary_colors = {
        1: "green",       # Blue
        2: "lightgreen",      # Green
        3: "orange",     # Orange
        4: "blue",      # Black
        5: "#f9d70b",    # Yellow (using hex to ensure visibility)
        6: "magenta",    # Magenta
        7: "cyan",       # Cyan
        8: "red",        # Red
        9: "purple",
        10: "brown",
        11: "gray", 
        12: "lightblue"
    }
    
    # Define base64 image strings for demo mode fallback
    base64_image_strings = {
        1: base64_image_string1,
        2: base64_image_string2,
        3: base64_image_string3,
        4: base64_image_string4,
        5: base64_image_string5,
        6: base64_image_string6,
        7: base64_image_string7,
        8: base64_image_string8
    }
    
    # Determine if we're in Live or Demo mode
    mode = "demo"  # Default to demo mode
    if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
        mode = app_mode["mode"]
    
    # Define demo mode primary names and active status
    demo_primary_names = {
        1: "CORN",
        2: "SPOT",
        3: "GREEN",
        4: "SOY",
        5: "SPLIT",
        6: "DARKS",
        7: "BROKEN",
        8: "MOLD",
        9: "",
        10: "",
        11: "",
        12: ""
    }
    
    # For demo mode, all primaries are active except #5 (to show the inactive state)
    demo_primary_active = {i: (i != 5) for i in range(1, 13)}
    
    # Initialize lists for left and right columns
    left_column_items = []
    right_column_items = []
    
    # Define the image container style with WHITE background for both modes
    # Base style for the image containers.  Border color is set later based on
    # whether a sensitivity is assigned.
    image_container_style = {
        "height": "50px",
        "width": "50px",
        "marginRight": "0px",
        "border": "2px solid #ccc",  # Increased default border thickness
        "borderRadius": "3px",
        "display": "flex",
        "alignItems": "center",
        "justifyContent": "center",
        "overflow": "hidden",
        "padding": "0px",
        "backgroundColor": "#ffffff"  # Force white background for both light and dark mode
    }
    
    # Image style to fill the container
    image_style = {
        "height": "100%",
        "width": "100%",
        "objectFit": "contain",
    }
    
    if mode == "demo":
        # Demo mode - use predefined values and demo images
        for i in range(1, 13):
            name = demo_primary_names[i]
            is_active = demo_primary_active[i]
                
            # Set styling based on active status
            if is_active:
                text_color = primary_colors[i]
                text_class = ""
            else:
                text_color = "#aaaaaa"  # Gray for inactive
                text_class = "text-muted"
            
            # Create text style with added bold font weight
            text_style = {
                "color": text_color,
                "display": "inline-block",
                "verticalAlign": "middle",
                "fontWeight": "bold",
                "whiteSpace": "nowrap",
            }
            border_color = "green" if is_active else "red"
            if not is_active:
                text_style["fontStyle"] = "italic"
            image_style_current = image_container_style.copy()
            image_style_current["border"] = f"2px solid {border_color}"
            
            # Create item with appropriate image or empty container
            if i <= 8 and i in base64_image_strings:  # First 8 items with images in demo mode
                base64_str = base64_image_strings[i]
                img_src = f"data:image/png;base64,{base64_str}" if not base64_str.startswith("data:") else base64_str

                # Create item with image in bordered container
                item = html.Div([
                    html.Span(
                        f"{i}. {name}",
                        style=text_style
                    ),
                    html.Div([
                        html.Img(
                            src=img_src,
                            style=image_style
                        )
                    ], style=image_style_current),
                ],
                className=f"mb-1 {text_class}",
                style={"display": "flex", "flexDirection": "column", "alignItems": "center"})
            else:  # Items 9-12 or fallbacks - empty white container instead of image
                item = html.Div([
                    html.Span(
                        f"{i}. {name}",
                        style=text_style
                    ),
                    html.Div([
                        # Nothing inside, just the white background
                    ], style=image_style_current),
                ],
                className=f"mb-1 {text_class}",
                style={"display": "flex", "flexDirection": "column", "alignItems": "center"})
            
            # Add to appropriate column based on odd/even
            if i % 2 == 1:  # Odds on the left
                left_column_items.append(item)
            else:          # Evens on the right
                right_column_items.append(item)
    
    elif not app_state_data.get("connected", False):
        # When not connected, show placeholder list with empty white containers
        for i in range(1, 13):
            # Bold text style for not connected state
            not_connected_style = {
                "display": "inline-block",
                "verticalAlign": "middle",
                "fontWeight": "bold",
                "whiteSpace": "nowrap",
            }
            
            item = html.Div([
                html.Span(
                    f"{i}) Not connected",
                    className="text-muted",
                    style=not_connected_style
                ),
                html.Div([], style=image_container_style),  # Empty white container
            ],
            className="mb-1",
            style={"display": "flex", "flexDirection": "column", "alignItems": "center"})
            
            # Add to appropriate column based on odd/even
            if i % 2 == 1:  # Odds on the left
                left_column_items.append(item)
            else:          # Evens on the right
                right_column_items.append(item)
    
    else:
        # Live mode - load images from OPC UA tags
        for i in range(1, 13):
            # Check if the primary is active
            is_active = True  # Default to active
            active_tag_name = f"{PRIMARY_ACTIVE_TAG_PREFIX}{i}{PRIMARY_ACTIVE_TAG_SUFFIX}"
            
            if active_tag_name in app_state.tags:
                is_active = bool(app_state.tags[active_tag_name]["data"].latest_value)
            
            # Get primary name
            name = f"Primary {i}"  # Default name
            name_tag = f"{PRIMARY_NAME_TAG_PREFIX}{i}{PRIMARY_NAME_TAG_SUFFIX}"
            
            if name_tag in app_state.tags:
                tag_value = app_state.tags[name_tag]["data"].latest_value
                if tag_value is not None:
                    name = tag_value
            
            # Get sample image from OPC UA tag
            image_tag = f"{PRIMARY_IMAGE_TAG_PREFIX}{i}{PRIMARY_IMAGE_TAG_SUFFIX}"
            has_image = False
            image_src = None
            
            if image_tag in app_state.tags:
                try:
                    image_data = app_state.tags[image_tag]["data"].latest_value
                    if image_data is not None:
                        # Check if the image data is already in the correct format
                        if isinstance(image_data, str):
                            if image_data.startswith("data:image"):
                                # Already in data URL format
                                image_src = image_data
                                has_image = True
                            elif len(image_data) > 100:  # Assume it's base64 if it's a long string
                                # Try to determine image type and create data URL
                                # For now, assume PNG - you might need to detect the actual format
                                image_src = f"data:image/png;base64,{image_data}"
                                has_image = True
                        elif isinstance(image_data, bytes):
                            # Convert bytes to base64
                            base64_str = base64.b64encode(image_data).decode('utf-8')
                            image_src = f"data:image/png;base64,{base64_str}"
                            has_image = True
                except Exception as e:
                    logger.error(f"Error processing image data for Primary {i}: {e}")
                    has_image = False
            else:
                logger.debug(f"Image tag {image_tag} not found in app_state.tags")
            
            # Set styling based on active status
            if is_active:
                text_color = primary_colors[i]
                text_class = ""
            else:
                text_color = "#aaaaaa"  # Gray for inactive
                text_class = "text-muted"
            
            # Create text style with added bold font weight
            text_style = {
                "color": text_color,
                "display": "inline-block",
                "verticalAlign": "middle",
                "fontWeight": "bold",
                "whiteSpace": "nowrap",
            }
            border_color = "green" if is_active else "red"
            if not is_active:
                text_style["fontStyle"] = "italic"
            image_style_current = image_container_style.copy()
            image_style_current["border"] = f"2px solid {border_color}"

            # Create item with image from OPC UA tag or empty white container
            if has_image and image_src:
                item = html.Div([
                    html.Span(
                        f"{i}) {name}",
                        style=text_style
                    ),
                    html.Div([  # Wrapper div for the image with white background
                        html.Img(
                            src=image_src,
                            style=image_style,
                            title=f"Sample image for {name}"  # Add tooltip
                        )
                    ], style=image_style_current),
                ],
                className=f"mb-1 {text_class}",
                style={"display": "flex", "flexDirection": "column", "alignItems": "center"})
            else:
                # No image available - show empty white container
                item = html.Div([
                    html.Span(
                        f"{i}) {name}",
                        style=text_style
                    ),
                    html.Div([  # Empty white container
                        # Nothing inside, just the white background
                    ], style=image_style_current),
                ],
                className=f"mb-1 {text_class}",
                style={"display": "flex", "flexDirection": "column", "alignItems": "center"})
            
            # Add to appropriate column based on odd/even
            if i % 2 == 1:  # Odds on the left
                left_column_items.append(item)
            else:          # Evens on the right
                right_column_items.append(item)
    
    # Allow this panel to flex so it shares space with other sections
    container_style = {"flex": "1"}
    
    # Return two-column layout
    return html.Div([
        html.H5(tr("sensitivities_title", lang), className="mb-2 text-left"),
        
        # Create a row with two columns
        dbc.Row([
            # Left column - odd items
            dbc.Col(
                html.Div(left_column_items),
                width=6
            ),

            # Right column - even items
            dbc.Col(
                html.Div(right_column_items),
                width=6
            ),
        ]),
    ], style=container_style)




@app.callback(
    Output("section-5-1", "children"),
    [Input("status-update-interval", "n_intervals"),
     Input("current-dashboard",       "data"),
    Input("historical-time-index",   "data"),
    Input("historical-data-cache",   "data")],
    [State("app-state", "data"),
     State("app-mode", "data"),
     State("active-machine-store", "data"),
     State("weight-preference-store", "data"),
     State("production-rate-unit", "data"),
     State("language-preference-store", "data")],
    prevent_initial_call=True
)
def update_section_5_1(n_intervals, which, state_data, historical_data, app_state_data, app_mode, active_machine_data, weight_pref, pr_unit, lang):

    """Update section 5-1 with trend graph for objects per minute"""
     # only run when we’re in the “main” dashboard
    if which != "main":
        raise PreventUpdate
        # or return [no_update, no_update]

    # Tag definitions - Easy to update when actual tag names are available
    OBJECTS_PER_MIN_TAG = "Status.ColorSort.Sort1.Throughput.ObjectPerMin.Current"
    CAPACITY_TAG = "Status.ColorSort.Sort1.Throughput.KgPerHour.Current"

    # Determine which units to display
    units = pr_unit or "objects"
    if units == "capacity":
        section_title = tr("production_rate_capacity_title", lang)
        data_tag = CAPACITY_TAG
    else:
        section_title = tr("production_rate_objects_title", lang)
        data_tag = OBJECTS_PER_MIN_TAG
    
    # Fixed time range for X-axis (last 2 minutes with 1-second intervals)
    max_points = 120  # 2 minutes × 60 seconds
    
    # Determine if we're in Live or Demo mode
    mode = "demo"  # Default to demo mode
    if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
        mode = app_mode["mode"]


    if mode == "historical":
        hours = state_data.get("hours", 24) if isinstance(state_data, dict) else 24
        active_id = active_machine_data.get("machine_id") if active_machine_data else None
        hist_data = (
            historical_data if isinstance(historical_data, dict) and "capacity" in historical_data
            else get_historical_data(timeframe=f"{hours}h", machine_id=active_id)
        )
        times = hist_data["capacity"]["times"]
        values_lbs = hist_data["capacity"]["values"]

        x_data = [t.strftime("%H:%M:%S") if isinstance(t, datetime) else t for t in times]
        y_data = [convert_capacity_from_lbs(v, weight_pref) for v in values_lbs]
        if y_data:
            min_val = max(0, min(y_data) * 0.9)
            max_val = max(y_data) * 1.1
        else:
            min_val = 0
            max_val = 10000

    elif mode == "live" and app_state_data.get("connected", False):
        # Live mode and connected - get real data
        tag_found = False
        current_value = 0


        
        # Check if the tag exists
        if data_tag in app_state.tags:
            tag_found = True
            tag_data = app_state.tags[data_tag]['data']
            
            # Get current value
            current_value = tag_data.latest_value if tag_data.latest_value is not None else 0
            if units == "capacity":
                current_value = convert_capacity_from_kg(current_value, weight_pref)
            
            # Get historical data
            timestamps = tag_data.timestamps
            values = tag_data.values
            if units == "capacity":
                values = [convert_capacity_from_kg(v, weight_pref) for v in values]
            
            # If we have data, create the time series
            if timestamps and values:
                # Ensure we only use the most recent data points (up to max_points)
                if len(timestamps) > max_points:
                    timestamps = timestamps[-max_points:]
                    values = values[-max_points:]
                
                # Format times for display
                x_data = [ts.strftime("%H:%M:%S") for ts in timestamps]
                y_data = values
                
                # Determine min and max values for y-axis with some padding
                if len(y_data) > 0:
                    min_val = max(0, min(y_data) * 0.9) if min(y_data) > 0 else 0
                    max_val = max(y_data) * 1.1 if max(y_data) > 0 else 10000
                else:
                    min_val = 0
                    max_val = 100000
            else:
                # No historical data yet, create empty chart
                current_time = datetime.now()
                x_data = [(current_time - timedelta(seconds=i)).strftime("%H:%M:%S") for i in range(max_points)]
                x_data.reverse()  # Put in chronological order
                y_data = [None] * max_points
                min_val = 0
                max_val = 10000
        else:
            # Tag not found - create dummy data
            current_time = datetime.now()
            x_data = [(current_time - timedelta(seconds=i)).strftime("%H:%M:%S") for i in range(max_points)]
            x_data.reverse()  # Put in chronological order
            y_data = [None] * max_points
            min_val = 0
            max_val = 10000
    else:
        # Demo mode or not connected - use the original code
        # Generate dummy data for demonstration
        current_time = datetime.now()
        x_data = [(current_time - timedelta(seconds=i)).strftime("%H:%M:%S") for i in range(max_points)]
        x_data.reverse()  # Put in chronological order
        
        # Demo mode - create realistic looking data
        if mode == "demo":
            if units == "capacity":
                # Base around 50,000 lbs/hr converted from kg
                base_value = convert_capacity_from_kg(50000 / 2.205, weight_pref)
            else:
                # Start with base value of 5000 objects per minute
                base_value = 5000
            
            # Create random variations around the base value
            np.random.seed(int(current_time.timestamp()) % 1000)  # Seed with current time for variety
            var_scale = 2000 if units == "capacity" else 1000
            variations = np.random.normal(0, var_scale, max_points)
            
            # Create a slightly rising trend
            trend = np.linspace(0, 15, max_points)  # Rising trend from 0 to 15
            
            # Add some cyclical pattern
            cycles = 10 * np.sin(np.linspace(0, 4*np.pi, max_points))  # Sine wave with amplitude 10
            
            # Combine base value, variations, trend, and cycles
            y_data = [max(0, base_value + variations[i] + trend[i] + cycles[i]) for i in range(max_points)]
            
            min_val = base_value * 0.8 if units == "capacity" else 3000
            max_val = max(y_data) * 1.1  # 10% headroom
        else:
            # Not connected - empty chart
            y_data = [None] * max_points
            min_val = 3000 if units != "capacity" else 0
            max_val = 10000
    
    # Create figure
    fig = go.Figure()
    
    # Add trace
    fig.add_trace(go.Scatter(
        x=x_data,
        y=y_data,
        mode='lines',
        name='Capacity' if units == "capacity" else 'Objects/Min',
        line=dict(color='#1f77b4', width=2)
    ))

    step = max(1, len(x_data) // 5)
    
    # Update layout
    fig.update_layout(
        title=None,
        xaxis=dict(
            showgrid=True,
            gridcolor='rgba(211,211,211,0.3)',
            tickmode='array',
            tickvals=list(range(0, len(x_data), step)),
            ticktext=[x_data[i] for i in range(0, len(x_data), step) if i < len(x_data)],
        ),
        yaxis=dict(
            title=None,
            showgrid=True,
            gridcolor='rgba(211,211,211,0.3)',
            range=[min_val, max_val]
        ),
        margin=dict(l=5, r=5, t=5, b=5),
        height=200,
        plot_bgcolor='var(--chart-bg)',
        paper_bgcolor='var(--chart-bg)',
        hovermode='closest',
        showlegend=False
    )
    
    # Include the historical indicator directly in the header so the
    # graph height remains unchanged when toggling modes.
    header = f"{section_title} (Historical View)" if mode == "historical" else section_title

    children = [
        dbc.Row([
            dbc.Col(html.H5(header, className="mb-0"), width=9),
            dbc.Col(
                dbc.Button(
                    "Units",
                    id={"type": "open-production-rate-units", "index": 0},
                    color="primary",
                    size="sm",
                    className="float-end",
                ),
                width=3,
            ),
        ], className="mb-2 align-items-center")
    ]



    children.append(
        dcc.Graph(
            id='trend-graph',
            figure=fig,
            config={'displayModeBar': False, 'responsive': True},
            style={'width': '100%', 'height': '100%'}
        )
    )

    return html.Div(children)


######BAR CHART###############
import math

# Initialize counter history with zeros so the dashboard starts from a
# predictable baseline instead of random demo data
previous_counter_values = [0] * 12

# Global variables for threshold settings

@app.callback(
    Output("alarm-data", "data"),
    [Input("status-update-interval", "n_intervals")],
    [State("app-state", "data")]
)
def update_alarms_store(n_intervals, app_state_data):
    """Update the alarms data store from the counter values and check for threshold violations"""
    global previous_counter_values, threshold_settings, threshold_violation_state
    
    # Get current time
    current_time = datetime.now()
    
    # Check for alarms
    alarms = []
    for i, value in enumerate(previous_counter_values):
        counter_num = i + 1
        
        # Safely check if counter_num exists in threshold_settings and is a dictionary
        if counter_num in threshold_settings and isinstance(threshold_settings[counter_num], dict):
            settings = threshold_settings[counter_num]
            violation = False
            is_high = False  # Track which threshold is violated (high or low)
            
            # Check for threshold violations
            if 'min_enabled' in settings and settings['min_enabled'] and value < settings['min_value']:
                violation = True
                alarms.append(f"Sens. {counter_num} below min threshold")
            elif 'max_enabled' in settings and settings['max_enabled'] and value > settings['max_value']:
                violation = True
                is_high = True
                alarms.append(f"Sens. {counter_num} above max threshold")
            
            # Get violation state for this counter
            violation_state = threshold_violation_state[counter_num]
            
            # If email notifications are enabled
            if threshold_settings.get('email_enabled', False):
                email_minutes = threshold_settings.get('email_minutes', 2)
                
                # If now violating but wasn't before
                if violation and not violation_state['is_violating']:
                    # Start tracking this violation
                    violation_state['is_violating'] = True
                    violation_state['violation_start_time'] = current_time
                    violation_state['email_sent'] = False
                    logger.info(f"Started tracking threshold violation for Sensitivity {counter_num}")
                
                # If still violating
                elif violation and violation_state['is_violating']:
                    # Check if it's been violating long enough to send an email
                    if not violation_state['email_sent']:
                        time_diff = (current_time - violation_state['violation_start_time']).total_seconds()
                        if time_diff >= (email_minutes * 60):
                            # Send the email
                            email_sent = send_threshold_email(counter_num, is_high)
                            if email_sent:
                                violation_state['email_sent'] = True
                                logger.info(f"Sent threshold violation email for Sensitivity {counter_num}")
                
                # If no longer violating
                elif not violation and violation_state['is_violating']:
                    # Reset the violation state
                    violation_state['is_violating'] = False
                    violation_state['violation_start_time'] = None
                    violation_state['email_sent'] = False
                    logger.info(f"Reset threshold violation for Sensitivity {counter_num}")
    
    return {"alarms": alarms}


# Update the section 5-2 callback to include the threshold settings button and modal
@app.callback(
    Output("section-5-2", "children"),
    [Input("status-update-interval", "n_intervals"),
     Input("current-dashboard",       "data"),
    Input("historical-time-index",   "data"),
    Input("historical-data-cache",   "data")],
    [State("app-state", "data"),
     State("app-mode", "data"),
     State("active-machine-store", "data"),
     State("language-preference-store", "data")],
    prevent_initial_call=True
)
def update_section_5_2(n_intervals, which, state_data, historical_data, app_state_data, app_mode, active_machine_data, lang):
    """Update section 5-2 with bar chart for counter values and update alarm data"""
    
    # only run when we’re in the “main” dashboard
    if which != "main":
        raise PreventUpdate
        # or return [no_update, no_update]
    global previous_counter_values, threshold_settings

    # Ensure we have a full set of values to work with
    if not previous_counter_values or len(previous_counter_values) < 12:
        previous_counter_values = [0] * 12
    
    # Define title for the section
    section_title = tr("sensitivity_rates_title", lang)
    
    # Define pattern for tag names in live mode
    TAG_PATTERN = "Status.ColorSort.Sort1.DefectCount{}.Rate.Current"
    
    # Define colors for each primary/counter number
    counter_colors = {
        1: "green",       # Blue
        2: "lightgreen",      # Green
        3: "orange",     # Orange
        4: "blue",      # Black
        5: "#f9d70b",    # Yellow (using hex to ensure visibility)
        6: "magenta",    # Magenta
        7: "cyan",       # Cyan
        8: "red",        # Red
        9: "purple",
        10: "brown",
        11: "gray",
        12: "lightblue"
    }
    
    # Get mode (live, demo, or historical)
    mode = "demo"  # Default to demo mode
    if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
        mode = app_mode["mode"]
    
    # Generate values based on mode
    if mode == "historical":
        hours = state_data.get("hours", 24) if isinstance(state_data, dict) else 24
        active_id = active_machine_data.get("machine_id") if active_machine_data else None
        historical_data = (
            historical_data
            if isinstance(historical_data, dict) and 1 in historical_data
            else get_historical_data(timeframe=f"{hours}h", machine_id=active_id)
        )
        
        # Use the average value for each counter over the timeframe
        new_counter_values = []
        for i in range(1, 13):
            vals = historical_data[i]["values"]
            if vals:
                avg_val = sum(vals) / len(vals)
                new_counter_values.append(avg_val)
            else:
                new_counter_values.append(50)

        # Store the new values for the next update
        previous_counter_values = new_counter_values.copy()
        logger.info(f"Section 5-2 values (historical mode): {new_counter_values}")
    elif mode == "live" and app_state_data.get("connected", False):
        # Live mode: get values from OPC UA
        # Use the tag pattern provided for each counter
        new_counter_values = []
        for i in range(1, 13):
            # Construct the tag name using the provided pattern
            tag_name = TAG_PATTERN.format(i)

            # Check if the tag exists
            if tag_name in app_state.tags:
                value = app_state.tags[tag_name]["data"].latest_value
                if value is None:
                    # If tag exists but value is None, keep previous value
                    value = previous_counter_values[i-1]
                new_counter_values.append(value)
            else:
                # Tag not found - keep previous value
                new_counter_values.append(previous_counter_values[i-1])

        # Store the new values for the next update
        previous_counter_values = new_counter_values.copy()
        logger.info(f"Section 5-2 values (live mode): {new_counter_values}")
    elif mode == "demo":
        # Demo mode: generate synthetic values
        new_counter_values = []
        for i, prev_value in enumerate(previous_counter_values):
            # Determine maximum change (up to ±20)
            max_change = min(20, prev_value - 10)  # Ensure we don't go below 10

            # Fix: Convert max_change to an integer
            max_change_int = int(max_change)

            # Use the integer version in randint
            change = random.randint(-max_change_int, 20)

            # Calculate new value with bounds
            new_value = max(10, min(180, prev_value + change))

            # Add to the list
            new_counter_values.append(new_value)

        # Store the new values for the next update
        previous_counter_values = new_counter_values.copy()
        logger.info(f"Section 5-2 values (demo mode): {new_counter_values}")
    else:
        # Live mode but not connected - keep the last values
        new_counter_values = previous_counter_values.copy()
        logger.info("Section 5-2 values (disconnected): using previous values")
    
    # Create counter names
    counter_names = [f"{i}" for i in range(1, 13)]
    
    # Create figure with our data
    fig = go.Figure()
    
    # Use a single bar trace with all data
    fig.add_trace(go.Bar(
        x=counter_names,  # Use all counter names as x values
        y=new_counter_values,  # Use all counter values as y values
        marker_color=[counter_colors.get(i, 'gray') for i in range(1, 13)],  # Set colors per bar
        hoverinfo='text',  # Keep hover info
    hovertext=[f"Sensitivity {i}: {new_counter_values[i-1]:.2f}" for i in range(1, 13)]  # Custom hover text with 2 decimal places

    ))
    
    # Add horizontal min threshold lines for each counter if enabled
    for i, counter in enumerate(counter_names):
        counter_num = i + 1
        # Check if counter_num exists in threshold_settings and is a dictionary
        if counter_num in threshold_settings and isinstance(threshold_settings[counter_num], dict):
            settings = threshold_settings[counter_num]
            
            if 'min_enabled' in settings and settings['min_enabled']:
                fig.add_shape(
                    type="line",
                    x0=i - 0.4,  # Start slightly before the bar
                    x1=i + 0.4,  # End slightly after the bar
                    y0=settings['min_value'],
                    y1=settings['min_value'],
                    line=dict(
                        color="black",
                        width=2,
                        dash="solid",
                    ),
                )
    
    # Add horizontal max threshold lines for each counter if enabled
    for i, counter in enumerate(counter_names):
        counter_num = i + 1
        # Check if counter_num exists in threshold_settings and is a dictionary
        if counter_num in threshold_settings and isinstance(threshold_settings[counter_num], dict):
            settings = threshold_settings[counter_num]
            
            if 'max_enabled' in settings and settings['max_enabled']:
                fig.add_shape(
                    type="line",
                    x0=i - 0.4,  # Start slightly before the bar
                    x1=i + 0.4,  # End slightly after the bar
                    y0=settings['max_value'],
                    y1=settings['max_value'],
                    line=dict(
                        color="red",
                        width=2,
                        dash="solid",
                    ),
                )
    
    # Calculate max value for y-axis scaling (with 10% headroom)
    # Include enabled thresholds in calculation
    all_values = new_counter_values.copy()
    for counter_num, settings in threshold_settings.items():
        # Only process if counter_num is an integer and settings is a dictionary
        if isinstance(counter_num, int) and isinstance(settings, dict):
            if 'max_enabled' in settings and settings['max_enabled']:
                all_values.append(settings['max_value'])
    
    max_value = max(all_values) if all_values else 100
    y_max = max(100, max_value * 1.1)  # At least 100, or 10% higher than max value
    
    # Update layout
    fig.update_layout(
        title=None,
        xaxis=dict(
            title=None,
            showgrid=False,
            tickangle=0,
        ),
        yaxis=dict(
            title=None,
            showgrid=True,
            gridcolor='rgba(211,211,211,0.3)',
            range=[0, y_max]  # Dynamic range based on data and thresholds
        ),
        margin=dict(l=5, r=5, t=0, b=20),  # Increased bottom margin for rotated labels
        height=198,  # Increased height since we have more space now
        plot_bgcolor='var(--chart-bg)',
        paper_bgcolor='var(--chart-bg)',
        showlegend=False,
    )
    
    # Create the section content
    section_content = html.Div([
        # Header row with title and settings button
        dbc.Row([
            dbc.Col(html.H5(section_title + (" (Historical)" if mode == "historical" else ""), className="mb-0"), width=9),
            dbc.Col(
                dbc.Button("Thresholds", 
                        id={"type": "open-threshold", "index": 0},
                        color="primary", 
                        size="sm",
                        className="float-end"),
                width=3
            )
        ], className="mb-2 align-items-center"),
        
        # Bar chart
        dcc.Graph(
            id='counter-bar-chart',
            figure=fig,
            config={'displayModeBar': False, 'responsive': True},
            style={'width': '100%', 'height': '100%'}
        )
    ])
    
    # Return the section content
    return section_content


############################### Sensitivity Trend Graph########################
@app.callback(

    Output("section-6-1", "children"),
    [Input("status-update-interval", "n_intervals"),
     Input("current-dashboard",       "data"),
    Input("historical-time-index",   "data")],
    [State("app-state", "data"),
     State("app-mode", "data"),
     State("active-machine-store", "data"),
     State("language-preference-store", "data")],
    prevent_initial_call=True
)
def update_section_6_1(n_intervals, which, state_data, app_state_data, app_mode, active_machine_data, lang):

    """Update section 6-1 with trend graph for the 12 counters, supporting historical data"""
    # only run when we’re in the “main” dashboard
    if which != "main":
        raise PreventUpdate
        # or return [no_update, no_update]
    global previous_counter_values, display_settings

    # Ensure baseline values exist for the trend graph
    if not previous_counter_values or len(previous_counter_values) < 12:
        previous_counter_values = [0] * 12
    
    # Define title for the section
    section_title = tr("counter_values_trend_title", lang)
    
    # Define colors for each counter - matching section 5-2
    counter_colors = {
        1: "green",       # Blue
        2: "lightgreen",      # Green
        3: "orange",     # Orange
        4: "blue",      # Black
        5: "#f9d70b",    # Yellow (using hex to ensure visibility)
        6: "magenta",    # Magenta
        7: "cyan",       # Cyan
        8: "red",        # Red
        9: "purple",
        10: "brown",
        11: "gray",
        12: "lightblue"
    }
    
    # Determine if we're in Live, Demo, or Historical mode
    mode = "demo"  # Default to demo mode
    if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
        mode = app_mode["mode"]
    
    # If in historical mode, load data from file instead of using app_state

    if mode == "historical":
        # Load historical data for the selected timeframe
        hours = state_data.get("hours", 24) if isinstance(state_data, dict) else 24
        active_id = active_machine_data.get("machine_id") if active_machine_data else None
        historical_data = get_historical_data(timeframe=f"{hours}h", machine_id=active_id)

        
        # Create figure
        fig = go.Figure()
        
        # Add a trace for each counter based on display settings
        for i in range(1, 13):
            # Only add trace if this counter is set to be displayed
            if display_settings.get(i, True):  # Default to True if not in settings
                counter_name = f"Counter {i}"
                color = counter_colors.get(i, "gray")
                
                # Get historical data for this counter
                times = historical_data[i]['times']
                values = historical_data[i]['values']
                
                # Format times for display
                time_labels = [t.strftime("%H:%M:%S") if isinstance(t, datetime) else t for t in times]
                
                # Add trace if we have data
                if times and values:
                    fig.add_trace(go.Scatter(
                        x=time_labels,
                        y=values,
                        mode='lines',
                        name=counter_name,
                        line=dict(color=color, width=2),
                        hoverinfo='text',
                        hovertext=[f"{counter_name}: {value}" for value in values]
                    ))
        
        # Determine tick labels similar to live mode
        ref_times = historical_data[1]['times'] if historical_data[1]['times'] else []
        label_list = [t.strftime('%H:%M:%S') if isinstance(t, datetime) else t for t in ref_times]
        step = max(1, len(label_list) // 5) if label_list else 1

        hist_values = [historical_data[i]['values'][-1] if historical_data[i]['values'] else None for i in range(1, 13)]
        logger.info(f"Section 6-1 latest values (historical mode): {hist_values}")

        # Update layout with timeline slider for historical data
        fig.update_layout(
            title=None,
            xaxis=dict(
                showgrid=False,
                gridcolor='rgba(211,211,211,0.3)',
                rangeslider=dict(visible=False),  # Add range slider for historical data
                tickmode='array',
                tickvals=list(range(0, len(label_list), step)) if label_list else [],
                ticktext=[label_list[i] for i in range(0, len(label_list), step) if i < len(label_list)] if label_list else [],
            ),
            yaxis=dict(
                title=None,
                showgrid=False,
                gridcolor='rgba(211,211,211,0.3)',
            ),
            margin=dict(l=5, r=5, t=5, b=5),
            # Match the live mode graph height so the component fits
            # within the fixed card dimensions when viewing
            # historical data.
            height=200,
            plot_bgcolor='var(--chart-bg)',
            paper_bgcolor='var(--chart-bg)',
            hovermode='closest',
            showlegend=False
        )
        
        # Return the section content.  The header indicates historical
        # mode so no extra indicator is needed.
        return html.Div([
            # Header row with title and display settings button
            dbc.Row([
                dbc.Col(html.H5(f"{section_title} (Historical View)", className="mb-0"), width=9),
                dbc.Col(
                    dbc.Button("Display", 
                            id={"type": "open-display", "index": 0},
                            color="primary", 
                            size="sm",
                            className="float-end"),
                    width=3
                )
            ], className="mb-2 align-items-center"),
            # Trend graph with range slider
            dcc.Graph(
                id='counter-trend-graph',
                figure=fig,
                config={'displayModeBar': False, 'responsive': True},
                style={'width': '100%', 'height': '100%'}
            )
        ])
    
    else:
        # Initialize trend data if it doesn't exist in app state
        if not hasattr(app_state, 'counter_history'):
            app_state.counter_history = {i: {'times': [], 'values': []} for i in range(1, 13)}
        
        # Get current time
        current_time = datetime.now()
        
        # Update the trend data with current values
        if mode == "live" and app_state_data.get("connected", False):
            # Use the latest values from section 5-2 for consistency
            for i, value in enumerate(previous_counter_values):
                counter_num = i + 1

                # Add current value to history
                app_state.counter_history[counter_num]['times'].append(current_time)
                app_state.counter_history[counter_num]['values'].append(value)
        elif mode == "demo":
            # Demo mode - use previous_counter_values
            for i, value in enumerate(previous_counter_values):
                counter_num = i + 1

                # Add current value to history
                app_state.counter_history[counter_num]['times'].append(current_time)
                app_state.counter_history[counter_num]['values'].append(value)
        else:
            # Live mode but not connected - keep previous values
            for i in range(1, 13):
                prev_vals = app_state.counter_history[i]['values']
                prev_value = prev_vals[-1] if prev_vals else 0
                app_state.counter_history[i]['times'].append(current_time)
                app_state.counter_history[i]['values'].append(prev_value)

        # Limit history size for all counters
        for i in range(1, 13):
            # Limit history size to prevent memory issues (keep last 120 points)
            max_points = 120
            if len(app_state.counter_history[i]['times']) > max_points:
                app_state.counter_history[i]['times'] = app_state.counter_history[i]['times'][-max_points:]
                app_state.counter_history[i]['values'] = app_state.counter_history[i]['values'][-max_points:]

        latest_values = [app_state.counter_history[i]['values'][-1] if app_state.counter_history[i]['values'] else None for i in range(1, 13)]
        logger.info(f"Section 6-1 latest values ({mode} mode): {latest_values}")
        
        # Create figure
        fig = go.Figure()
        
        # Add a trace for each counter based on display settings
        for i in range(1, 13):
            # Only add trace if this counter is set to be displayed
            if display_settings.get(i, True):  # Default to True if not in settings
                counter_name = f"Counter {i}"
                color = counter_colors.get(i, "gray")
                
                # Get time and value data
                times = app_state.counter_history[i]['times']
                values = app_state.counter_history[i]['values']
                
                # Format times for display
                time_labels = [t.strftime("%H:%M:%S") for t in times]
                
                # Add trace if we have data
                if times and values:
                    fig.add_trace(go.Scatter(
                        x=time_labels,
                        y=values,
                        mode='lines',
                        name=counter_name,
                        line=dict(color=color, width=2),
                        hoverinfo='text',
                        hovertext=[f"{counter_name}: {value}" for value in values]
                    ))
        
        # Update layout
        fig.update_layout(
            title=None,
            xaxis=dict(
                showgrid=False,
                gridcolor='rgba(211,211,211,0.3)',
                tickmode='array',
                tickvals=list(range(0, len(time_labels), max(1, len(time_labels) // 5))) if time_labels else [],
                ticktext=[time_labels[i] for i in range(0, len(time_labels), 
                                                            max(1, len(time_labels) // 5))
                        if i < len(time_labels)] if time_labels else [],
            ),
            yaxis=dict(
                title=None,
                showgrid=False,
                gridcolor='rgba(211,211,211,0.3)',
            ),
            margin=dict(l=5, r=5, t=5, b=5),
            height=200,
            plot_bgcolor='var(--chart-bg)',
            paper_bgcolor='var(--chart-bg)',
            hovermode='closest',
            showlegend=False
        )
        
        # Return the section content
        return html.Div([
            # Header row with title and display settings button
            dbc.Row([
                dbc.Col(html.H5(section_title, className="mb-0"), width=9),
                dbc.Col(
                    dbc.Button("Display", 
                            id={"type": "open-display", "index": 0},
                            color="primary", 
                            size="sm",
                            className="float-end"),
                    width=3
                )
            ], className="mb-2 align-items-center"),
            
            # Trend graph
            dcc.Graph(
                id='counter-trend-graph',
                figure=fig,
                config={'displayModeBar': False, 'responsive': True},
                style={'width': '100%', 'height': '100%'}
            )
        ])
@app.callback(
    Output("section-6-2", "children"),
    [Input("alarm-data", "data"),
     Input("current-dashboard",       "data"),
     Input("status-update-interval", "n_intervals")],
    [State("language-preference-store", "data")],
    prevent_initial_call=True
)
def update_section_6_2(alarm_data,which, n_intervals, lang):
    """Update section 6-2 with alarms display in two columns"""
     # only run when we’re in the “main” dashboard
    if which != "main":
        raise PreventUpdate
        # or return [no_update, no_update]
    # Set title for the section
    section_title = tr("sensitivity_threshold_alarms_title", lang)
    
    # Get alarms from the data store
    alarms = alarm_data.get("alarms", []) if alarm_data else []

    def _translate_alarm(alarm):
        if alarm.startswith("Sens."):
            parts = alarm.split()
            if len(parts) >= 3:
                num = parts[1]
                if "below" in alarm:
                    return tr("sensitivity_below_min", lang).format(num=num)
                elif "above" in alarm:
                    return tr("sensitivity_above_max", lang).format(num=num)
        return alarm

    translated_alarms = [_translate_alarm(a) for a in alarms]
    
    # Create alarm display with two columns
    if alarms:
        # Split alarms into two columns
        mid_point = len(alarms) // 2 + len(alarms) % 2  # Ceiling division to balance columns
        left_alarms = translated_alarms[:mid_point]
        right_alarms = translated_alarms[mid_point:]
        
        # Create left column items
        left_items = [html.Li(alarm, className="text-danger mb-1") for alarm in left_alarms]
        
        # Create right column items
        right_items = [html.Li(alarm, className="text-danger mb-1") for alarm in right_alarms]
        
        # Create two-column layout
        alarm_display = html.Div([
            html.Div(tr("active_alarms_title", lang), className="fw-bold text-danger mb-2"),
            dbc.Row([
                # Left column
                dbc.Col(
                    html.Ul(left_items, className="ps-3 mb-0"),
                    width=6
                ),
                # Right column
                dbc.Col(
                    html.Ul(right_items, className="ps-3 mb-0"),
                    width=6
                ),
            ]),
        ])
    else:
        # No alarms display
        alarm_display = html.Div([
            html.Div("No active alarms", className="text-success")
        ])
    
    # Return the section content with fixed height
    return html.Div([
        html.H5(section_title, className="text-center mb-2"),
        
        # Alarms display with fixed height
        dbc.Card(
            dbc.CardBody(
                alarm_display, 
                className="p-2 overflow-auto",  # Add overflow-auto for scrolling if needed
                # Scale alarm display height with viewport
                style={"height": "205px"}
            ),
            className="h-100"
        ),
        
        # Timestamp
        
    ])

@app.callback(
    Output("section-7-1", "children"),
    [Input("status-update-interval", "n_intervals"),
     Input("current-dashboard",       "data"),],
    [State("app-state", "data"),
     State("app-mode", "data"),
     State("language-preference-store", "data")],
    prevent_initial_call=True
)
def update_section_7_1(n_intervals,which, app_state_data, app_mode, lang):
    """Update section 7-1 with air pressure gauge"""
    # only run when we’re in the “main” dashboard
    if which != "main":
        raise PreventUpdate
        # or return [no_update, no_update]

    # Tag definition for air pressure - Easy to update when actual tag name is available
    AIR_PRESSURE_TAG = "Status.Environmental.AirPressurePsi"
    
    # Define gauge configuration
    min_pressure = 0
    max_pressure = 100
    
    # Define color ranges for gauge based on requirements
    red_range_low = [0, 30]       # Critical low range
    yellow_range = [31, 50]       # Warning range
    green_range = [51, 75]        # Normal range
    red_range_high = [76, 100]    # Critical high range
    
    # Determine if we're in Live or Demo mode
    mode = "demo"  # Default to demo mode
    if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
        mode = app_mode["mode"]
    
    # Get air pressure value based on mode
    if mode == "live" and app_state_data.get("connected", False):
        # Live mode: get value from OPC UA tag
        if AIR_PRESSURE_TAG in app_state.tags:
            # Read the actual value from the tag
            air_pressure = (app_state.tags[AIR_PRESSURE_TAG]["data"].latest_value)/100
            if air_pressure is None:
                air_pressure = 0  # Default to 0 if tag exists but value is None
        else:
            # Tag not found, use 0 as per requirement
            air_pressure = 0
    else:
        # Demo mode: generate a realistic air pressure value with limited variation
        # Use timestamp for some variation in the demo
        timestamp = int(datetime.now().timestamp())
        
        # Generate value that stays very close to 65 PSI (±3 PSI maximum variation)
        base_value = 65  # Base in middle of green range
        # Use a small sine wave variation (±3 PSI max)
        variation = 3 * math.sin(timestamp / 10)  # Limited to ±3 PSI
        air_pressure = base_value + variation
    
    # Determine indicator color based on pressure value
    if 0 <= air_pressure <= 30:
        indicator_color = "red"
        status_text = "Critical Low"
        status_color = "danger"
    elif 31 <= air_pressure <= 50:
        indicator_color = "yellow"
        status_text = "Warning Low"
        status_color = "warning"
    elif 51 <= air_pressure <= 75:
        indicator_color = "green"
        status_text = "Normal"
        status_color = "success"
    else:  # 76-100
        indicator_color = "red"
        status_text = "Critical High"
        status_color = "danger"
    
    # Create the gauge figure
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=air_pressure,
        domain={'x': [0, 1], 'y': [0, 1]},
        #title={'text': "Air Pressure", 'font': {'size': 14}},
        gauge={
            'axis': {'range': [min_pressure, max_pressure], 'tickwidth': 1, 'tickcolor': "darkblue"},
            'bar': {'color': indicator_color},  # Use dynamic color based on value
            'bgcolor': "#d3d3d3",  # Light grey background
            'borderwidth': 2,
            'bordercolor': "gray",
            'threshold': {
                'line': {'color': "darkgray", 'width': 4},
                'thickness': 0.75,
                'value': air_pressure
            }
        }
    ))
    
    # Update layout for the gauge
    fig.update_layout(
        height=200,
        margin=dict(l=10, r=10, t=30, b=10),
        paper_bgcolor='var(--chart-bg)',  # Use grey paper background
        plot_bgcolor='var(--chart-bg)',   # Use grey plot background
        font={'color': "darkblue", 'family': "Arial"}
    )
    
    return html.Div([
        html.H5(tr("air_pressure_title", lang), className="text-left mb-1"),
        # Gauge chart
        dcc.Graph(
            figure=fig,
            config={'displayModeBar': False, 'responsive': True},
            style={'width': '100%', 'height': '100%'}
        ),
        
        # Status text below the gauge
        #html.Div([
        #    html.Span("Status: ", className="fw-bold me-1"),
        #    html.Span(status_text, className=f"text-{status_color}")
        #], className="text-center mt-2")
    ])

# Callback for section 7-2
@app.callback(
    Output("section-7-2", "children"),
    [Input("status-update-interval", "n_intervals"),
     Input("current-dashboard",       "data"),
     Input("historical-time-index",   "data")],
    [State("app-state", "data"),
     State("app-mode", "data"),
     State("active-machine-store", "data"),
     State("language-preference-store", "data")],
    prevent_initial_call=True
)
def update_section_7_2(n_intervals, which, time_state, app_state_data, app_mode, active_machine_data, lang):
    """Update section 7-2 with Machine Control Log"""
    # only run when we’re in the “main” dashboard
    if which != "main":
        raise PreventUpdate
        # or return [no_update, no_update]
    global prev_values, prev_active_states, machine_control_log

    machine_id = active_machine_data.get("machine_id") if active_machine_data else None

    # Determine current mode (live or demo)
    mode = "demo"
    if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
        mode = app_mode["mode"]

    # Live monitoring of feeder rate tags
    if mode == "live" and app_state_data.get("connected", False):
        machine_prev = prev_values[machine_id]
        for opc_tag, friendly_name in MONITORED_RATE_TAGS.items():
            if opc_tag in app_state.tags:
                new_val = app_state.tags[opc_tag]["data"].latest_value
                prev_val = machine_prev.get(opc_tag)
                if prev_val is not None and new_val is not None and new_val != prev_val:
                    add_control_log_entry(friendly_name, prev_val, new_val, machine_id=machine_id)
                machine_prev[opc_tag] = new_val

        machine_prev_active = prev_active_states[machine_id]
        for opc_tag, sens_num in SENSITIVITY_ACTIVE_TAGS.items():
            if opc_tag in app_state.tags:
                new_val = app_state.tags[opc_tag]["data"].latest_value
                prev_val = machine_prev_active.get(opc_tag)
                if prev_val is not None and new_val is not None and bool(new_val) != bool(prev_val):
                    add_activation_log_entry(sens_num, bool(new_val), machine_id=machine_id)
                machine_prev_active[opc_tag] = new_val
    
    # Create the log entries display - with even more compact styling
    log_entries = []

    # Determine which log to display based on mode
    display_log = machine_control_log
    if mode == "historical":
        hours = time_state.get("hours", 24) if isinstance(time_state, dict) else 24

        machine_id = active_machine_data.get("machine_id") if active_machine_data else None
        display_log = get_historical_control_log(timeframe=hours, machine_id=machine_id)
        display_log = sorted(display_log, key=lambda e: e.get("timestamp"), reverse=True)
    elif mode == "live":
        display_log = [
            e for e in machine_control_log
            if not e.get("demo") and e.get("machine_id") == machine_id
        ]

    # newest entries first
    display_log = display_log[:20]

    for idx, entry in enumerate(display_log, start=1):
        timestamp = entry.get("display_timestamp")
        if not timestamp:
            ts = entry.get("timestamp")
            if isinstance(ts, datetime):
                timestamp = ts.strftime("%Y-%m-%d %H:%M:%S")
            elif ts:
                timestamp = str(ts)
            else:
                t = entry.get("time")
                if isinstance(t, datetime):
                    timestamp = t.strftime("%Y-%m-%d %H:%M:%S")
                elif t:
                    timestamp = str(t)
                else:
                    timestamp = ""

        def _translate_tag(tag):
            if tag.startswith("Sens "):
                parts = tag.split()
                if len(parts) >= 2:
                    return f"{tr('sensitivity_label', lang)} {parts[1]}"
            if tag.startswith("Feeder") or tag.startswith("Feed"):
                parts = tag.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return tr(f"feeder_{parts[1]}", lang)
                return tr('feeder_label', lang).rstrip(':')
            return tag

        tag_translated = _translate_tag(entry.get('tag', ''))

        if entry.get("icon"):
            color_class = "text-success" if entry.get("action") == "Enabled" else "text-danger"
            icon = html.Span(entry.get("icon"), className=color_class)
            log_entries.append(
                html.Div(
                    [f"{idx}. {tag_translated} {entry.get('action')} ", icon, f" {timestamp}"],
                    className="mb-1 small",
                    style={"whiteSpace": "nowrap"},
                )
            )
        else:
            description = f"{tag_translated} {entry.get('action', '')}".strip()
            value_change = f"{entry.get('old_value', '')} -> {entry.get('new_value', '')}"
            log_entries.append(
                html.Div(
                    f"{idx}. {description} {value_change} {timestamp}",
                    className="mb-1 small",
                    style={"whiteSpace": "nowrap"}
                )
            )

    # If no entries, show placeholder
    if not log_entries:
        log_entries.append(
            html.Div(tr("no_changes_yet", lang), className="text-center text-muted py-1")
        )

    # Return the section content with title
    return html.Div(
        [html.H5(tr("machine_control_log_title", lang), className="text-left mb-1"), *log_entries],
        className="overflow-auto px-0",
        # Use flexbox so this log grows with available space
        style={"flex": "1"}
    )



# Function to create display settings form
def create_display_settings_form():
    """Create a form for display settings"""
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
                        f"Sensitivity {i}:", 
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
                        label="Display",
                    ),
                    width=8
                ),
            ], className="mb-2")
        )
    
    # Add header
    header = html.Div(
        "Select which counter traces to display:",
        className="mb-3 fw-bold"
    )
    
    # Return the form with header
    return html.Div([header] + form_items)

@app.callback(
    [Output("historical-time-index", "data"),
     Output("historical-time-display", "children"),
     Output("historical-data-cache", "data")],
    [Input("historical-time-slider", "value"),
     Input("mode-selector", "value")],
    [State("active-machine-store", "data")],
    prevent_initial_call=True
)
def update_historical_time_and_display(slider_value, mode, active_machine_data):
    """Return the chosen historical range, display text, and cached data."""
    if mode != "historical":
        return dash.no_update, "", dash.no_update

    # Load filtered historical data for the selected timeframe so the graphs
    # update immediately when the slider changes
    machine_id = active_machine_data.get("machine_id") if active_machine_data else None
    historical_data = load_historical_data(f"{slider_value}h", machine_id=machine_id)

    # Use counter 1 as the reference for the time axis.  If data exists, format
    # the first timestamp for display to indicate the starting point.
    ref_counter = 1
    timestamp_str = ""
    if (ref_counter in historical_data and
            historical_data[ref_counter]['times']):
        first_ts = historical_data[ref_counter]['times'][0]
        if isinstance(first_ts, datetime):
            timestamp_str = first_ts.strftime("%H:%M")
        else:
            timestamp_str = str(first_ts)

    display_text = f"Showing last {slider_value} hours"
    if timestamp_str:
        display_text += f" starting {timestamp_str}"


    # Return the selected timeframe, display text, and cached data
    return {"hours": slider_value}, display_text, historical_data

@app.callback(
    Output("historical-time-controls", "className"),
    [Input("mode-selector", "value")],
    prevent_initial_call=True
)
def toggle_historical_controls_visibility(mode):
    """Show/hide historical controls based on selected mode"""
    if mode == "historical":
        return "d-block"  # Show controls
    else:
        return "d-none"  # Hide controls


# Callback to open/close the display settings modal and save settings
@app.callback(
    [Output("display-modal", "is_open"),
     Output("display-form-container", "children")],
    [Input({"type": "open-display", "index": ALL}, "n_clicks"),
     Input("close-display-settings", "n_clicks"),
     Input("save-display-settings", "n_clicks")],
    [State("display-modal", "is_open"),
     State({"type": "display-enabled", "index": ALL}, "value")],
    prevent_initial_call=True
)
def toggle_display_modal(open_clicks, close_clicks, save_clicks, is_open, display_enabled_values):
    """Handle opening/closing the display settings modal and saving settings"""
    global display_settings
    
    ctx = callback_context
    
    # Check if callback was triggered
    if not ctx.triggered:
        return no_update, no_update
    
    # Get the property that triggered the callback
    trigger_prop_id = ctx.triggered[0]["prop_id"]
    
    # Check for open button clicks (with pattern matching)
    if '"type":"open-display"' in trigger_prop_id:
        # Check if any button was actually clicked (not initial state)
        if any(click is not None for click in open_clicks):
            return True, create_display_settings_form()
    
    # Check for close button click
    elif trigger_prop_id == "close-display-settings.n_clicks":
        # Check if button was actually clicked (not initial state)
        if close_clicks is not None:
            return False, no_update
    
    # Check for save button click
    elif trigger_prop_id == "save-display-settings.n_clicks":
        # Check if button was actually clicked (not initial state)
        if save_clicks is not None and display_enabled_values:
            # Safety check: make sure we have the right number of values
            if len(display_enabled_values) == 12:  # We expect 12 counters
                # Update the display settings
                for i in range(len(display_enabled_values)):
                    counter_num = i + 1
                    display_settings[counter_num] = display_enabled_values[i]
                
                # Save settings to file
                save_success = save_display_settings(display_settings)
                if save_success:
                    logger.info("Display settings saved successfully")
                else:
                    logger.warning("Failed to save display settings")
            else:
                logger.warning(f"Unexpected number of display values: {len(display_enabled_values)}")
            
            # Close modal
            return False, create_display_settings_form()
    
    # Default case - don't update anything
    return no_update, no_update

# Callback to open/close the production rate units modal
@app.callback(
    [Output("production-rate-units-modal", "is_open"),
     Output("production-rate-unit", "data")],
    [Input({"type": "open-production-rate-units", "index": ALL}, "n_clicks"),
     Input("close-production-rate-units", "n_clicks"),
     Input("save-production-rate-units", "n_clicks")],
    [State("production-rate-units-modal", "is_open"),
     State("production-rate-unit-selector", "value")],
    prevent_initial_call=True,
)
def toggle_production_rate_units_modal(open_clicks, close_clicks, save_clicks, is_open, selected):
    """Show or hide the units selection modal and save the chosen unit."""
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update

    trigger = ctx.triggered[0]["prop_id"]
    if '"type":"open-production-rate-units"' in trigger:
        if any(click is not None for click in open_clicks):
            return True, dash.no_update
    elif trigger == "close-production-rate-units.n_clicks":
        if close_clicks is not None:
            return False, dash.no_update
    elif trigger == "save-production-rate-units.n_clicks":
        if save_clicks is not None:
            return False, selected

    return no_update, no_update

# Callback to process the uploaded image
@app.callback(
    [Output("additional-image-store", "data"),
     Output("upload-status", "children")],
    [Input("upload-image", "contents")],
    [State("upload-image", "filename")]
)
def process_uploaded_image(contents, filename):
    """Process the uploaded image and store it"""
    if contents is None:
        return dash.no_update, dash.no_update
    
    try:
        # Log the content for debugging
        logger.info(f"Processing image upload: {filename}")
        
        # Store the image content in the data store
        new_data = {"image": contents}
        
        # Save the image for persistence
        save_success = save_uploaded_image(contents)
        
        # Log the result for debugging
        logger.info(f"Image save result: {save_success}")
        
        return new_data, html.Div(f"Uploaded: {filename}", className="text-success")
    except Exception as e:
        logger.error(f"Error uploading image: {str(e)}")
        return dash.no_update, html.Div(f"Error uploading image: {str(e)}", className="text-danger")


# Callback to open/close the Update Counts modal
@app.callback(
    Output("update-counts-modal", "is_open"),
    [Input("open-update-counts", "n_clicks"),
     Input("close-update-counts", "n_clicks"),
     Input("save-count-settings", "n_clicks")],
    [State("update-counts-modal", "is_open")],
    prevent_initial_call=True,
)
def toggle_update_counts_modal(open_click, close_click, save_click, is_open):
    ctx = callback_context
    if not ctx.triggered:
        return dash.no_update

    trigger = ctx.triggered[0]["prop_id"]
    if trigger == "open-update-counts.n_clicks" and open_click:
        return True
    elif trigger == "close-update-counts.n_clicks" and close_click:
        return False
    elif trigger == "save-count-settings.n_clicks" and save_click:
        return False

    return is_open

@app.callback(
    [Output("app-mode", "data"),
     Output("historical-time-slider", "value")],
    [Input("mode-selector", "value")],
    prevent_initial_call=False
)
def update_app_mode(mode):
    """Update the application mode (live, demo, or historical)"""
    # Reset historical slider to most recent when switching to historical mode
    slider_value = 24 if mode == "historical" else dash.no_update

    # Log the new mode for debugging unexpected switches
    logger.info(f"App mode updated to '{mode}'")

    return {"mode": mode}, slider_value


# ---------------------------------------------------------------------------
# Keep a global copy of the current application mode
# ---------------------------------------------------------------------------
@app.callback(Output("app-mode-tracker", "data"), Input("app-mode", "data"))
def _track_app_mode(data):
    """Synchronize ``current_app_mode`` with the ``app-mode`` store."""
    global current_app_mode
    if isinstance(data, dict) and "mode" in data:
        current_app_mode = data["mode"]
    return dash.no_update

@app.callback(
    [Output("threshold-modal", "is_open")],  # Changed this to remove the second output
    [Input({"type": "open-threshold", "index": ALL}, "n_clicks"),
     Input("close-threshold-settings", "n_clicks"),
     Input("save-threshold-settings", "n_clicks")],
    [State("threshold-modal", "is_open"),
     State({"type": "threshold-min-enabled", "index": ALL}, "value"),
     State({"type": "threshold-max-enabled", "index": ALL}, "value"),
     State({"type": "threshold-min-value", "index": ALL}, "value"),
     State({"type": "threshold-max-value", "index": ALL}, "value"),
     State("threshold-email-address", "value"),
     State("threshold-email-minutes", "value"),
     State("threshold-email-enabled", "value")],
    prevent_initial_call=True
)
def toggle_threshold_modal(open_clicks, close_clicks, save_clicks, is_open,
                          min_enabled_values, max_enabled_values, min_values, max_values,
                          email_address, email_minutes, email_enabled):
    """Handle opening/closing the threshold settings modal and saving settings"""
    global threshold_settings
    
    ctx = callback_context
    
    # Check if callback was triggered
    if not ctx.triggered:
        return [no_update]  # Return as a list with one element
    
    # Get the property that triggered the callback
    trigger_prop_id = ctx.triggered[0]["prop_id"]
    
    # Check for open button clicks (with pattern matching)
    if '"type":"open-threshold"' in trigger_prop_id:
        # Check if any button was actually clicked (not initial state)
        if any(click is not None for click in open_clicks):
            return [True]  # Return as a list with one element
    
    # Check for close button click
    elif trigger_prop_id == "close-threshold-settings.n_clicks":
        # Check if button was actually clicked (not initial state)
        if close_clicks is not None:
            return [False]  # Return as a list with one element
    
    # Check for save button click
    elif trigger_prop_id == "save-threshold-settings.n_clicks":
        # Check if button was actually clicked (not initial state)
        if save_clicks is not None and min_enabled_values:
            # Update the threshold settings
            for i in range(len(min_enabled_values)):
                counter_num = i + 1
                threshold_settings[counter_num] = {
                    'min_enabled': min_enabled_values[i],
                    'max_enabled': max_enabled_values[i],
                    'min_value': float(min_values[i]),
                    'max_value': float(max_values[i])
                }
            
            # Save the email settings
            threshold_settings['email_enabled'] = email_enabled
            threshold_settings['email_address'] = email_address
            threshold_settings['email_minutes'] = int(email_minutes) if email_minutes is not None else 2
            
            # Save settings to file
            save_success = save_threshold_settings(threshold_settings)
            if save_success:
                logger.info("Threshold settings saved successfully")
            else:
                logger.warning("Failed to save threshold settings")
            
            # Close modal - no need to update the settings display anymore
            return [False]  # Return as a list with one element
    
    # Default case - don't update anything
    return [no_update]  # Return as a list with one element


# ---------------------------------------------------------------------------
# Metric logging every minute
# ---------------------------------------------------------------------------
@app.callback(
    Output("metric-logging-store", "data"),
    [Input("metric-logging-interval", "n_intervals")],

    [State("app-state", "data"),
     State("app-mode", "data"),
     State("machines-data", "data"),
     State("production-data-store", "data"),
     State("weight-preference-store", "data")],
    prevent_initial_call=True,
)
def log_current_metrics(n_intervals, app_state_data, app_mode, machines_data, production_data, weight_pref):

    """Collect metrics for each connected machine and append to its file."""
    global machine_connections

    CAPACITY_TAG = "Status.ColorSort.Sort1.Throughput.KgPerHour.Current"
    REJECTS_TAG = "Status.ColorSort.Sort1.Total.Percentage.Current"
    OPM_TAG = "Status.ColorSort.Sort1.Throughput.ObjectPerMin.Current"
    COUNTER_TAG = "Status.ColorSort.Sort1.DefectCount{}.Rate.Current"
    mode = "demo"
    if app_mode and isinstance(app_mode, dict) and "mode" in app_mode:
        mode = app_mode["mode"]

    if not weight_pref:
        weight_pref = load_weight_preference()

    if mode == "demo":
        if machines_data and machines_data.get("machines"):
            for m in machines_data["machines"]:
                prod = (m.get("operational_data") or {}).get("production", {})
                capacity = prod.get("capacity", 0)
                accepts = prod.get("accepts", 0)
                rejects = prod.get("rejects", 0)

                metrics = {
                    "capacity": convert_capacity_to_lbs(capacity, weight_pref),
                    "accepts": convert_capacity_to_lbs(accepts, weight_pref),
                    "rejects": convert_capacity_to_lbs(rejects, weight_pref),
                    "objects_per_min": 0,
                }

                counters = m.get("demo_counters", [0] * 12)
                for i in range(1, 13):
                    metrics[f"counter_{i}"] = counters[i-1] if i-1 < len(counters) else 0

                append_metrics(metrics, machine_id=str(m.get("id")), mode="Demo")

        return dash.no_update

    for machine_id, info in machine_connections.items():
        if not info.get("connected", False):
            continue
        tags = info["tags"]
        capacity_value = tags.get(CAPACITY_TAG, {}).get("data").latest_value if CAPACITY_TAG in tags else None
        reject_pct = tags.get(REJECTS_TAG, {}).get("data").latest_value if REJECTS_TAG in tags else None

        capacity_lbs = capacity_value * 2.205 if capacity_value is not None else 0
        reject_pct = reject_pct if reject_pct is not None else 0
        rejects_lbs = (reject_pct / 100.0) * capacity_lbs
        accepts_lbs = capacity_lbs - rejects_lbs

        opm = tags.get(OPM_TAG, {}).get("data").latest_value if OPM_TAG in tags else 0
        if opm is None:
            opm = 0

        metrics = {
            "capacity": capacity_lbs,
            "accepts": accepts_lbs,
            "rejects": rejects_lbs,
            "objects_per_min": opm,
        }

        for i in range(1, 13):
            tname = COUNTER_TAG.format(i)
            val = tags.get(tname, {}).get("data").latest_value if tname in tags else 0
            if val is None:
                val = 0
            metrics[f"counter_{i}"] = val

        append_metrics(metrics, machine_id=str(machine_id), mode="Live")

    return dash.no_update


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
        
        logger.info("About to start auto-reconnection thread...")
        start_auto_reconnection()
        logger.info("Auto-reconnection thread start command completed")

        startup_thread = Thread(target=delayed_startup_connect)
        startup_thread.daemon = True
        startup_thread.start()
        logger.info("Scheduled startup auto-connection...")

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
