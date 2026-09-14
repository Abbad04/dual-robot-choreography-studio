# FR5 Choreography Connector for macOS

The standalone connector provides Apple Silicon and Intel builds with macOS 12
as the deployment target; release CI currently executes them on macOS 15. The
launcher selects the correct binary automatically; no Python, Homebrew,
Docker, compiler, or SDK download is required.

> [!CAUTION]
> Physical playback can move the robot. Clear the workcell, use reduced-speed
> mode, keep the physical emergency stop reachable, and follow FAIRINO’s
> commissioning and risk-assessment requirements.

## Before opening the connector

1. Extract the ZIP and keep the complete **FR5 Connector** folder together.
   Do not copy out only the `.command` file or one file from `bin/`.
2. Connect the Mac to the FR5 controller by Ethernet. The controller normally
   uses `192.168.58.2`. Give the Mac a different address on the same subnet,
   for example `192.168.58.100` with subnet mask `255.255.255.0`. Leave the
   router field blank. Wi-Fi can remain on.
3. With the controller powered, open `http://192.168.58.2` in Safari. Keep
   motion disabled until the workcell and operator are ready. If the
   controller page does not load, fix the Ethernet connection before starting
   the connector.

## First connection

1. Control-click **Start FR5 Connector.command**, choose **Open**, and confirm
   the macOS prompt.
2. Press Return to accept the normal robot IP, `192.168.58.2`, or enter the
   controller’s actual IP. Do not enter the Mac’s Ethernet address.
3. Type uppercase `YES` only when physical movement is safe. Any other answer
   intentionally starts the connector read-only.
4. Leave the Terminal window open. Copy its current pairing code into the
   [FR5-WML web app](https://abbad04.github.io/dual-robot-choreography-studio/?robot=fr5)
   and choose **Connect**. You do not need to refresh an already-open app.
5. Check **I am beside the robot and ready** only after the page shows that
   motion is enabled and the robot safety state is ready.

Play begins at the selected timeline position. If the playhead is in the
middle of a sequence, the FR5 first moves at reduced speed to the matching pose
and playback begins only after arrival. Pause freezes both robot and timeline;
Play resumes both. Stop terminates the controller program and resets the web
timeline to zero. Stop does not command an automatic return movement.

## If macOS blocks the inner binary

The package is ad-hoc signed, not Apple-notarized, so Gatekeeper can separately
ask you to approve the architecture-specific binary on its first run.

1. If the launcher reaches the `YES` prompt and macOS then offers **Show in
   Finder**, use it.
2. Control-click the highlighted `FR5-Connector-arm64` or
   `FR5-Connector-x86_64`, choose **Open**, and confirm once.
3. The directly opened binary may display a pairing code in **read-only** mode.
   Close that Terminal window; do not use the inner binary directly for normal
   motion-enabled operation.
4. Run **Start FR5 Connector.command** again and type uppercase `YES`.

## Compatibility and integrity

The package contains the unmodified official FAIRINO pure-Python SDK source
for WebApp versions 3.9.4 through 3.9.9. It reads the controller version
without issuing a motion command, selects the matching source, and verifies
its pinned byte count and SHA-256 immediately before loading it. Unsupported
versions fail before motion is enabled.

Download the current package and checksum from the
[latest release](https://github.com/Abbad04/dual-robot-choreography-studio/releases/latest):

- [FR5-Connector-macOS.zip](https://github.com/Abbad04/dual-robot-choreography-studio/releases/latest/download/FR5-Connector-macOS.zip)
- [FR5-Connector-macOS.zip.sha256](https://github.com/Abbad04/dual-robot-choreography-studio/releases/latest/download/FR5-Connector-macOS.zip.sha256)

To verify both files downloaded into the same folder, open Terminal in that
folder and run:

```sh
shasum -a 256 -c FR5-Connector-macOS.zip.sha256
```

Continue only if the result ends in `OK`.

For known errors and network checks, see the online
[Troubleshooting guide](https://github.com/Abbad04/dual-robot-choreography-studio/blob/main/docs/TROUBLESHOOTING.md).
The release build verifies both architecture binaries, every bundled SDK
version, code signing, and the final package checksum. A qualified operator
must still commission the first real run at reduced speed on the specific
robot and workcell.
