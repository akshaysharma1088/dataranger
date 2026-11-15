# Security policy

## What DataRanger touches

DataRanger reads the tables you point it at and writes its reports to a folder you choose. It never
modifies your input tables: fixes are written to a new version of the data. The Snowflake and dbt
connectors run read-only `SELECT` statements, tagged `dataranger` in query history. Nothing is sent
over the network unless you turn on an optional LLM feature, and the MCP server only exposes
read-only tools.

## Supported versions

Security fixes go into the latest release on PyPI. Please upgrade before reporting.

## Reporting a vulnerability

Please do not open a public issue. Report it privately through this repository's
**Security** tab, using **Report a vulnerability**. Only the maintainer can see these reports.

Include what you ran, what happened, and a small example that reproduces it if you can.

You will get a first reply within 7 days. Once a fix is ready it is released on PyPI, noted in
`CHANGELOG.md`, and credited to you unless you prefer otherwise.
