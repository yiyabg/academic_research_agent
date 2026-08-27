"""Compatibility composition root for local paper-library endpoints.

Public paths remain unchanged.  Transport concerns are separated into
``local_library_routes`` so this module cannot grow into an application layer.
"""

from app.api.routes.v1.literature_research.local_library_routes import router

__all__ = ["router"]
