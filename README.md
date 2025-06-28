# Enpresor OPC Dashboard

This project aims to replicate the full functionality of `EnpresorOPCDataViewBeforeRestructureLegacy.py` while gradually breaking the monolithic code into smaller, maintainable modules. Every feature and UI button from the legacy dashboard is preserved so that the modernized version behaves identically to the original application.

## Setup
1. Ensure you have Python 3 installed.
2. Install required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   (If no `requirements.txt` is provided, install packages referenced in the legacy script as needed.)

## Usage
Run the dashboard from the repository root:
```bash
python3 EnpresorOPCDataViewBeforeRestructureLegacy.py
```
The script prints the local and network URLs for accessing the interface. Optionally use `--open-browser` to automatically open your web browser and `--debug` for verbose output.

As the code is refactored into modules, the entry point and command-line options will remain consistent so that users experience no change in behavior.
