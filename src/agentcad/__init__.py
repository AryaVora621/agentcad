"""agentcad: CAD for AI agents. Model parts in code, then prove them by measuring the built solids."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agentcad")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.2.0"

from .checks import CheckContext
from .model import Part, Pose
from .project import Project
from .report import Check, Report, Status

__all__ = ["Check", "CheckContext", "Part", "Pose", "Project", "Report", "Status", "__version__"]
