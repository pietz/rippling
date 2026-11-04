"""Unofficial command-line client for Rippling time tracking and leave."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("rippling")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0"
