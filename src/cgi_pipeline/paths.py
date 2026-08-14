"""Stable paths for the source package and repository-owned data."""

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_ROOT.parent
REPOSITORY_ROOT = PACKAGE_ROOT.parents[1]
CAPABILITIES_ROOT = PACKAGE_ROOT / "capabilities"
CONFIG_ROOT = REPOSITORY_ROOT / "config"
WORKFLOWS_ROOT = REPOSITORY_ROOT / "workflows"
INTEGRATIONS_ROOT = REPOSITORY_ROOT / "integrations"
