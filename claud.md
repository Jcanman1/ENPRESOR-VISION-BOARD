# Claud

This repository contains the *Enpresor OPC Dashboard*, a Dash application that replicates the behavior of the original `EnpresorOPCDataViewBeforeRestructureLegacy.py` script.

## Running the application
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Start the dashboard:
   ```bash
   python3 EnpresorOPCDataViewBeforeRestructureLegacy.py
   ```
   The script prints the local and network URLs for accessing the interface.

## Testing
After installing the requirements, run:
```bash
pytest
```
All tests should pass.

## Packaging
When creating a frozen executable (e.g. with PyInstaller) or building the Windows installer, include the `Audiowide-Regular.ttf` font from the `assets` directory so PDF headers render correctly.
