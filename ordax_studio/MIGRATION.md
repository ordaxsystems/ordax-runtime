# Studio host compatibility during repository cutover

The Python ordax_studio package is retained here temporarily because the Windows launcher/installer still hosts the portable UI through this package.

Canonical portable UI/product source is ordaxsystems/ordax-apps/apps/studio. New portable product logic must not be added here. This compatibility package must be reduced to host adapters once the Windows package consumes the external Studio artifact.

