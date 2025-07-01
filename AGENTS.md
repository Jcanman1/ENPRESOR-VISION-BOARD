# AGENTS Instructions

This project is a Dash-based web application that replicates the legacy
`EnpresorOPCDataViewBeforeRestructureLegacy.py` script. Use these
instructions when running tests or making automated updates.

## Environment Setup
1. **Python version:** Ensure Python 3.x is available.
2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt -r test-requirements.txt
   ```
   This installs both runtime and test packages (including `dash` and `pytest`).

## Running Tests
From the repository root, execute:
```bash
pytest
```
The test suite resides in the `tests/` directory and requires no
additional setup beyond the dependencies above.

## Running the Application
To start the dashboard locally:
```bash
python3 EnpresorOPCDataViewBeforeRestructureLegacy.py
```
The script prints URLs for accessing the interface. Optional environment
variables include:
- `LOG_LEVEL` – overrides log verbosity.
- `OPEN_BROWSER` – set to `0` or `false` to disable automatic browser opening.
- `DEBUG` – set to `1` for verbose output.

## Packaging Notes
If you create a PyInstaller build or Windows installer, include the font
`assets/Audiowide-Regular.ttf` so PDF reports render correctly.

## Guidelines for Contributions
- Keep pull requests focused and well-scoped.
- Run `pytest` before submitting changes.
- Provide clear commit messages with a concise summary line followed by a
  short description if necessary.

