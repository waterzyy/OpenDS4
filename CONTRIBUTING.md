# Contributing to OpenDS4

Thanks for taking the time to improve OpenDS4. Bug reports, documentation fixes, and focused pull requests are all welcome.

## Development setup

```bash
git clone https://github.com/waterzyy/OpenDS4.git
cd OpenDS4
python -m venv .venv
.venv\Scripts\activate            # on Windows
python -m pip install -e ".[dev]"
```

The app itself targets Windows 10/11 because of ViGEmBus and the Win32 tray integration. The protocol, profile, and math modules are platform-independent and can be exercised on any OS.

## Running the tests

```bash
python -m pytest -v
```

The suite runs without hardware and without a display server for everything except the `tkinter` import. On CI it runs on Windows, where Tcl/Tk is available.

## Code style

- Python 3.10 or newer.
- [Ruff](https://docs.astral.sh/ruff/) enforces linting and formatting:

  ```bash
  python -m ruff check .
  python -m ruff format --check .
  ```

- Keep modules small and auditable. The project intentionally ships as a flat set of readable files rather than a packaged framework.
- Add type hints to public functions and keep docstrings short and factual.
- Source files are plain ASCII. The test suite rejects non-English text and emoji in `.py` files so the codebase stays searchable and consistent.

## Project rules

These are not style preferences; the test suite enforces them.

1. **No network access.** Do not import `socket`, `urllib`, `http`, `requests`, `aiohttp`, `httpx`, `ftplib`, `smtplib`, `telnetlib`, `xmlrpc`, or `webbrowser`.
2. **No dynamic execution.** Do not use `eval`, `exec`, `compile`, or `__import__`.
3. **No subprocesses.** Do not import or call `subprocess`.
4. **No silent dependencies.** New third-party packages need a discussion first, because every dependency adds something users have to trust.
5. **Keep the HID loop non-blocking.** Never call blocking `time.sleep` in `ds4_engine.py`; feedback writes belong on the dedicated output thread.

If a change genuinely needs one of the above, open an issue and explain the trade-off before writing code.

## Pull requests

1. Fork the repository and create a topic branch.
2. Add or update tests for the behavior you are changing.
3. Run `python -m pytest -v` and `python -m ruff check .` locally.
4. Fill in the pull request template and describe what changed and why.
5. Keep the diff focused. Unrelated formatting or refactoring belongs in a separate pull request.

### Commit messages

Use a short imperative summary, for example `Fix Bluetooth CRC seed for output reports` or `Add per-trigger deadzone UI`. There is no enforced convention beyond that.

## Reporting bugs

Open an issue using the bug report template and include:

- Windows version and Python version
- Controller model and how it is connected (USB, Bluetooth, or Wireless Adapter)
- Whether ViGEmBus is installed and visible in `joy.cpl`
- Steps to reproduce
- Log output from `run_windows.bat --console`, if available

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
