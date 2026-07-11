# Security

## The control socket

The Remote Script opens a TCP socket on `127.0.0.1:9877` so the MCP server can send
commands to Ableton Live. There is no authentication on this socket. Any local
process can connect to it and drive Live while the script is loaded.

To keep this safe:

- The socket binds to loopback (`127.0.0.1`) by default, so it is not reachable from
  the network. Do not change the bind address to a public interface and do not
  forward the port.
- The bind address can be overridden with the `ABLETON_MCP_HOST` environment
  variable for advanced setups. Only do this if you understand the exposure and have
  other controls in place.

## Reporting a vulnerability

Open a private security advisory on the GitHub repository, or a regular issue if the
report is not sensitive.
