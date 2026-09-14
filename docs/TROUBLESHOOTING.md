# Troubleshooting

Start with the newest
[macOS connector release](https://github.com/Abbad04/dual-robot-choreography-studio/releases/latest)
and keep the extracted folder intact. Older connector errors are intentionally
not worked around with manual SDK download commands.

> [!CAUTION]
> If a failure could affect physical motion, stop testing, use the physical
> emergency stop when necessary, and make the workcell safe before diagnosing
> software.

## The app says “Connected read-only”

Motion was not enabled at connector startup. Close the connector and start
**Start FR5 Connector.command** again. At the safety prompt, type exactly
uppercase `YES`.

Do not start `bin/FR5-Connector-arm64` or `bin/FR5-Connector-x86_64` directly
for normal operation. A direct launch has no launcher confirmation and
therefore starts read-only by design.

## “I am beside the robot and ready” is disabled

The checkbox stays disabled until all of these are true:

- the browser is connected to the connector;
- the connector was launched with motion enabled;
- the controller reports a motion-ready safety state; and
- no physical playback is already active.

Restart with uppercase `YES`, then check robot power, enable state, safety
state, and emergency stop. Do not bypass the gate if the controller is not
ready.

## macOS opens Finder or blocks an architecture binary

Gatekeeper can approve the `.command` launcher and its inner binary
separately. Choose **Show in Finder**, Control-click the highlighted
architecture binary, choose **Open**, and confirm once. Close the read-only
Terminal that direct opening starts, then rerun **Start FR5 Connector.command**
and type uppercase `YES`.

## The Mac cannot reach the robot

The controller normally uses `192.168.58.2`. The Mac must use a different
address on the same private subnet, such as `192.168.58.100/24` or
`192.168.58.120/24`; it must never take the controller’s address.

1. Keep Wi-Fi connected if internet access is also needed.
2. In macOS Ethernet TCP/IP settings, configure a manual address on
   `192.168.58.0/24` with subnet mask `255.255.255.0`. Leave the router blank.
3. Open `http://192.168.58.2` in Safari. The controller page must load.
4. Enter `192.168.58.2`, not the Mac address, in the connector launcher.

If the controller uses another documented IP, use a non-conflicting Mac
address on that controller’s subnet instead.

## The app says the FAIRINO SDK could not download

This message came from an older connector package that prepared the SDK at
runtime. The current standalone package bundles and integrity-checks the
supported SDK sources and does not need a first-run download.

Delete the old extracted connector folder, download the
[latest ZIP](https://github.com/Abbad04/dual-robot-choreography-studio/releases/latest/download/FR5-Connector-macOS.zip),
extract the whole folder, and start its `.command` launcher. Do not reuse an
old Terminal window or inner binary.

## The app says the official FAIRINO SDK could not start

The current package can show this when the matched, verified SDK source cannot
execute on that Mac. First replace the entire old folder with a freshly
downloaded latest release and retry. If it remains, open a bug report with the
complete parenthesized exception text, Mac architecture, macOS version, and
controller WebApp version. Do not substitute a manually downloaded SDK file.

## “Trusted choreography frontend is invalid”

This was fixed in the current standalone package. Replace the old folder with
a fresh copy from the latest release. Do not copy only the new `.command` file
into an older folder.

## `LuaUpload failed with FAIRINO code -1`

Connector v1.0.3 fixed incomplete transfer of larger generated Lua programs.
Install the latest release and start its launcher again. Projects do not need
to be recreated.

If the error remains on v1.0.3 or later, stop physical testing and open a bug
report with the connector version, controller WebApp version, approximate
project duration or frame count, and the final Terminal error. Do not attach
the project if it contains confidential choreography.

## The connector is offline or the pairing code fails

- Leave exactly one connector window running.
- Use the newest code printed by the currently running connector. A restart
  invalidates the previous code.
- Enter the code in the FR5 connection panel and press **Connect**; refreshing
  the web app is not required.
- Keep the connector on its default loopback address and port,
  `127.0.0.1:8766`.
- Close stale direct-binary or read-only connector windows before retrying.

Never post an active pairing code in an issue, screenshot, chat, or log.

## Still blocked

Search the [existing issues](https://github.com/Abbad04/dual-robot-choreography-studio/issues)
or open a bug report. Include:

- connector release and Mac architecture;
- macOS version and browser;
- controller model, WebApp version, and safety state;
- exact reproduction steps; and
- the final relevant Terminal lines with pairing codes and private details
  removed.
