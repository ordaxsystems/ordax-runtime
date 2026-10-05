# ORDAX Studio surface boundary

## Purpose

ORDAX Studio is a first-class work surface for AI-assisted projects. It is not a second operating-system shell and it must not duplicate the global navigation owned by `prototipo-ordax-os`.

## Ownership

### OrdaX OS owns

- global app navigation and launcher;
- Files, Internet, Network, Settings, Account and System surfaces;
- OS Identity, Spaces, global Permissions, Intelligence/Model Router and Memory authority;
- the first-party outer shell when Studio is mounted inside OrdaX OS.

### ORDAX Studio owns

- project/workspace selection;
- project-scoped files, Git, search and execution views;
- project continuity and memory views backed by the canonical runtime contracts;
- MCP/device capability visibility and audit;
- the main project work area and project preview/context pane;
- specialized project capabilities such as Blender and Unity, without making them the product identity.

## Hosts

The Windows WPF application is a native **standalone host**. It owns lifecycle, native WebView hosting and auxiliary tools, but must keep the shared Studio surface as the primary canvas.

OrdaX OS does **not** embed the Windows WPF shell. It consumes the headless Runtime/capability contracts and mounts the Studio surface inside the OS-owned shell.

The two hosts may differ in chrome, but they must not fork project semantics, permissions, Runtime state, grants, memory or capability contracts.

## AI providers

Provider web views are auxiliary user-visible surfaces. They are not an automation shortcut and must not be scraped or impersonated as a provider API. Remote model control uses the authenticated MCP/action path and explicit grants. Provider/account state shown in Studio must come from real connector/runtime state; no fake connected accounts, fake conversations or hardcoded availability.

## UX rules

1. Open into the user's project/work context, not an administrative dashboard.
2. Keep project navigation on the left, work/AI context in the center and preview/context on the right when screen size permits.
3. Keep Runtime/MCP/grant health visible but secondary; diagnostics must not dominate normal work.
4. Do not reproduce the OrdaX OS global sidebar inside Studio.
5. Do not create a parallel permission system or parallel Runtime for a visual feature.
6. Specialized adapters remain subordinate capabilities until the general Computer Control gate is complete.
7. Empty states are allowed; fabricated project data or provider activity is not.

## Canonical implementation

- Shared project surface: `ordax_studio/studio_product.html` + `ordax_studio/assets/studio.css` + `ordax_studio/assets/studio.js`; owner-local Computer Control settings live in the dedicated `computer_access.js/.css` surface module.
- Windows native host: `native/ordax-workbench/`.
- Headless local capability runtime: `ordax_dev_agent` / `ordax_device_agent`.
- Remote authority: Product Action Gateway + MCP + Cloudflare control plane + grants.
