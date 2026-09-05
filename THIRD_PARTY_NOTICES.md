# Third-party notices

The generated `index.html` embeds the components below so the choreography
editor and its UR20 and FR5-WML previews can run without a network connection.

## Three.js 0.158.0

- Project: <https://threejs.org/>
- Source: <https://www.npmjs.com/package/three/v/0.158.0>
- License: MIT

The build includes the Three.js core module, `OrbitControls`, and
`ColladaLoader`.

## WaveSurfer.js 7.12.11

- Project: <https://wavesurfer.xyz/>
- Source: <https://www.npmjs.com/package/wavesurfer.js/v/7.12.11>
- License: BSD-3-Clause

## Official Universal Robots UR20 visual assets

- Project: Universal Robots ROS 2 Description
- Source: <https://github.com/UniversalRobots/Universal_Robots_ROS2_Description>
- Model: UR20
- Included files: seven Collada link meshes and `UR20_DIFF_8bit_2K.png`

Use of the UR20 graphical assets is subject to the Universal Robots terms
included verbatim at [`assets/ur20/LICENSE.txt`](assets/ur20/LICENSE.txt).
The build embeds the same files byte-for-byte and records their SHA-256 hashes
in `globalThis.__UR20_BUILD_METADATA__`.

Copyright notice requested by the graphical-documentation terms:

> © 2023 Universal Robots A/S. Use hereof is subject to Universal Robots A/S’
> Terms and Conditions for Use of Graphical Documentation.

Keep this notice file and `assets/ur20/LICENSE.txt` with every downloadable
software package that contains the graphical assets.

## Official FAIRINO SimMachine FR5-WML visual assets

- Source: locally installed official FAIRINO SimMachine 3.9.8 WebApp
- Model: FR5-WML, controller model 803
- Included files: seven Collada link meshes

These files are used locally to render the same model geometry exposed by the
installed SimMachine application. They are proprietary simulator-derived
assets. No open license or redistribution permission is asserted. Obtain
FAIRINO authorization before redistributing a package that contains them. The
standalone build records their individual SHA-256 hashes in
`globalThis.__FR5_BUILD_METADATA__`.
