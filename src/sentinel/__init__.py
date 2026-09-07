# sentinel — macOS resource governor

from importlib.metadata import PackageNotFoundError, version as _metadata_version

__all__ = ["__version__"]

# Resolved from installed package metadata rather than duplicated here, so
# pyproject.toml stays the single source of truth. An editable install whose
# metadata was written before a version bump will report the older number —
# that is the honest answer: it is what the interpreter actually has installed.
try:
    __version__ = _metadata_version("sentinel")
except PackageNotFoundError:  # pragma: no cover - only when not installed at all
    # Running straight from a source checkout with no install of any kind.
    __version__ = "0+unknown"
