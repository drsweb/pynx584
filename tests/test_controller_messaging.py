"""Tests for controller messaging integration."""

import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from nx584 import model
from nx584.controller import NXController


class MockMessaging:
    """Mock messaging backend that records all calls."""

    def __init__(self):
        self.calls = []

    def _record(self, method_name, *args, **kwargs):
        self.calls.append((method_name, args, kwargs))

    def publish_zone_state(self, zone_number, state):
        self._record('publish_zone_state', zone_number, state)

    def publish_zone_bypassed(self, zone_number, state):
        self._record('publish_zone_bypassed', zone_number, state)

    def publish_zone_type_flags(self, zone_number, flags):
        self._record('publish_zone_type_flags', zone_number, flags)

    def publish_zone_condition_flags(self, zone_number, flags):
        self._record('publish_zone_condition_flags', zone_number, flags)

    def publish_zone_name(self, zone_number, name):
        self._record('publish_zone_name', zone_number, name)

    def publish_partition_state(self, partition_number, state):
        self._record('publish_partition_state', partition_number, state)

    def publish_partition_condition_flags(self, partition_number, flags):
        self._record('publish_partition_condition_flags', partition_number, flags)

    def publish_system_id(self, panel_id):
        self._record('publish_system_id', panel_id)

    def publish_system_info(self, flags):
        self._record('publish_system_info', flags)

    def publish_system_datetime(self):
        self._record('publish_system_datetime')

    def publish_system_avail(self, status):
        self._record('publish_system_avail', status)

    def publish_all(self):
        self._record('publish_all')


class TestControllerMessaging(unittest.TestCase):
    """Tests for controller messaging integration."""

    def setUp(self):
        """Create a controller with mock messaging."""
        # Create a controller without connecting to a panel
        self.ctrl = NXController.__new__(NXController)
        self.ctrl.messaging = MockMessaging()
        self.ctrl.initial_mqtt_publish_all_completed = False
        self.ctrl.system = model.System()
        self.ctrl.system.panel_id = 12345
        self.ctrl.system.status_flags = ['AC fail']
        self.ctrl.partitions = {}
        self.ctrl.zones = {}
        self.ctrl.extensions = []
        self.ctrl._config = MagicMock()
        self.ctrl.event_queue = MagicMock()

    def test_controller_uses_messaging_interface(self):
        """Controller calls self.messaging methods."""
        self.ctrl.publish_all()
        self.assertEqual(len(self.ctrl.messaging.calls), 1)
        self.assertEqual(self.ctrl.messaging.calls[0][0], 'publish_all')

    def test_controller_process_msg_4_publishes_zone(self):
        """process_msg_4() calls all zone publish methods."""
        # Set up a zone
        zone = model.Zone(1)
        zone.name = 'Test Zone'
        zone.condition_flags = ['Faulted']
        zone.type_flags = ['Fire']
        self.ctrl.zones[1] = zone

        # Create a mock frame
        frame = MagicMock()
        frame.data = [0, 0, 1, 0, 0, 1, 0, 0]  # zone 1, condition bit 0 (Faulted)

        self.ctrl.process_msg_4(frame)

        # Check that messaging methods were called
        method_names = [c[0] for c in self.ctrl.messaging.calls]
        self.assertIn('publish_zone_state', method_names)
        self.assertIn('publish_zone_bypassed', method_names)
        self.assertIn('publish_zone_condition_flags', method_names)
        self.assertIn('publish_zone_type_flags', method_names)

    @patch('nx584.controller.mail')
    def test_controller_process_msg_6_publishes_partition(self, mock_mail):
        """process_msg_6() calls partition publish methods."""
        # Set up a partition
        partition = model.Partition(1)
        partition.condition_flags = ['Armed']
        self.ctrl.partitions[1] = partition

        # First call should trigger publish_all (initial sync)
        frame = MagicMock()
        frame.data = [0, 1, 0, 0, 0, 0, 0, 0]  # partition 1, Armed flag

        self.ctrl.process_msg_6(frame)

        # Check that publish_all was called (initial sync)
        method_names = [c[0] for c in self.ctrl.messaging.calls]
        self.assertIn('publish_all', method_names)

        # Reset and call again (should be incremental)
        self.ctrl.messaging.calls = []
        self.ctrl.initial_mqtt_publish_all_completed = True
        partition.condition_flags = ['Armed', 'Entryguard (stay mode)']
        self.ctrl.process_msg_6(frame)

        method_names = [c[0] for c in self.ctrl.messaging.calls]
        self.assertIn('publish_partition_state', method_names)
        self.assertIn('publish_partition_condition_flags', method_names)

    def test_controller_publish_all_delegates(self):
        """controller.publish_all() delegates to messaging.publish_all()."""
        self.ctrl.publish_all()
        self.assertEqual(len(self.ctrl.messaging.calls), 1)
        self.assertEqual(self.ctrl.messaging.calls[0][0], 'publish_all')

    def test_controller_get_partition_state_string(self):
        """Returns correct state strings."""
        # Armed + armedHome
        p1 = model.Partition(1)
        p1.condition_flags = ['Armed', 'Entryguard (stay mode)']
        self.assertEqual(self.ctrl.get_partition_state_string(p1), 'armed_home')

        # Armed only
        p2 = model.Partition(2)
        p2.condition_flags = ['Armed']
        self.assertEqual(self.ctrl.get_partition_state_string(p2), 'armed_away')

        # Disarmed
        p3 = model.Partition(3)
        p3.condition_flags = []
        self.assertEqual(self.ctrl.get_partition_state_string(p3), 'disarmed')


if __name__ == '__main__':
    unittest.main()
