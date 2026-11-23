# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 1.0.x   | Yes       |
| older   | No        |

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting: open the **Security** tab of the repository and choose **Report a vulnerability**. If that is unavailable, open a regular issue that only says "security contact needed" and a maintainer will follow up privately.

Please do not publish exploitable details in a public issue.

## Scope

OpenDS4 is an offline desktop application. It does not open sockets, does not ship a server, and does not execute downloaded code. Reports in the following areas are especially useful:

- HID report parsing that could be crashed or made to read out of bounds
- Profile JSON handling that could write outside the `profiles/` directory
- Any path that reintroduces network access or dynamic code execution

Driver installation (ViGEmBus, HidHide) is handled by those projects and is outside the scope of this policy. OpenDS4 does not bundle or install drivers itself.

## Hardening

- The test suite parses every Python file with `ast` and fails if networking modules, `subprocess`, `eval`, `exec`, `compile`, or `__import__` are used.
- Profile names are sanitized before being used as file names, and profile writes are atomic.
- The application runs without administrator privileges. Only the one-time ViGEmBus driver install requires elevation.
