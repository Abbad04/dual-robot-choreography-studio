# Changelog

Notable changes to the standalone macOS FR5 connector are recorded here.

## [1.0.3] - 2026-09-14

- Replaced the SDK wrapper’s partial-send upload path with a complete,
  acknowledged FAIRINO file transfer for large generated Lua programs.
- Preserved the size, digest, registration, and program-load checks before
  playback.

## [1.0.2] - 2026-09-14

- Bundled integrity-checked official FAIRINO SDK sources for supported WebApp
  versions, removing the first-run download requirement.
- Fixed validation of the connector’s packaged fallback frontend.

## [1.0.1] - 2026-09-13

- Corrected the writable macOS application-support path used by the standalone
  connector.

## [1.0.0] - 2026-09-13

- Added standalone Apple Silicon and Intel connector binaries in one macOS
  package.
- Added architecture-selecting `.command` launcher and checksum release asset.

[1.0.3]: https://github.com/Abbad04/dual-robot-choreography-studio/releases/tag/fr5-connector-v1.0.3
[1.0.2]: https://github.com/Abbad04/dual-robot-choreography-studio/releases/tag/fr5-connector-v1.0.2
[1.0.1]: https://github.com/Abbad04/dual-robot-choreography-studio/releases/tag/fr5-connector-v1.0.1
[1.0.0]: https://github.com/Abbad04/dual-robot-choreography-studio/releases/tag/fr5-connector-v1.0.0
