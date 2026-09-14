# Architecture and safety boundaries

The editor is a static, client-side application. Physical FR5 playback adds a
small local connector so the public HTTPS page never connects directly to the
robot controller.

```text
GitHub Pages (static HTTPS app)
              |
              | trusted HTTPS origin + current launch code
              v
Mac/PC loopback connector — 127.0.0.1:8766
              |
              | FAIRINO protocol to private controller address
              | (normally dedicated Ethernet)
              v
FR5 controller — normally 192.168.58.2
```

## Browser application

`index.html` contains the application, 3D renderer, robot visual assets, and
export logic. Choreography editing and previewing happen in the browser.
Projects and user-selected audio do not need an application backend.

The FR5 and UR20 are separate profiles. A project targets one profile at a
time; the application does not coordinate two physical robots simultaneously.

## Local connector

The connector:

- listens only on IPv4 loopback at `127.0.0.1:8766`;
- rejects non-loopback clients;
- uses a new high-entropy pairing code for each launch;
- accepts a paired, trusted web origin rather than exposing a LAN service;
- reads controller state before allowing an operator to arm playback; and
- accepts only a validated private controller address, which normally routes
  over the computer’s dedicated Ethernet connection.

The pairing code is a local capability, not a password to reuse or publish.
Restarting the connector creates a new one.

## Motion gates

Connecting is read-only by default. Physical movement requires all of the
following independent gates:

1. the connector starts with `--allow-motion` (the macOS launcher adds it only
   after the operator types uppercase `YES`);
2. the hosted app presents the current launch’s pairing code, or a frontend
   served by the connector uses its current-launch same-origin session;
3. the controller reports a motion-ready safety and program state; and
4. the operator checks **I am beside the robot and ready** in the browser.

The connector revalidates payload shape, joint domains, speed, acceleration,
frame count, and the fixed 8 ms command period before physical playback.
Communication loss and explicit Stop use the available controller stop paths.
These controls reduce software risk; they do not replace safety-rated robot
hardware or workcell safeguards.

## Controller programs and large trajectories

For synchronized physical playback, the browser encodes joint samples as
signed microdegree deltas plus microsecond durations, then Base64-encodes that
binary payload. The connector validates and reconstructs the trajectory,
generates a strict ASCII Lua program, and uploads it to the controller.

Connector v1.0.3 uses FAIRINO’s complete-file protocol on TCP port 20010 to
avoid the SDK wrapper’s partial-send behavior on large programs. The transfer
includes the declared size and MD5 required by that controller protocol, waits
for controller acknowledgement, registers the uploaded program, and only then
loads it.

## macOS SDK selection

Release packages bundle the unmodified upstream `Robot.py` for each supported
FAIRINO WebApp version. The connector reads the controller’s version without a
motion command and matches it to an immutable upstream commit, expected byte
count, and SHA-256 digest. A mismatch or unsupported version fails before
motion is enabled.

The macOS release workflow builds separate Apple Silicon and Intel binaries,
verifies every bundled SDK inside each binary, ad-hoc signs the executables,
and publishes a universal ZIP plus SHA-256 checksum. Ad-hoc signing does not
provide Apple notarization, so Gatekeeper approval can still be required on
first launch.

## Security assumptions

- The computer and robot Ethernet link are trusted and physically controlled.
- The computer browser and operating-system account are not compromised.
- The public app is loaded from the repository’s official GitHub Pages origin.
- The operator retains access to a physical emergency stop and follows the
  robot manufacturer’s procedures.

For sensitive reports, follow [SECURITY.md](../SECURITY.md).
