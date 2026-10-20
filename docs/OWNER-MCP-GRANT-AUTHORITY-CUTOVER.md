# Owner grant cutover gate

This cutover is complete only when Runtime callers use the canonical owner remote-authority and remote-grant-group endpoints and no production source references retired Product device-link or specialized grant routes. The Studio authorization surface must select a real consented Product MCP client returned by the owner authority and must fail closed when the client is ambiguous.
