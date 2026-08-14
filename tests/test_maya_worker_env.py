from __future__ import annotations

import os
import sysconfig

from cgi_pipeline.hosts.maya.worker import _build_env
from cgi_pipeline.paths import SOURCE_ROOT


def test_mayapy_inherits_managed_brain_site_packages(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", r"C:\existing")
    monkeypatch.setenv("PYTHONNOUSERSITE", "0")

    env = _build_env("test-worker")

    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PYTHONPATH"].split(os.pathsep) == [
        sysconfig.get_paths()["purelib"],
        str(SOURCE_ROOT),
        r"C:\existing",
    ]
