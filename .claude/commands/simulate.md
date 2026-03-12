---
description: Simulate a CASA Measurement Set from a natural-language observation description.
allowed-tools: Bash, Read, Write, Edit
---

Simulate a CASA Measurement Set based on the following description: $ARGUMENTS

Follow the ms-simulator skill protocol:

1. **Parse** the description to extract telescope, config, band/frequency,
   source(s), duration, and any corruption requests.
2. **Fill defaults** for anything not specified (see 01-conversation-protocol.md).
3. **Present the simulation plan** as a parameter summary and wait for
   confirmation (unless the user said "just do it" or similar).
4. **Generate** a complete Python script using `casatools.simulator`.
5. **Execute** the script and report the result.
6. **Validate** the output MS using ms_inspect tools or listobs.
