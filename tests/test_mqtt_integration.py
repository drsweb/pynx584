"""Integration tests for MQTT CLI, configuration, and backward compatibility.

Tests verify:
- CLI entry points (nx584_server, nx584_client) work correctly
- MQTT CLI switches (--mqtt, --mqttPort, --username, etc.) are present
- HTTP-only mode creates HttpMessaging only
- MQTT mode creates CompositeMessaging with proper config/CLI override behavior
- Error handling when MQTT is enabled but no broker host is available
- Backward compatibility (setup.py extras, paho-mqtt import, config.ini reference)
"""

import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch


class TestEntryPoints(unittest.TestCase):
    """Test that entry points work correctly."""

    def test_nx584_server_help(self):
        """nx584_server --help exits with code 0."""
        result = subprocess.run(
            [sys.executable, 'nx584_server', '--help'],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, 'nx584_server --help should exit 0')

    def test_nx584_client_help(self):
        """nx584_client --help exits with code 0."""
        result = subprocess.run(
            [sys.executable, '-m', 'nx584.client', '--help'],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, 'nx584_client --help should exit 0')

    def test_nx584_server_version(self):
        """nx584_server --version prints version and exits 0."""
        result = subprocess.run(
            [sys.executable, 'nx584_server', '--version'],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, 'nx584_server --version should exit 0')
        self.assertIn('0.8', result.stdout, 'should print version 0.8.x')

    def test_nx584_server_has_mqtt_switch(self):
        """nx584_server has --mqtt switch for enabling MQTT."""
        result = subprocess.run(
            [sys.executable, 'nx584_server', '--help'],
            capture_output=True, text=True
        )
        self.assertIn('--mqtt', result.stdout, 'should have --mqtt switch')
        self.assertIn('--logLevel', result.stdout, 'should have --logLevel switch')

    def test_nx584_server_has_mqtt_override_switches(self):
        """nx584_server has MQTT override switches."""
        result = subprocess.run(
            [sys.executable, 'nx584_server', '--help'],
            capture_output=True, text=True
        )
        self.assertIn('--mqttPort', result.stdout)
        self.assertIn('--username', result.stdout)
        self.assertIn('--password', result.stdout)
        self.assertIn('--stateTopicRoot', result.stdout)
        self.assertIn('--commandTopic', result.stdout)
        self.assertIn('--mqttTlsActive', result.stdout)
        self.assertIn('--mqttTlsInsecure', result.stdout)
        self.assertIn('--timeout', result.stdout)


class TestHTTPOnlyMode(unittest.TestCase):
    """Test HTTP-only mode (no MQTT enabled)."""

    def test_build_messaging_http_only(self):
        """_build_messaging with --mqtt=None creates HttpMessaging only."""
        from nx584.main import _build_messaging
        from nx584.http_messaging import HttpMessaging

        ctrl = MagicMock()
        ctrl._config = MagicMock()
        ctrl._config.has_section = MagicMock(return_value=False)

        args = MagicMock()
        args.mqtt = None

        _build_messaging(ctrl, None, args)

        self.assertIsInstance(ctrl.messaging, HttpMessaging)

    def test_build_messaging_with_mqtt_flag(self):
        """_build_messaging with --mqtt creates CompositeMessaging."""
        from nx584.main import _build_messaging
        from nx584.messaging import CompositeMessaging

        ctrl = MagicMock()
        ctrl._config = MagicMock()

        args = MagicMock()
        args.mqtt = 'localhost'
        args.mqttPort = None
        args.username = None
        args.password = None
        args.stateTopicRoot = None
        args.commandTopic = None
        args.mqttTlsActive = None
        args.mqttTlsInsecure = None
        args.timeout = None

        with patch('nx584.mqtt_messaging.MqttMessaging'):
            _build_messaging(ctrl, {'host': 'confighost'}, args)

            self.assertIsInstance(ctrl.messaging, CompositeMessaging)

    def test_build_messaging_cli_host_overrides_config(self):
        """CLI --mqtt HOST overrides config.ini [mqtt] host."""
        from nx584.main import _build_messaging

        ctrl = MagicMock()
        ctrl._config = MagicMock()

        args = MagicMock()
        args.mqtt = 'clihost'
        args.mqttPort = None
        args.username = None
        args.password = None
        args.stateTopicRoot = None
        args.commandTopic = None
        args.mqttTlsActive = None
        args.mqttTlsInsecure = None
        args.timeout = None

        with patch('nx584.mqtt_messaging.MqttMessaging') as mock_mqtt:
            _build_messaging(ctrl, {'host': 'confighost'}, args)
            call_args = mock_mqtt.call_args
            self.assertEqual(call_args.kwargs['host'], 'clihost',
                             'CLI host should override config.ini host')

    def test_build_messaging_error_when_no_host(self):
        """_build_messaging errors when --mqtt set but no host available."""
        from nx584.main import _build_messaging

        ctrl = MagicMock()
        ctrl._config = MagicMock()

        args = MagicMock()
        args.mqtt = True  # Flag set but no hostname
        args.mqttPort = None
        args.username = None
        args.password = None
        args.stateTopicRoot = None
        args.commandTopic = None
        args.mqttTlsActive = None
        args.mqttTlsInsecure = None
        args.timeout = None

        # No config.ini host either
        with patch('nx584.mqtt_messaging.MqttMessaging'):
            with self.assertRaises(SystemExit) as ctx:
                _build_messaging(ctrl, None, args)
            self.assertEqual(ctx.exception.code, 1)


class TestBackwardCompatibility(unittest.TestCase):
    """Test backward compatibility."""

    def test_paho_mqtt_import_works(self):
        """paho-mqtt can be imported (if installed)."""
        from nx584.mqtt_messaging import _HAS_PAHO
        self.assertTrue(_HAS_PAHO)  # paho-mqtt is installed in test env

    def test_setup_py_has_extras(self):
        """setup.py defines mqtt extra."""
        with open('setup.py', 'r') as f:
            content = f.read()
        self.assertIn("'mqtt':", content, 'setup.py should have mqtt extra')
        self.assertIn('paho-mqtt', content, 'setup.py should reference paho-mqtt')

    def test_mqtt_enabled_via_flag_and_config(self):
        """MQTT is enabled via --mqtt flag + config.ini [mqtt] section."""
        with open('nx584/main.py', 'r') as f:
            content = f.read()
        self.assertIn('--mqtt', content, 'main.py should have --mqtt CLI arg')
        self.assertIn('[mqtt]', content, 'main.py should reference [mqtt] config section')
        self.assertIn('nargs', content, '--mqtt should accept optional hostname')

    def test_loglevel_switch_present(self):
        """--logLevel switch is present for nx584mqtt parity."""
        with open('nx584/main.py', 'r') as f:
            content = f.read()
        self.assertIn('--logLevel', content, 'main.py should have --logLevel switch')


if __name__ == '__main__':
    unittest.main()
