"""Conflicting target inputs never get silently overwritten."""
import pytest
from cgi_pipeline.capabilities.maya.display_inheritance import apply_state


class Cmds:
    def __init__(self, incoming=(), locked=False):
        self.value = 1
        self.incoming = list(incoming)
        self.locked = locked
        self.writes = []
    def objExists(self, plug):
        return plug.endswith('.visibility')
    def listConnections(self, *args, **kwargs):
        return self.incoming
    def getAttr(self, plug, **kwargs):
        return self.locked if kwargs.get('lock') else self.value
    def setAttr(self, plug, *values, **kwargs):
        self.writes.append((plug, values, kwargs))
        if 'lock' in kwargs:
            self.locked = kwargs['lock']
        else:
            self.value = values[0]
    def connectAttr(self, source, target):
        self.incoming = [source]


def test_conflicting_driver_is_rejected_without_write():
    cmds = Cmds(['other.visibility'])
    with pytest.raises(ValueError, match='Conflicting'):
        apply_state(cmds, {'visibility': {'value': 1, 'inputs': ['control.visibility']}}, '|target')
    assert not cmds.writes


def test_static_value_copied_and_lock_restored():
    cmds = Cmds(locked=True)
    apply_state(cmds, {'visibility': {'value': 0, 'inputs': []}}, '|target')
    assert cmds.value == 0 and cmds.locked


def test_matching_driver_is_noop():
    cmds = Cmds(['control.visibility'])
    apply_state(cmds, {'visibility': {'value': 1, 'inputs': ['control.visibility']}}, '|target')
    assert not cmds.writes
