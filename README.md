
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

## Running with Gunicorn

For production deployments you can run the Dash application using Gunicorn.


1. Install Gunicorn:
   ```bash
   pip install gunicorn
   ```
2. Start the server by pointing Gunicorn at the WSGI entry point:
   ```bash
   gunicorn --bind 0.0.0.0:8050 wsgi:application
   ```

## Packaging

When creating a frozen executable (for example with PyInstaller) or building the
Windows installer using the provided Inno Setup script, make sure the
`Audiowide-Regular.ttf` font file is bundled with the application. `draw_header`
will look for the font either in the application directory or an `assets`
subfolder. The installer script copies the file into `assets` automatically so
the PDF headers render with the correct font.

