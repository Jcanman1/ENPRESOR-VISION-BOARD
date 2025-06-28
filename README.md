# Satake Enpresor Tools

This repository contains utilities for working with Enpresor devices. Dash is used to build dashboards while ReportLab creates PDF reports.

## Installation

1. It is recommended to use a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

2. Install the Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Contents

- `EnpresorOPCDataViewBeforeRestructureLegacy.py` – dashboard and OPC UA client
- `generate_report.py` – generate PDF reports using recorded data
- `hourly_data_saving.py` – helper routines for writing metrics to CSV

