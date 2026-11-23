# Contributing

Thanks for taking the time to improve OpenDS4. Bug reports, small fixes, and documentation tweaks are all welcome.

## Setting up

```bash
git clone https://github.com/waterzyy/OpenDS4.git
cd OpenDS4
python -m venv .venv
.venv\Scripts\activate            # on Windows
python -m pip install -e ".[dev]"
```

The app itself targets Windows 10/11 because of ViGEmBus and the Win32 tray, and CI runs the tests on Windows. The protocol, profile, and math modules are plain Python and read the same anywhere, but expect the GUI and tray paths to need Tcl/Tk and Windows.

## Running the tests

```bash
python -m pytest -v
```

The suite runs without hardware and takes a few seconds.

## A few ground rules

These are enforced by the test suite, so they are worth knowing before you start:

1. **No network access.** Please don't import `socket`, `urllib`, `http`, `requests`, `aiohttp`, `httpx`, `ftplib`, `smtplib`, `telnetlib`, `xmlrpc`, or `webbrowser`.
2. **No dynamic execution.** Please don't use `eval`, `exec`, `compile`, or `__import__`.
3. **No subprocesses.** Please don't import or call `subprocess`.
4. **Keep the HID loop non-blocking.** No blocking `time.sleep` in `ds4_engine.py`; feedback writes belong on the dedicated output thread.

If a change genuinely needs one of these, open an issue and explain the trade-off first.

## Style

- Python 3.10 or newer.
- [Ruff](https://docs.astral.sh/ruff/) runs a small set of correctness checks:

  ```bash
  python -m ruff check .
  ```

- Keep modules small and readable. The project is intentionally a flat set of files rather than a packaged framework.
- Add type hints to public functions and keep docstrings short and factual.
- Source files are plain ASCII, English only, no emoji. The test suite checks for this.

## Pull requests

1. Fork the repository and create a topic branch.
2. Add or update tests for the behavior you are changing.
3. Run `python -m pytest -v` and `python -m ruff check .` locally.
4. Fill in the pull request template and explain what changed and why.
5. Keep the diff focused. Unrelated cleanup belongs in a separate pull request.

Commit messages can be plain and short, for example `Fix Bluetooth CRC seed for output reports` or `Add per-trigger deadzone UI`.

## Reporting bugs

Open an issue using the bug report template. The details that help most are the Windows and Python versions, the controller model, how it's connected (USB, Bluetooth, or Wireless Adapter), the steps to reproduce, and the log output from `run_windows.bat --console` if you have it.

For questions and feature ideas, see [SUPPORT.md](SUPPORT.md).

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
