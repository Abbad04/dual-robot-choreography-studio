# FR5 Choreography Connector for macOS

This package supports both Apple Silicon and Intel Macs. The launcher chooses
the correct version automatically; no Python installation is needed.

## First connection

1. Extract the ZIP. Keep the complete **FR5 Connector** folder together.
2. Connect the Mac to the FR5 controller by Ethernet. The controller normally
   uses `192.168.58.2`.
3. Power on and enable the FR5. Clear the workcell, select reduced-speed mode,
   and keep the physical emergency stop reachable.
4. Double-click **Start FR5 Connector.command**. If macOS blocks the first
   opening, Control-click it, choose **Open**, and confirm once. The connector
   is ad-hoc signed but is not Apple-notarized.
5. Enter the robot IP and type `YES` only when physical motion is safe.
6. Leave the connector window open. Copy its pairing code into the deployed
   choreography web app and choose **Connect**.
7. After the robot is connected, check **I am beside the robot and ready**.

The package contains the unmodified official FAIRINO pure-Python SDK source for
WebApp versions 3.9.4 through 3.9.9. The connector selects the exact controller
version and verifies its pinned SHA-256 digest before loading it. No internet,
compiler, Docker, Homebrew, Python installation, or separate SDK installation
is required. Unsupported versions fail before motion is enabled.

Play starts at the selected timeline position. If that position is in the
middle, the FR5 first moves at reduced speed to the matching pose and then
starts synchronized playback. Pause freezes both; Play resumes both; Stop
halts the controller program and resets the web timeline without starting an
automatic physical return move.

The physical path has automated mock coverage and macOS build checks, but has
not been commissioned against your particular robot. A qualified operator must
perform the first real test at reduced speed.
