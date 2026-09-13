# Dual Robot Choreography Studio

Browser-based manual choreography for the FAIRINO FR5-WML model 803 and the
Universal Robots UR20.

## Use the app

Open the GitHub Pages link from this repository. Select **FR5-WML** in the
top-left selector, or append `?robot=fr5` to the URL.

The application runs locally in the browser. It supports:

- official-model 3D visualization for FR5-WML and UR20;
- six-joint jogging and Cartesian target editing;
- fixed-duration MoveJ and wait nodes;
- timeline play, pause, seek, and optional audio;
- optional connection to a physical FR5 through the included local connector;
- `.fr5proj` and `.ur20proj` project save/open;
- strict ASCII FAIRINO `RAW_*.lua` export at the 8 ms ServoJ clock;
- URScript and URPX export in UR20 mode.

No Docker installation is required for manual FR5 authoring, visualization, or
Lua export. Physical FR5 playback uses the local connector included in this
repository; the public webpage never exposes the robot to the internet.

## Connect a physical FR5

1. On macOS, use the app's **Download for macOS** button and extract the entire
   **FR5 Connector** folder. It contains standalone Apple Silicon and Intel
   builds and does not require Python, Docker, Homebrew, or a compiler. On
   Windows/Linux, download and extract this repository and install the official
   FAIRINO Python SDK matching the controller.
2. Connect that computer to the FR5 by Ethernet. On macOS, double-click
   **Start FR5 Connector.command**; on Windows, double-click
   **Start FR5 Connector.cmd**. Enter the robot IP, approve physical control,
   and leave its window open.
3. Copy the pairing code shown in that window.
4. Open the hosted app in **FR5-WML** mode, enter the code in **FR5
   connection**, and choose **Connect**.
5. Confirm **I am beside the robot and ready**. The normal timeline controls
   now operate the connected FR5.

On connection, the Mac package reads the controller version without moving the
robot and selects the matching bundled official FAIRINO pure-Python SDK source.
It verifies the exact size and SHA-256 immediately before use. No internet or
manual SDK download is required. Versions 3.9.4 through 3.9.9 are currently
pinned; other versions fail before motion is enabled.

**Play** begins at the current timeline cursor. If that cursor is in the middle
of the sequence, the robot first moves to the pose at that point and playback
starts only after it arrives. **Pause** pauses both robot and timeline; **Play**
resumes both. **Stop** terminates the controller program and resets the timeline
to zero. Stop does not initiate a return movement.

## Safety

Physical playback is for supervised commissioning and is not a safety-rated
teach pendant. Begin in reduced-speed mode, keep the workcell clear and the
physical emergency stop reachable, and perform the manufacturer-required risk
assessment before use. The connector defaults to 10% playback speed.

## Authors

Megan Del Villar and Abbad Shazly

## Third-party material

Read [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before copying or
redistributing the application. Universal Robots asset terms are included in
[`assets/ur20/LICENSE.txt`](assets/ur20/LICENSE.txt).
