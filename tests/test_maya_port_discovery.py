import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cgi_pipeline.server import ports


class MayaPortDiscoveryTests(unittest.TestCase):
    def test_discovery_probes_in_parallel_and_keeps_port_order(self):
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_probe(port, timeout):
            nonlocal active, max_active
            self.assertEqual(timeout, 0.05)
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02 if port % 2 else 0.01)
            with lock:
                active -= 1
            return port in {7001, 7003}

        with patch.object(ports, "maya_scan_ports", return_value=list(range(7001, 7005))), patch.object(
            ports, "_probe_maya_port", side_effect=fake_probe
        ):
            result = ports.discover_maya_ports(timeout=0.05, max_workers=4)

        self.assertGreater(max_active, 1)
        self.assertEqual(result, [7001, 7003])

    def test_empty_scan_range_returns_immediately(self):
        with patch.object(ports, "maya_scan_ports", return_value=[]):
            self.assertEqual(ports.discover_maya_ports(), [])

    def test_explicit_ports_extend_range_without_widening_every_probe(self):
        with patch.dict('os.environ', {
            'MAYA_FOREGROUND_PORT_START': '7001',
            'MAYA_FOREGROUND_PORT_END': '7002',
            'MAYA_FOREGROUND_PORTS': '7123, 7002;7200',
        }, clear=False):
            self.assertEqual(ports.maya_scan_ports(), [7001, 7002, 7123, 7200])

    def test_invalid_explicit_port_fails_loudly(self):
        with patch.dict('os.environ', {'MAYA_FOREGROUND_PORTS': 'not-a-port'}, clear=False):
            with self.assertRaisesRegex(ValueError, 'invalid MAYA_FOREGROUND_PORTS'):
                ports.maya_scan_ports()


if __name__ == "__main__":
    unittest.main()
