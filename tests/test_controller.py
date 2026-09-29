import configparser
import io
import logging
import unittest
import tempfile
from unittest import mock

from nx584 import controller
from nx584.nx_protocol_adapter import NX584Adapter
from nx584.nxcore import fletcher
from nx584.nxcore import make_ascii

TESTFRAME = [1, 0x7e, 2, 0x7d, 3]
logging.basicConfig(level=logging.DEBUG)


class TestProtocols(unittest.TestCase):
    def test_write_ascii(self):
        stream = io.BytesIO()
        proto = controller.NXASCII(stream)

        # Write a test frame
        proto.write_frame(TESTFRAME)

        # Make sure that the stream got the actual bytes expected
        length = len(TESTFRAME)
        s1, s2 = fletcher([length] + TESTFRAME)
        self.assertEqual(b'\n%02x017E027D03%02x%02x\r' % (length, s1, s2),
                         stream.getvalue())

        # Reset the stream and read it back
        stream.seek(0)
        data = proto.read_frame()

        # Make sure we got the right length, data, and checksum
        self.assertEqual(data, [length] + TESTFRAME + [s1, s2])

    def test_write_binary(self):
        stream = io.BytesIO()
        proto = controller.NXBinary(stream)

        # Write a test frame
        proto.write_frame(TESTFRAME)

        # Make sure that the stream got the actual bytes expected
        length = len(TESTFRAME)
        s1, s2 = fletcher([length] + TESTFRAME)
        self.assertEqual(b'\x7e' + bytes([length]) +
                         b'\x01\x7d\x5e\x02\x7d\x5d\x03' + bytes([s1, s2]),
                         stream.getvalue())

        # Reset the stream and read it back
        stream.seek(0)
        data = proto.read_frame()

        # Make sure we got the right length, data, and checksum
        self.assertEqual(data, [length] + TESTFRAME + [s1, s2])


class SplitIO(io.BytesIO):
    def __init__(self, r, w):
        self.r = r
        self.w = w

    def read(self, n):
        return self.r.read(n)

    def write(self, b):
        return self.w.write(b)


def get_wrapper(config, use_nx587_device=False):
    rio = io.BytesIO()
    wio = io.BytesIO()

    class FakeWrapper(controller.StreamWrapper):
        def _connect(self):
            self._s = SplitIO(rio, wio)
            return True

    if use_nx587_device:
        config.get.side_effect = lambda section, key, *args: 'true' if key == 'use_nx587_device' else args[0]
    else:
        def config_get_side_effect(section, key, *args):
            if section == 'config' and key == 'use_nx587_device':
                raise configparser.NoOptionError(section, key)
            raise KeyError(f"Unexpected get call: ({section!r}, {key!r})")
        config.get.side_effect = config_get_side_effect

    return rio, wio, FakeWrapper('fakeport', config)


class TestController(unittest.TestCase):
    def setUp(self):
        with mock.patch('stevedore.extension.ExtensionManager'):
            with tempfile.NamedTemporaryFile() as f:
                self.ctrl = controller.NXController('fakeport', f.name)
                self.ctrl._adapter = NX584Adapter(self.ctrl)

    def _run_until_idle(self):
        def stop():
            self.ctrl.running = False

        self.ctrl.running = True
        self.ctrl._idle_time_heartbeat_seconds = 0.1
        self.ctrl._config.set('config', 'max_zone', '2')

        with mock.patch.object(self.ctrl, 'generate_heartbeat_activity',
                               side_effect=stop):
            self.ctrl.controller_loop()

    def _test_startup(self, binary):
        fake_config = mock.MagicMock()
        fake_config.getboolean.return_value = binary
        rio, wio, self.ctrl._ser = get_wrapper(fake_config)

        self._run_until_idle()

        expected = [
            '0128292A',   # system status
            '022400264E', # zone 1 status
            '022300254C', # zone 1 name
            '022401274F', # zone 2 status
            '022301264D', # zone 2 name
        ]

        return expected, wio

    def test_startup_ascii(self):
        expected, wio = self._test_startup(False)

        messages = wio.getvalue().split(b'\r')

        # First message is the time/date, which will vary. Make sure
        # it looks sane
        self.assertTrue(messages[0].startswith(b'\n073B'))

        # Check the rest of the messages to make sure they look like
        # we expect
        self.assertEqual([b'\n%s' % m.encode() for m in expected],
                         messages[1:-1])

    def test_startup_binary(self):
        expected, wio = self._test_startup(True)

        # Use the NXBinary proto adapter to read the stream so we can
        # compare to the same list of expected messages.
        wio.seek(0)
        proto = controller.NXBinary(wio)

        messages = []
        while True:
            try:
                frame = proto.read_frame()
                messages.append(make_ascii(frame))
            except controller.ReadTimeout:
                break

        # First message is the time/date, which will vary. Make sure
        # it looks sane
        self.assertTrue(messages[0].startswith('073B'))

        # Check the rest of the messages to make sure they look like
        # we expect
        self.assertEqual(expected, messages[1:])

    def _test_receive(self, binary):
        fake_config = mock.MagicMock()
        fake_config.getboolean.return_value = binary
        rio, wio, self.ctrl._ser = get_wrapper(fake_config)

        messages = [
            # Partitions status
            [0x87, 0x47, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
            # Partitions snapshot
            [0x86, 0x00, 0x68, 0x00, 0xE0, 0x40, 0x62, 0x04, 0x82, 0x02, 0x07],
            # Partitions status
            [0x87, 0x47, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
        ]

        if binary:
            proto = controller.NXBinary(rio)
        else:
            proto = controller.NXASCII(rio)

        for message in messages:
            proto.write_frame(message)
        rio.seek(0)

        self.ctrl.process_msg_7 = None
        with mock.patch.object(self.ctrl, 'process_msg_7') as pm:
            with mock.patch.object(self.ctrl, 'send_ack') as sa:
                self._run_until_idle()
                self.assertEqual(2, pm.call_count)
                sa.assert_has_calls([mock.call(), mock.call()])

        return rio.getvalue()

    def test_receive_ascii(self):
        buf = self._test_receive(False)
        print('Test buffer is %r' % buf)

    def test_receive_binary(self):
        buf = self._test_receive(True)
        print('Test buffer is %r' % list(buf))


class TestNX587Controller(unittest.TestCase):
    def setUp(self):
        with mock.patch('stevedore.extension.ExtensionManager'):
            with tempfile.NamedTemporaryFile() as f:
                self.ctrl = controller.NXController('fakeport', f.name)

    def _run_until_idle(self):
        def stop():
            self.ctrl.running = False

        self.ctrl.running = True
        self.ctrl._idle_time_heartbeat_seconds = 0.1
        self.ctrl._config.set('config', 'max_zone', '2')

        with mock.patch.object(self.ctrl, 'generate_heartbeat_activity',
                                side_effect=stop):
            self.ctrl.controller_loop()

    def _nx587_config_get(self, section, key=None, fallback=None):
        if section == 'nx587e' and key == 'command_delay_seconds':
            return '0.5'
        if section == 'config' and key == 'use_nx587_device':
            return 'true'
        if fallback is not None:
            return fallback
        raise KeyError(key)

    def _make_nx587_wrapper(self):
        fake_config = mock.MagicMock()
        fake_config.getboolean.return_value = False
        fake_config.get.return_value = 'true'
        fake_config.has_section.return_value = False
        fake_config.has_option.return_value = False
        fake_config.getint.return_value = 2
        fake_config.has_section.side_effect = lambda s: s == 'nx587e'
        fake_config.get.side_effect = self._nx587_config_get
        return get_wrapper(fake_config, use_nx587_device=True)

    def _setup_nx587_ctrl(self, fake_wrapper):
        self.ctrl.use_nx587_device = True
        self.ctrl._ser = fake_wrapper
        self.ctrl._adapter = controller.NX587Adapter(
            fake_wrapper.protocol, fake_wrapper._config, 0.5)

    def test_nx587_protocol_selection(self):
        from nx584.nx587 import NX587Protocol
        rio, wio, fake_wrapper = self._make_nx587_wrapper()
        self.assertIsInstance(fake_wrapper.protocol, NX587Protocol)

    def test_nx587_frame_dispatch(self):
        rio, wio, fake_wrapper = self._make_nx587_wrapper()
        self.ctrl.use_nx587_device = True
        self.ctrl._ser = fake_wrapper
        self.ctrl._adapter = controller.NX587Adapter(fake_wrapper.protocol,
                                                      fake_wrapper._config, 0.5)

        zone_body = 'ZN001ftttbailb'
        parsed = fake_wrapper.protocol.parse_message(zone_body)
        frame = controller.NXFrame()
        frame.msgtype = 0xFF
        frame.data = parsed
        frame.checksum = 0
        frame.ackreq = False

        self.ctrl.process_nx587_frame(frame)

        self.assertIn(1, self.ctrl.zones)
        zone = self.ctrl.zones[1]
        self.assertFalse(zone.state)
        self.assertEqual('Unknown', zone.name)

    def test_nx587_zone_status_processing(self):
        rio, wio, fake_wrapper = self._make_nx587_wrapper()
        self.ctrl.use_nx587_device = True
        self.ctrl._ser = fake_wrapper

        zone_body = 'ZN003ALARM SENSOR    FTtBaillb'
        parsed = fake_wrapper.protocol.parse_message(zone_body)
        frame = controller.NXFrame()
        frame.msgtype = 0xFF
        frame.data = parsed
        frame.checksum = 0
        frame.ackreq = False

        self.ctrl.process_nx587_zone_status(frame)

        self.assertIn(3, self.ctrl.zones)
        zone = self.ctrl.zones[3]
        self.assertTrue(zone.state)
        self.assertEqual('ALARM SENSOR', zone.name)
        self.assertIn('Faulted', zone.condition_flags)
        self.assertIn('Tamper', zone.condition_flags)
        self.assertIn('Bypass', zone.condition_flags)

    def test_nx587_partition_status_processing(self):
        rio, wio, fake_wrapper = self._make_nx587_wrapper()
        self.ctrl.use_nx587_device = True
        self.ctrl._ser = fake_wrapper

        part_body = 'PA1RASCEEPS'
        parsed = fake_wrapper.protocol.parse_message(part_body)
        frame = controller.NXFrame()
        frame.msgtype = 0xFF
        frame.data = parsed
        frame.checksum = 0
        frame.ackreq = False

        self.ctrl.process_nx587_partition_status(frame)

        self.assertIn(1, self.ctrl.partitions)
        part = self.ctrl.partitions[1]
        self.assertTrue(part.armed)
        self.assertIn('Ready to arm', part.condition_flags)
        self.assertIn('Armed', part.condition_flags)
        self.assertIn('Entryguard (stay mode)', part.condition_flags)
        self.assertIn('Chime mode on', part.condition_flags)
        self.assertIn('Entry', part.condition_flags)
        self.assertIn('Exit 1', part.condition_flags)
        self.assertIn('Previous alarm', part.condition_flags)
        self.assertIn('Siren on', part.condition_flags)

    def test_nx587_startup_sequence(self):
        rio, wio, fake_wrapper = self._make_nx587_wrapper()
        self._setup_nx587_ctrl(fake_wrapper)
        self.ctrl._config.set('config', 'max_zone', '2')

        with mock.patch('time.sleep'):
            self.ctrl._adapter.startup_sequence()

        written = wio.getvalue()
        # Verify sent frames (no LF/CR wrapping for NX587E)
        self.assertIn(b'taLIPZN', written)
        self.assertIn(b'Q001', written)
        self.assertIn(b'Q002', written)
        self.assertIn(b'Q193', written)
        self.assertIn(b'Q194', written)
        # Verify frame order: toggles before queries
        toggle_pos = written.index(b'taLIPZN')
        q001_pos = written.index(b'Q001')
        self.assertLess(toggle_pos, q001_pos)

    def test_nx587_heartbeat(self):
        rio, wio, fake_wrapper = self._make_nx587_wrapper()
        self._setup_nx587_ctrl(fake_wrapper)

        self.ctrl.generate_heartbeat_activity()

        written = wio.getvalue()
        self.assertIn(b'Q201', written)

    def test_nx587_drain_queue_noop(self):
        rio, wio, fake_wrapper = self._make_nx587_wrapper()
        self._setup_nx587_ctrl(fake_wrapper)
        self.ctrl._queue = [1, 2, 3]

        self.ctrl._drain_queue()

        self.assertEqual([1, 2, 3], self.ctrl._queue)

    def test_nx587_system_status_none_flags(self):
        rio, wio, fake_wrapper = self._make_nx587_wrapper()
        self._setup_nx587_ctrl(fake_wrapper)

        self.ctrl._notify_extensions('system_status', self.ctrl.system)

        self.assertIsNone(self.ctrl.system.ac_fail)
        self.assertIsNone(self.ctrl.system.low_battery)
        self.assertIsNone(self.ctrl.system.phone_fault)

    def test_nx587_extension_callbacks(self):
        with mock.patch('stevedore.extension.ExtensionManager') as em_mock:
            mock_ext = mock.MagicMock()
            mock_ext.obj = mock.MagicMock()
            mock_ext.obj.zone_status = mock.MagicMock()
            mock_ext.obj.partition_status = mock.MagicMock()
            mock_ext.obj.system_status = mock.MagicMock()
            mock_ext.obj.device_command = mock.MagicMock()
            mock_ext.obj.log_event = mock.MagicMock()

            class FakeExtMgr:
                def __init__(self, *a, **k):
                    pass
                def names(self):
                    return ['test']
                def __getitem__(self, key):
                    return mock_ext

            em_mock.return_value = FakeExtMgr()

            with tempfile.NamedTemporaryFile() as f:
                ctrl = controller.NXController('fakeport', f.name)

            ctrl.use_nx587_device = True

            class MockNX587Adapter:
                def notify_extensions(self, callback_name, *args):
                    if callback_name in ('device_command', 'log_event'):
                        return False
                    if callback_name == 'system_status' and args:
                        system = args[0]
                        for flag in ('panel_id', 'ac_fail', 'low_battery',
                                     'phone_fault', 'power_fail',
                                     'system_trouble', 'panel_cleared',
                                     'local_alarm', 'remote_alarm',
                                     'phone_tier_1', 'phone_tier_2',
                                     'fire_alarm', 'burglar_alarm',
                                     'gas_alarm', 'water_alarm',
                                     'freeze_alarm', 'trouble_alarm',
                                     'supervisory_alarm', 'state_alarm'):
                            if hasattr(system, flag):
                                setattr(system, flag, None)
                    return True

            ctrl._adapter = MockNX587Adapter()

            zone = controller.model.Zone(1)
            ctrl._notify_extensions('zone_status', zone)
            mock_ext.obj.zone_status.assert_called_once_with(zone)

            partition = controller.model.Partition(1)
            ctrl._notify_extensions('partition_status', partition)
            mock_ext.obj.partition_status.assert_called_once_with(partition)

            ctrl._notify_extensions('system_status', ctrl.system)
            mock_ext.obj.system_status.assert_called_once_with(ctrl.system)
            self.assertIsNone(ctrl.system.ac_fail)

            ctrl._notify_extensions('device_command', 'A', 1, 'on')
            mock_ext.obj.device_command.assert_not_called()

            ctrl._notify_extensions('log_event', mock.MagicMock())
            mock_ext.obj.log_event.assert_not_called()


class TestBackwardCompatibility(unittest.TestCase):
    def setUp(self):
        with mock.patch('stevedore.extension.ExtensionManager'):
            with tempfile.NamedTemporaryFile() as f:
                self.ctrl = controller.NXController('fakeport', f.name)
                self.ctrl._adapter = NX584Adapter(self.ctrl)

    def _run_until_idle(self):
        def stop():
            self.ctrl.running = False

        self.ctrl.running = True
        self.ctrl._idle_time_heartbeat_seconds = 0.1
        self.ctrl._config.set('config', 'max_zone', '2')

        with mock.patch.object(self.ctrl, 'generate_heartbeat_activity',
                                side_effect=stop):
            self.ctrl.controller_loop()

    def test_nx584_ascii_still_works(self):
        fake_config = mock.MagicMock()
        fake_config.getboolean.return_value = False
        rio, wio, self.ctrl._ser = get_wrapper(fake_config)

        proto = controller.NXASCII(rio)
        proto.write_frame([0x08, 0x01, 0x28, 0x29, 0x2A, 0x00, 0x00, 0x00, 0x00, 0x00])
        proto.write_frame([0x04, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        proto.write_frame([0x03, 0x00] + list(b'ZONE 1            '))
        rio.seek(0)

        self._run_until_idle()

        self.assertEqual(self.ctrl.system.panel_id, 1)
        self.assertIn(1, self.ctrl.zones)

    def test_nx584_binary_still_works(self):
        fake_config = mock.MagicMock()
        fake_config.getboolean.return_value = True
        rio, wio, self.ctrl._ser = get_wrapper(fake_config)

        proto = controller.NXBinary(rio)
        proto.write_frame([0x08, 0x01, 0x28, 0x29, 0x2A, 0x00, 0x00, 0x00, 0x00, 0x00])
        proto.write_frame([0x04, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        proto.write_frame([0x03, 0x00] + list(b'ZONE 1            '))
        rio.seek(0)

        self._run_until_idle()

        self.assertEqual(self.ctrl.system.panel_id, 1)
        self.assertIn(1, self.ctrl.zones)

    def test_nx584_system_status_not_none(self):
        fake_config = mock.MagicMock()
        fake_config.getboolean.return_value = False
        rio, wio, self.ctrl._ser = get_wrapper(fake_config)

        system_frame = '0128292A\r'
        rio.write(system_frame.encode('latin-1'))
        rio.seek(0)

        self._run_until_idle()

        self.assertFalse(self.ctrl.system.ac_fail)
        self.assertFalse(self.ctrl.system.low_battery)
        self.assertFalse(self.ctrl.system.phone_fault)

    def test_nx584_log_events_still_work(self):
        fake_config = mock.MagicMock()
        fake_config.getboolean.return_value = False
        rio, wio, self.ctrl._ser = get_wrapper(fake_config)

        proto = controller.NXASCII(rio)
        proto.write_frame([0x0a, 0x01, 0x00, 0x01, 0x01, 0x08, 0x15, 0x10, 0x1e, 0x00])
        rio.seek(0)

        self.ctrl.process_msg_10 = None
        with mock.patch.object(self.ctrl, 'process_msg_10') as pm:
            self._run_until_idle()
            pm.assert_called_once()
