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
- `.fr5proj` and `.ur20proj` project save/open;
- strict ASCII FAIRINO `RAW_*.lua` export at the 8 ms ServoJ clock;
- URScript and URPX export in UR20 mode.

No Docker installation is required for manual FR5 authoring, visualization, or
Lua export. Direct controller verification, UR RTDE capture, and simulator
launching are local companion features and are not available from GitHub Pages.

## Safety

This is a simulator-authoring tool. It does not connect to or command a physical
robot from the hosted site. Validate exported programs in FAIRINO SimMachine,
then perform the manufacturer-required risk assessment before physical use.

## Third-party material

Read [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before copying or
redistributing the application. Universal Robots asset terms are included in
[`assets/ur20/LICENSE.txt`](assets/ur20/LICENSE.txt).
