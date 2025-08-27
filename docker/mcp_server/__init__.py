# mcp_server/__init__.py
__all__ = ["get_app", "__version__"]

__version__ = "0.1.0"

from importlib.metadata import version, PackageNotFoundError

try:                      # If installed as a package
    __version__ = version(__name__)
except PackageNotFoundError:
    pass


def get_app():
    """Return the FastAPI application (used by uvicorn)."""
    from .main import app  # pylint: disable=import-outside-toplevel
    return app