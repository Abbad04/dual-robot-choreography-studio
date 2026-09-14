# Security policy

This project includes a local connector capable of commanding physical robot
motion. Please report security and safety-control vulnerabilities privately.

## Supported versions

Security fixes are applied to the current `main` branch and, when applicable,
the latest published connector release. Older connector releases may not
receive fixes. Reproduce against the latest version when it is safe to do so.

## Reporting a vulnerability

Use GitHub’s
[private vulnerability reporting](https://github.com/Abbad04/dual-robot-choreography-studio/security/advisories/new).
Do not open a public issue for a vulnerability before a fix is available.

Include only what is necessary to reproduce the issue:

- affected commit or connector version;
- operating system, browser, and controller WebApp version;
- attack prerequisites and impact;
- minimal reproduction steps or proof of concept; and
- whether physical motion or a safety gate could be affected.

Remove active pairing codes, confidential choreography, credentials, personal
data, and unrelated controller logs. A pairing code is created per launch and
remains valid while that connector is running, so treat it as a local secret.

Relevant reports include pairing or origin bypasses, non-loopback exposure,
unauthorized motion, safety-gate bypasses, trajectory-validation failures,
unsafe behavior after communication loss, malicious project import, and
release-package integrity problems.

## Immediate physical risk

GitHub is not an emergency channel. If testing creates an immediate risk,
stop the robot with the appropriate physical control, secure the workcell, and
follow the robot manufacturer’s incident procedure before collecting software
diagnostics.

Please allow time to reproduce and assess a report before public disclosure.
