
# Enpresor OPC Dashboard

This project aims to replicate the full functionality of `EnpresorOPCDataViewBeforeRestructureLegacy.py` while gradually breaking the monolithic code into smaller, maintainable modules. Every feature and UI button from the legacy dashboard is preserved so that the modernized version behaves identically to the original application.

## Repository Structure

The project is intentionally split into small modules that mirror sections of the
legacy script. Below is a short overview of the key files and how they fit into
the application:

- **`EnpresorOPCDataViewBeforeRestructureLegacy.py`** – main Dash application
  that wires together the layout, OPC connection logic and callbacks.
- **`EnpresorOPCDataViewBeforeRestructureORIGINAL.py`** – the original
  monolithic script kept for reference during the refactor.
- **`autoconnect.py`** – starts background threads to automatically reconnect to
  saved machines when the app launches.
- **`callbacks.py`** – registers all Dash callbacks used by the dashboard.
- **`counter_manager.py`** – utilities for tracking counter histories with a
  fixed maximum length.
- **`df_processor.py`** – helpers for safely reading and pruning large CSV
  files.
- **`generate_report.py`** – creates PDF production reports from the exported
  metrics.
- **`hourly_data_saving.py`** – periodically writes machine metrics and control
  logs to CSV and exposes functions for querying historical data.
- **`i18n.py`** – provides language translations via the `tr()` helper.
- **`image_manager.py`** – validates uploaded images and caches them on disk.
- **`memory_leak_fixes.py`** and **`memory_monitor.py`** – small utilities used
  to track memory usage and clean up cached data.
- **`wsgi.py`** – minimal entry point used by Gunicorn (`application = app.server`).
- **`assets/`** – fonts, CSS and images loaded by Dash and the reporting code.
- **`tests/`** – pytest suite covering report generation and callback logic.
- **`EnpresorDataIcon.ico`** and **`EnpresorOPCViewerInstaller.iss`** – resources
  used when packaging the application for Windows.
- **`requirements.txt`** and **`test-requirements.txt`** – runtime and testing
  dependencies.

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

## Testing

After installing the requirements run:

```bash
pytest
```

All tests should pass.

## Packaging

When creating a frozen executable (for example with PyInstaller) or building the
Windows installer using the provided Inno Setup script, make sure the
`Audiowide-Regular.ttf` font file is bundled with the application. `draw_header`
will look for the font either in the application directory or an `assets`
subfolder. The installer script copies the file into `assets` automatically so
the PDF headers render with the correct font. When generating Japanese
reports you must also include `NotoSansJP-Regular.otf` in the `assets`
folder so the text renders correctly.

To include the `assets` folder when creating the executable with PyInstaller use
the `--add-data` option. The separator differs by platform:

- **Windows**
  ```bash
  pyinstaller script.py --add-data "assets;assets"
  ```
- **macOS/Linux**
  ```bash
  pyinstaller script.py --add-data "assets:assets"
  ```

With PyInstaller 6 the bundled files are extracted into an `_internal`
directory next to the executable.

## Generating Reports in Lab Mode

`generate_report.py` builds PDF summaries from the CSV exports. When working
with lab data pass the `--lab` option so irregular timestamps are used when
calculating capacities and object totals:

```bash
python3 generate_report.py <export directory> --lab
```

## Running Tests

Install `pytest` along with any runtime dependencies, for example:

```bash
pip install -r requirements.txt -r test-requirements.txt
```

Then run the test suite from the repository root:

```bash
pytest
```

