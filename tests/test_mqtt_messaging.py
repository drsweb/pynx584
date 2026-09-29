"""Tests for MqttMessaging with mocked paho-mqtt client."""

import sys
import unittest
from unittest.mock import MagicMock, patch

# Import MqttMessaging normally (paho-mqtt is installed)
from nx584.mqtt_messaging import MqttMessaging, _HAS_PAHO


class MockController:
    """Minimal mock controller for testing."""

    def __init__(self):
        self.system = MagicMock()
        self.system.panel_id = 12345
        self.system.status_flags = ['AC fail']
        self.partitions = {}
        self.zones = {}
        self._partition_state_calls = []
        # Use MagicMock so methods are mockable
        self.arm_exit = MagicMock()
        self.arm_stay = MagicMock()
        self.disarm = MagicMock()
        self.zone_bypass_toggle = MagicMock()
        self.set_time = MagicMock()
        self.publish_all = MagicMock()

    def get_partition_state_string(self, partition):
        self._partition_state_calls.append(partition.number)
        return 'disarmed'


@unittest.skipUnless(_HAS_PAHO, 'paho-mqtt not installed')
class TestMqttMessaging(unittest.TestCase):
    """Tests for MqttMessaging class."""

    def setUp(self):
        """Create a MqttMessaging instance with mocked client."""
        self.mock_client = MagicMock()

        with patch('paho.mqtt.client.Client') as mock_client_class:
            mock_client_class.return_value = self.mock_client

            self.mock_controller = MockController()
            with patch('nx584.mqtt_messaging._controller', self.mock_controller):
                self.mqtt = MqttMessaging(
                    host='localhost',
                    port=1883,
                    state_topic_root='home/alarm',
                    command_topic='home/alarm/set',
                )
                # Simulate successful connection
                self.mock_client.on_connect(self.mock_client, None, None, 0)

    def test_mqtt_connect_calls_loop_start(self):
        """connect() starts background loop."""
        self.mock_client.loop_start.assert_called_once()

    def test_mqtt_on_connect_subscribes(self):
        """on_connect() subscribes to command topic."""
        self.mock_client.subscribe.assert_called_with('home/alarm/set')

    def test_mqtt_on_connect_sets_lwt(self):
        """on_connect() sets Last Will message."""
        self.mock_client.will_set.assert_called_with(
            'home/alarm/system/avail', 'offline', qos=1, retain=True)

    def test_mqtt_publish_sends_to_broker(self):
        """publish() calls client.publish() with correct topic/payload."""
        self.mqtt.publish('test/topic', 'hello', qos=1, retain=True)
        self.mock_client.publish.assert_called_with('test/topic', 'hello', 1, True)

    def test_mqtt_publish_skipped_when_disconnected(self):
        """publish() does nothing when not connected."""
        self.mqtt.connected = False
        self.mock_client.reset_mock()
        self.mqtt.publish('test/topic', 'hello')
        self.mock_client.publish.assert_not_called()

    def test_mqtt_on_message_arm_away(self):
        """on_message() dispatches arm_away command."""
        msg = MagicMock()
        msg.payload = b'arm_away,1'
        with patch('nx584.mqtt_messaging._controller', self.mock_controller):
            self.mqtt.on_message(self.mock_client, None, msg)
        self.mock_controller.arm_exit.assert_called_with(1)

    def test_mqtt_on_message_arm_home(self):
        """on_message() dispatches arm_home command."""
        msg = MagicMock()
        msg.payload = b'arm_home,2'
        with patch('nx584.mqtt_messaging._controller', self.mock_controller):
            self.mqtt.on_message(self.mock_client, None, msg)
        self.mock_controller.arm_stay.assert_called_with(2)

    def test_mqtt_on_message_disarm(self):
        """on_message() dispatches disarm command."""
        msg = MagicMock()
        msg.payload = b'disarm,1,1234'
        with patch('nx584.mqtt_messaging._controller', self.mock_controller):
            self.mqtt.on_message(self.mock_client, None, msg)
        self.mock_controller.disarm.assert_called_with('1234', 1)

    def test_mqtt_on_message_bypass_toggle(self):
        """on_message() dispatches bypass_toggle command."""
        msg = MagicMock()
        msg.payload = b'bypass_toggle,3'
        with patch('nx584.mqtt_messaging._controller', self.mock_controller):
            self.mqtt.on_message(self.mock_client, None, msg)
        self.mock_controller.zone_bypass_toggle.assert_called_with(3)

    def test_mqtt_on_message_time(self):
        """on_message() dispatches time command."""
        msg = MagicMock()
        msg.payload = b'time'
        with patch('nx584.mqtt_messaging._controller', self.mock_controller):
            self.mqtt.on_message(self.mock_client, None, msg)
        self.mock_controller.set_time.assert_called_once()

    def test_mqtt_on_message_status(self):
        """on_message() dispatches status command."""
        msg = MagicMock()
        msg.payload = b'status'
        with patch('nx584.mqtt_messaging._controller', self.mock_controller):
            self.mqtt.on_message(self.mock_client, None, msg)
        self.mock_controller.publish_all.assert_called_once()

    def test_mqtt_on_message_nop(self):
        """on_message() ignores nop command."""
        msg = MagicMock()
        msg.payload = b'nop'
        # Should not raise
        self.mqtt.on_message(self.mock_client, None, msg)

    def test_mqtt_on_message_unknown(self):
        """on_message() logs error for unknown command."""
        msg = MagicMock()
        msg.payload = b'unknown_command'
        # Should not raise, just log
        self.mqtt.on_message(self.mock_client, None, msg)

    def test_mqtt_on_message_clears_pin_after_arm(self):
        """on_message() publishes nop after arm/disarm."""
        msg = MagicMock()
        msg.payload = b'arm_away,1'
        with patch('nx584.mqtt_messaging._controller', self.mock_controller):
            self.mqtt.on_message(self.mock_client, None, msg)
        calls = [c for c in self.mock_client.publish.call_args_list
                 if c[0][0] == 'home/alarm/set' and c[0][1] == 'nop']
        self.assertTrue(len(calls) > 0, 'nop should be published after arm_away')

    def test_mqtt_topic_construction(self):
        """All topic builder methods produce correct topics."""
        self.assertEqual(self.mqtt._zone_state_topic(1),
                         'home/alarm/zones/1/faulted')
        self.assertEqual(self.mqtt._zone_bypassed_topic(2),
                         'home/alarm/zones/2/bypassed')
        self.assertEqual(self.mqtt._zone_types_topic(3),
                         'home/alarm/zones/3/types')
        self.assertEqual(self.mqtt._zone_conditions_topic(4),
                         'home/alarm/zones/4/conditions')
        self.assertEqual(self.mqtt._zone_name_topic(5),
                         'home/alarm/zones/5/name')
        self.assertEqual(self.mqtt._partition_state_topic(1),
                         'home/alarm/partitions/1/state')
        self.assertEqual(self.mqtt._partition_conditions_topic(2),
                         'home/alarm/partitions/2/conditions')
        self.assertEqual(self.mqtt._system_id_topic(),
                         'home/alarm/system/id')
        self.assertEqual(self.mqtt._system_info_topic(),
                         'home/alarm/system/info')
        self.assertEqual(self.mqtt._system_datetime_topic(),
                         'home/alarm/system/datetime')
        self.assertEqual(self.mqtt._system_avail_topic(),
                         'home/alarm/system/avail')

    def test_mqtt_publish_all_full_sync(self):
        """publish_all() publishes all state topics."""
        mock_partition = MagicMock()
        mock_partition.number = 1
        mock_partition.condition_flags = ['Armed']
        self.mock_controller.partitions[1] = mock_partition

        mock_zone = MagicMock()
        mock_zone.number = 1
        mock_zone.name = 'Test Zone'
        mock_zone.type_flags = ['Fire']
        mock_zone.condition_flags = ['Faulted']
        mock_zone.bypassed = False
        mock_zone.state = True
        self.mock_controller.zones[1] = mock_zone

        with patch('nx584.mqtt_messaging._controller', self.mock_controller):
            self.mqtt.publish_all()

        self.mock_client.publish.assert_any_call(
            'home/alarm/system/id', '12345', 1, True)
        self.mock_client.publish.assert_any_call(
            'home/alarm/partitions/1/state', 'disarmed', 1, True)
        self.mock_client.publish.assert_any_call(
            'home/alarm/zones/1/name', 'Test Zone', 1, True)

    def test_mqtt_system_datetime_increments_counter(self):
        """publish_system_datetime() increments sync counter."""
        initial = self.mqtt._dt_sync_counter
        self.mqtt.publish_system_datetime()
        self.assertEqual(self.mqtt._dt_sync_counter, initial + 1)

    def test_mqtt_system_avail_online_at_counter_3(self):
        """publish_system_avail publishes 'online' when counter reaches 3."""
        self.mqtt._dt_sync_counter = 0
        self.mqtt.publish_system_datetime()
        self.mqtt.publish_system_datetime()
        self.mqtt.publish_system_datetime()
        avail_calls = [c for c in self.mock_client.publish.call_args_list
                       if c[0][0] == 'home/alarm/system/avail'
                       and c[0][1] == 'online']
        self.assertTrue(len(avail_calls) > 0,
                        "'online' should be published at counter == 3")


if __name__ == '__main__':
    unittest.main()
