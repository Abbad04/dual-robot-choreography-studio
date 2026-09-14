# Contributing

Thanks for helping improve Dual Robot Choreography Studio. Because this
software can command physical motion, safety and reproducibility take priority
over convenience.

## Before starting

- Search [existing issues](https://github.com/Abbad04/dual-robot-choreography-studio/issues)
  and read [Troubleshooting](docs/TROUBLESHOOTING.md).
- Open an issue before a large feature or a change to the motion path so the
  intended behavior and safety impact can be discussed.
- Use the private process in [SECURITY.md](SECURITY.md) for vulnerabilities;
  do not disclose them in a public issue.
- Do not include active pairing codes, confidential choreography, robot logs
  with sensitive details, or third-party assets without redistribution rights.
- This repository does not yet grant a project-wide license. Before external
  code is merged, the contributor and maintainers must explicitly establish
  the rights under which the project may distribute that contribution.

## Local web preview

The deployed application is the self-contained `index.html` file. From the
repository root, run either:

```sh
python3 -m http.server 8000
```

or, on Windows:

```powershell
py -m http.server 8000
```

Then open:

- `http://127.0.0.1:8000/` for UR20; or
- `http://127.0.0.1:8000/?robot=fr5` for FR5-WML.

`index.html` is a production artifact containing embedded libraries and robot
assets. Avoid bulk formatting or unrelated rewrites. If a pull request is not
intended to change the web app, verify that this file remains byte-for-byte
unchanged.

## Connector checks

Source-based connector work requires Python 3.10 or newer. Safe checks that do
not contact a robot are:

```sh
python fr5_connector.py --help
python -m py_compile fr5_connector.py src/ur20_timing/fr5_live_bridge.py src/ur20_timing/fr5_macos_sdk.py
```

The public release workflow additionally builds Apple Silicon and Intel
executables, verifies the pinned SDK sources inside both packages, verifies
code signing, and produces a checksum.

Do not connect an automated test to a physical robot. A real-robot test must
be explicitly planned, supervised by a qualified operator, begun at reduced
speed, and performed with a clear workcell and reachable emergency stop.

## Safety invariants

A change must preserve these properties unless a reviewed design provides a
stronger control:

- the connector listens only on loopback and rejects remote clients;
- each launch requires a fresh pairing code;
- startup is read-only unless motion is explicitly authorized;
- browser and controller safety gates fail closed;
- trajectories are validated before upload or streaming;
- communication loss and Stop cannot silently continue playback; and
- physical speed defaults remain conservative.

Explain the safety effect of connector, timing, parser, export, or transport
changes in the pull request.

## Pull requests

Keep each pull request focused. Include:

- the problem and intended behavior;
- screenshots for visible UI changes;
- exact manual or automated checks performed;
- controller and simulator versions when relevant;
- a safety-impact statement; and
- provenance and license terms for any new dependency or asset.

Use conventional, imperative commit messages such as `Document FR5 network
setup` or `Reject malformed trajectory frames`.
