"""Unit tests for NX587E protocol implementation.

Tests frame encoding/decoding, message parsing, command sequences,
and feature toggles for the NX587E Virtual LCD Keypad protocol.
"""
import io
import unittest
from unittest import mock

from nx584.nx587 import NX587Protocol
from nx584.nxcore import NXFrame


class MockStream(object):
    """Mock stream for testing NX587Protocol read/write."""

    def __init__(self, data=b''):
        self._data = data
        self._pos = 0
        self.written = b''

    def read(self, n):
        result = self._data[self._pos:self._pos + n]
        self._pos += n
        if not result:
            from nx584.nxcore import ReadTimeout
            raise ReadTimeout()
        return result

    def write(self, data):
        self.written += data


class TestNX587Protocol(unittest.TestCase):
    """Test NX587Protocol class."""

    def setUp(self):
        self.stream = MockStream()
        self.config = mock.MagicMock()
        self.config.getfloat.return_value = 0.5
        self.proto = NX587Protocol(self.stream, self.config)

    # --- Frame Encoding/Decoding ---

    def test_encode_frame(self):
        """Verify LF + body + CR encoding for various bodies."""
        result = self.proto.encode_frame('ZN017FttBaillb')
        self.assertEqual(b'\x0aZN017FttBaillb\x0d', result)

    def test_encode_frame_simple(self):
        """Verify encoding of a simple body."""
        result = self.proto.encode_frame('Q001')
        self.assertEqual(b'\x0aQ001\x0d', result)

    def test_encode_frame_single_char(self):
        """Verify encoding of a single character."""
        result = self.proto.encode_frame('S')
        self.assertEqual(b'\x0aS\x0d', result)

    def test_decode_frame(self):
        """Verify LF/CR frame decoding."""
        result = self.proto.decode_frame(b'\x0aZN017FttBaillb\x0d')
        self.assertEqual('ZN017FttBaillb', result)

    def test_decode_frame_simple(self):
        """Verify decoding of a simple frame."""
        result = self.proto.decode_frame(b'\x0aQ001\x0d')
        self.assertEqual('Q001', result)

    def test_decode_frame_invalid_no_lf(self):
        """Verify ValueError for frame without LF start."""
        with self.assertRaises(ValueError):
            self.proto.decode_frame(b'ZN017FttBaillb\x0d')

    def test_decode_frame_invalid_no_cr(self):
        """Verify ValueError for frame without CR end."""
        with self.assertRaises(ValueError):
            self.proto.decode_frame(b'\x0aZN017FttBaillb')

    def test_decode_frame_empty(self):
        """Verify ValueError for empty frame."""
        with self.assertRaises(ValueError):
            self.proto.decode_frame(b'')

    def test_encode_decode_roundtrip(self):
        """Encode then decode produces original body."""
        bodies = [
            'ZN017FttBaillb',
            'Q001',
            'S',
            'E',
            'B7',
            '*131234S',
            'PA1RasCeEps',
            'LIVING ROOM PIR fTtbaillb',
        ]
        for body in bodies:
            encoded = self.proto.encode_frame(body)
            decoded = self.proto.decode_frame(encoded)
            self.assertEqual(body, decoded)

    def test_write_frame(self):
        """Verify write_frame sends correct bytes to stream."""
        frame = NXFrame()
        frame.msgtype = 0xFF
        frame.data = 'Q001'
        frame.checksum = 0
        frame.ackreq = False
        self.proto.write_frame(frame)
        self.assertEqual(b'Q001', self.stream.written)

    def test_write_frame_with_dict_data(self):
        """Verify write_frame with dict data uses _body key."""
        frame = NXFrame()
        frame.msgtype = 0xFF
        frame.data = {'_body': 'ZN017FttBaillb'}
        frame.checksum = 0
        frame.ackreq = False
        self.proto.write_frame(frame)
        self.assertEqual(b'ZN017FttBaillb', self.stream.written)

    def test_read_frame(self):
        """Verify read_frame parses a complete frame."""
        raw = b'\x0aZN017FttBaillb\x0d'
        self.stream = MockStream(raw)
        self.proto = NX587Protocol(self.stream)

        frame = self.proto.read_frame()
        self.assertEqual(0xFF, frame.msgtype)
        self.assertEqual(0, frame.checksum)
        self.assertFalse(frame.ackreq)
        self.assertEqual('ZN', frame.data['prefix'])
        self.assertEqual(17, frame.data['zone_number'])

    def test_read_frame_discards_prefix_bytes(self):
        """Verify read_frame discards bytes until LF."""
        raw = b'\x00\x01\x02\x0aZN017FttBaillb\x0d'
        self.stream = MockStream(raw)
        self.proto = NX587Protocol(self.stream)

        frame = self.proto.read_frame()
        self.assertEqual(0xFF, frame.msgtype)
        self.assertEqual(17, frame.data['zone_number'])

    def test_back_to_back_frames(self):
        """Verify correct handling of consecutive LF/CR-delimited frames."""
        raw1 = b'\x0aQ001\x0d'
        raw2 = b'\x0aPA1RasCeEps\x0d'
        raw = raw1 + raw2
        self.stream = MockStream(raw)
        self.proto = NX587Protocol(self.stream)

        frame1 = self.proto.read_frame()
        self.assertEqual('Q', frame1.data.get('prefix'))

        frame2 = self.proto.read_frame()
        self.assertEqual('PA', frame2.data['prefix'])
        self.assertEqual(1, frame2.data['partition_number'])

    # --- Zone Status Parsing ---

    def test_parse_zone_status_simple(self):
        """Parse ZN017FttBaillb."""
        result = self.proto.parse_zone_status('ZN017FttBaillb')
        self.assertEqual('ZN', result['prefix'])
        self.assertEqual(17, result['zone_number'])
        self.assertTrue(result['status']['Faulted'])
        self.assertFalse(result['status']['Tamper'])
        self.assertFalse(result['status']['Trouble'])
        self.assertTrue(result['status']['Bypass'])
        self.assertFalse(result['status']['Alarm memory'])
        self.assertFalse(result['status']['Inhibit'])
        self.assertFalse(result['status']['Low battery'])
        self.assertFalse(result['status']['Lost'])
        self.assertFalse(result['status']['Bypass memory'])
        self.assertNotIn('name', result)

    def test_parse_zone_status_with_name(self):
        """Parse ZN003LIVING ROOM PIR fTtbaillb."""
        result = self.proto.parse_zone_status('ZN003LIVING ROOM PIR fTtbaillb')
        self.assertEqual('ZN', result['prefix'])
        self.assertEqual(3, result['zone_number'])
        self.assertEqual('LIVING ROOM PIR', result['name'])
        self.assertFalse(result['status']['Faulted'])
        self.assertTrue(result['status']['Tamper'])
        self.assertFalse(result['status']['Trouble'])
        self.assertFalse(result['status']['Bypass'])

    def test_parse_zone_status_all_true(self):
        """Parse zone with all status fields uppercase (true)."""
        result = self.proto.parse_zone_status('ZN001FTTBAILLB')
        for field in NX587Protocol.ZONE_STATUS_FIELDS:
            self.assertTrue(result['status'][field],
                          'Field %s should be True' % field)

    def test_parse_zone_status_all_false(self):
        """Parse zone with all status fields lowercase (false)."""
        result = self.proto.parse_zone_status('ZN001fttbaillb')
        for field in NX587Protocol.ZONE_STATUS_FIELDS:
            self.assertFalse(result['status'][field],
                           'Field %s should be False' % field)

    def test_parse_zone_status_repeated_letters(self):
        """Verify positional parsing of T/T, L/L, B/B (not letter searching)."""
        # 9 status chars: F T T T B I L L B
        # pos1=Faulted=F, pos2=Tamper=T, pos3=Trouble=T, pos4=Bypass=T, pos6=Inhibit=T, pos7=Low battery=T, pos8=Lost=T, pos9=Bypass memory=T
        result = self.proto.parse_zone_status('ZN005FTTTBILLB')
        self.assertTrue(result['status']['Tamper'])
        self.assertTrue(result['status']['Trouble'])
        self.assertTrue(result['status']['Low battery'])
        self.assertTrue(result['status']['Lost'])
        self.assertTrue(result['status']['Bypass'])
        self.assertTrue(result['status']['Bypass memory'])

    def test_parse_zone_status_space_preservation(self):
        """Verify spaces in zone names are preserved."""
        # Name is 16 chars: 'TEST ZONE 123   ' (13 chars + 3 trailing spaces)
        # Status is 9 chars: 'fTtBaillb'
        # Total remaining: 25 chars
        result = self.proto.parse_zone_status('ZN001TEST ZONE 123   fTtBaillb')
        self.assertEqual('TEST ZONE 123', result['name'])

    def test_parse_zone_status_trailing_space_strip(self):
        """Verify trailing spaces are stripped from names."""
        # Name is 16 chars: 'ZONE NAME      ' (9 chars + 7 trailing spaces)
        # Status is 9 chars: 'fTtBaillb'
        # Total remaining: 25 chars
        result = self.proto.parse_zone_status('ZN001ZONE NAME       fTtBaillb')
        self.assertEqual('ZONE NAME', result['name'])

    def test_parse_zone_status_8_char_status_string(self):
        """Parse zone with 8-char status string (device omits Inhibit flag)."""
        # Device sends 8-char status with extra space from name field
        # Name: '2' + 10 spaces = 11 chars (instead of 16)
        # Status: 'fttbAllb' (8 chars, missing Inhibit at pos 6)
        # Full remaining: 11 + 8 = 19 chars (instead of 25)
        # Parser extracts ' fttbAllb' (9 chars with leading space)
        # After fix: 'fttbAllb' → 'fttbAl lb' (space inserted at Inhibit pos 6)
        result = self.proto.parse_zone_status('ZN002\x00\x00\x00\x00\x002          fttbAllb')
        self.assertEqual(2, result['zone_number'])
        self.assertEqual('2', result['name'])
        self.assertFalse(result['status']['Faulted'])
        self.assertFalse(result['status']['Tamper'])
        self.assertFalse(result['status']['Trouble'])
        self.assertFalse(result['status']['Bypass'])
        self.assertTrue(result['status']['Alarm memory'])
        self.assertFalse(result['status']['Inhibit'])
        self.assertFalse(result['status']['Low battery'])
        self.assertFalse(result['status']['Lost'])
        self.assertFalse(result['status']['Bypass memory'])

    def test_parse_zone_status_invalid_prefix(self):
        """Verify ValueError for invalid ZN prefix."""
        with self.assertRaises(ValueError):
            self.proto.parse_zone_status('XX001FTTBAILLB')

    def test_parse_zone_status_insufficient_data(self):
        """Verify ValueError for insufficient data."""
        with self.assertRaises(ValueError):
            self.proto.parse_zone_status('ZN001FT')

    def test_parse_zone_status_zone_001(self):
        """Parse zone 001."""
        result = self.proto.parse_zone_status('ZN001FTTBAILLB')
        self.assertEqual(1, result['zone_number'])

    def test_parse_zone_status_zone_192(self):
        """Parse zone 192."""
        result = self.proto.parse_zone_status('ZN192fttbaillb')
        self.assertEqual(192, result['zone_number'])

    # --- Partition Status Parsing ---

    def test_parse_partition_status_simple(self):
        """Parse PA1RasCeEps."""
        result = self.proto.parse_partition_status('PA1RasCeEps')
        self.assertEqual('PA', result['prefix'])
        self.assertEqual(1, result['partition_number'])
        self.assertTrue(result['status']['Ready to arm'])
        self.assertFalse(result['status']['Armed'])
        self.assertFalse(result['status']['Entryguard (stay mode)'])
        self.assertTrue(result['status']['Chime mode on'])
        self.assertFalse(result['status']['Entry'])
        self.assertTrue(result['status']['Exit 1'])
        self.assertFalse(result['status']['Previous alarm'])
        self.assertFalse(result['status']['Siren on'])

    def test_parse_partition_status_all_true(self):
        """Parse partition with all fields uppercase (true)."""
        # R(ready) A(armed) S(stay) C(chime) E(entry_delay)
        # E(exit_period) P(prev_alarm) S(siren_on)
        # 8 status chars: RASCeEPPS... no: R+A+S+C+E+E+P+S = RASCeEPPS (9 chars, too many)
        # Correct: 'RASCEEPS' = 8 chars
        result = self.proto.parse_partition_status('PA1RASCEEPS')
        for field in NX587Protocol.PARTITION_STATUS_FIELDS:
            self.assertTrue(result['status'][field],
                          'Field %s should be True' % field)

    def test_parse_partition_status_all_false(self):
        """Parse partition with all fields lowercase (false)."""
        result = self.proto.parse_partition_status('PA1rasc eeps')
        for field in NX587Protocol.PARTITION_STATUS_FIELDS:
            self.assertFalse(result['status'][field],
                           'Field %s should be False' % field)

    def test_parse_partition_status_repeated_letters(self):
        """Verify positional parsing of E/E, S/S."""
        # Position 2=Entryguard (stay mode)=S, position 4=Entry=E
        # position 5=Exit 1=E, position 7=Siren on=S
        result = self.proto.parse_partition_status('PA1RASEEPPS')
        self.assertTrue(result['status']['Entryguard (stay mode)'])
        self.assertTrue(result['status']['Entry'])
        self.assertTrue(result['status']['Exit 1'])
        self.assertTrue(result['status']['Siren on'])

    def test_parse_partition_status_all_partitions(self):
        """Parse PA1 through PA8."""
        for i in range(1, 9):
            body = 'PA%dRasCeEps' % i
            result = self.proto.parse_partition_status(body)
            self.assertEqual(i, result['partition_number'])

    def test_parse_partition_status_invalid_prefix(self):
        """Verify ValueError for invalid PA prefix."""
        with self.assertRaises(ValueError):
            self.proto.parse_partition_status('XX1RasCeEps')

    def test_parse_partition_status_insufficient_data(self):
        """Verify ValueError for insufficient status data."""
        with self.assertRaises(ValueError):
            self.proto.parse_partition_status('PA1Ras')

    # --- Query Encoding ---

    def test_query_zone_001(self):
        """Encode Q001."""
        encoded = self.proto.encode_frame('Q001')
        self.assertEqual(b'\x0aQ001\x0d', encoded)

    def test_query_zone_192(self):
        """Encode Q192."""
        encoded = self.proto.encode_frame('Q192')
        self.assertEqual(b'\x0aQ192\x0d', encoded)

    def test_query_partition_1(self):
        """Encode Q193."""
        encoded = self.proto.encode_frame('Q193')
        self.assertEqual(b'\x0aQ193\x0d', encoded)

    def test_query_partition_8(self):
        """Encode Q200."""
        encoded = self.proto.encode_frame('Q200')
        self.assertEqual(b'\x0aQ200\x0d', encoded)

    def test_query_display(self):
        """Encode Q000."""
        encoded = self.proto.encode_frame('Q000')
        self.assertEqual(b'\x0aQ000\x0d', encoded)

    def test_query_leds_buzzer(self):
        """Encode Q201."""
        encoded = self.proto.encode_frame('Q201')
        self.assertEqual(b'\x0aQ201\x0d', encoded)

    def test_get_startup_queries(self):
        """Verify get_startup_queries returns correct list."""
        queries = self.proto.get_startup_queries(3, 2)
        expected = ['Q001', 'Q002', 'Q003', 'Q193', 'Q194']
        self.assertEqual(expected, queries)

    def test_get_startup_queries_zero_zones(self):
        """Verify get_startup_queries with zero zones."""
        queries = self.proto.get_startup_queries(0, 2)
        expected = ['Q193', 'Q194']
        self.assertEqual(expected, queries)

    def test_get_startup_queries_zero_partitions(self):
        """Verify get_startup_queries with zero partitions."""
        queries = self.proto.get_startup_queries(3, 0)
        expected = ['Q001', 'Q002', 'Q003']
        self.assertEqual(expected, queries)

    # --- Feature Toggles ---

    def test_toggle_enable(self):
        """Verify uppercase characters (e.g., 'Z')."""
        self.assertIn('Z', NX587Protocol.ENABLED_TOGGLES)
        self.assertIn('Z', NX587Protocol.TOGGLE_ZONE_STATUS)

    def test_toggle_disable(self):
        """Verify lowercase characters can disable (e.g., 'z')."""
        self.assertEqual('Z', NX587Protocol.TOGGLE_ZONE_STATUS)
        self.assertEqual('P', NX587Protocol.TOGGLE_PARTITION_STATUS)

    def test_all_toggles(self):
        """Verify all 7 toggles are defined."""
        expected = ['Z', 'P', 'N', 'T', 'A', 'L', 'I']
        for toggle in expected:
            self.assertIn(toggle, NX587Protocol.ENABLED_TOGGLES.upper())

    def test_startup_toggles(self):
        """Verify get_feature_toggles() returns 'taLIPZN'."""
        result = self.proto.get_feature_toggles()
        self.assertEqual('taLIPZN', result)

    # --- Character Substitutions ---

    def test_substitution_0x00_to_aacute(self):
        """0x00 -> aacute."""
        self.assertEqual('\u00e1', NX587Protocol.SUBSTITUTIONS[0x00])

    def test_substitution_0x01_to_eacute(self):
        """0x01 -> eacute."""
        self.assertEqual('\u00e9', NX587Protocol.SUBSTITUTIONS[0x01])

    def test_substitution_0x02_to_iacute(self):
        """0x02 -> iacute."""
        self.assertEqual('\u00ed', NX587Protocol.SUBSTITUTIONS[0x02])

    def test_substitution_0x03_to_oacute(self):
        """0x03 -> oacute."""
        self.assertEqual('\u00f3', NX587Protocol.SUBSTITUTIONS[0x03])

    def test_substitution_0x04_to_uacute(self):
        """0x04 -> uacute."""
        self.assertEqual('\u00fa', NX587Protocol.SUBSTITUTIONS[0x04])

    def test_substitution_0x05_to_ntilde(self):
        """0x05 -> ntilde."""
        self.assertEqual('\u00f1', NX587Protocol.SUBSTITUTIONS[0x05])

    def test_substitution_0x06_to_ntilde_upper(self):
        """0x06 -> Ntilde."""
        self.assertEqual('\u00d1', NX587Protocol.SUBSTITUTIONS[0x06])

    def test_substitution_0x1f_to_oelig(self):
        """0x1F -> oe ligature."""
        self.assertEqual('\x9c', NX587Protocol.SUBSTITUTIONS[0x1F])

    def test_substitution_0x5c_to_zero(self):
        """0x5C -> zero."""
        self.assertEqual('0', NX587Protocol.SUBSTITUTIONS[0x5C])

    def test_substitution_0x7e_to_thorn(self):
        """0x7E -> thorn."""
        self.assertEqual('\xfe', NX587Protocol.SUBSTITUTIONS[0x7E])

    def test_substitution_0x7f_to_sup3(self):
        """0x7F -> sup3."""
        self.assertEqual('\xb3', NX587Protocol.SUBSTITUTIONS[0x7F])

    def test_all_substitutions_decode_encode_roundtrip(self):
        """Verify all substitution characters encode in latin-1."""
        for byte_val, char in NX587Protocol.SUBSTITUTIONS.items():
            # All substitution chars must be encodable in latin-1
            encoded = char.encode('latin-1')
            self.assertEqual(len(encoded), 1,
                           'Char %r for byte %02x should be single byte in latin-1' % (char, byte_val))

    # --- Message Parsing ---

    def test_parse_message_zn(self):
        """Verify parse_message dispatches ZN to parse_zone_status."""
        result = self.proto.parse_message('ZN017FttBaillb')
        self.assertEqual('ZN', result['prefix'])
        self.assertEqual(17, result['zone_number'])

    def test_parse_message_pa(self):
        """Verify parse_message dispatches PA to parse_partition_status."""
        result = self.proto.parse_message('PA1RasCeEps')
        self.assertEqual('PA', result['prefix'])
        self.assertEqual(1, result['partition_number'])

    def test_parse_message_q(self):
        """Verify parse_message dispatches Q to parse_query_response."""
        result = self.proto.parse_message('Q001')
        self.assertEqual('Q', result['prefix'])
        self.assertTrue(result.get('_query', False))

    def test_parse_message_dl1(self):
        """Verify parse_message handles DL1."""
        result = self.proto.parse_message('DL1HOME')
        self.assertEqual('DL1', result['prefix'])
        self.assertEqual('HOME', result['text'])

    def test_parse_message_dl2(self):
        """Verify parse_message handles DL2."""
        result = self.proto.parse_message('DL2ARMED')
        self.assertEqual('DL2', result['prefix'])
        self.assertEqual('ARMED', result['text'])

    def test_parse_message_lds(self):
        """Verify parse_message handles LDS."""
        result = self.proto.parse_message('LDS1')
        self.assertEqual('LDS', result['prefix'])

    def test_parse_message_ldf(self):
        """Verify parse_message handles LDF."""
        result = self.proto.parse_message('LDF1')
        self.assertEqual('LDF', result['prefix'])

    def test_parse_message_l(self):
        """Verify parse_message handles L (individual LED)."""
        result = self.proto.parse_message('L011')
        self.assertEqual('L', result['prefix'])
        self.assertEqual('01', result['id'])

    def test_parse_message_bz(self):
        """Verify parse_message handles BZ."""
        result = self.proto.parse_message('BZ1')
        self.assertEqual('BZ', result['prefix'])

    def test_parse_message_unknown(self):
        """Verify parse_message handles unknown prefixes."""
        result = self.proto.parse_message('XYZ')
        self.assertEqual('UNKNOWN', result['prefix'])
        self.assertEqual('XYZ', result['raw'])

    # --- Command Sequence Generation ---

    def test_arm_stay_no_partition(self):
        """Verify 'S' sequence for arm_stay without partition."""
        mock_send = mock.MagicMock()
        self.proto.send_keypad_sequence = mock_send
        self.proto.execute_command('arm_stay')
        mock_send.assert_called_once_with('S', delays=None)

    def test_arm_stay_with_partition(self):
        """Verify '*13$1234$S' sequence for arm_stay with partition."""
        mock_send = mock.MagicMock()
        self.proto.send_keypad_sequence = mock_send
        self.proto.execute_command('arm_stay', partition=3, pin='1234')
        mock_send.assert_called_once_with('*13$1234$S', delays=None)

    def test_arm_stay_partition_1_no_pin(self):
        """Verify 'S' sequence for arm_stay partition 1 without PIN (quick arm)."""
        mock_send = mock.MagicMock()
        self.proto.send_keypad_sequence = mock_send
        self.proto.execute_command('arm_stay', partition=1)
        mock_send.assert_called_once_with('S', delays=None)

    def test_arm_stay_missing_pin(self):
        """Verify ValueError when PIN required but not provided."""
        with self.assertRaises(ValueError):
            self.proto.execute_command('arm_stay', partition=3, pin=None)

    def test_arm_exit_no_partition(self):
        """Verify 'E' sequence for arm_exit without partition."""
        mock_send = mock.MagicMock()
        self.proto.send_keypad_sequence = mock_send
        self.proto.execute_command('arm_exit')
        mock_send.assert_called_once_with('E', delays=None)

    def test_arm_exit_with_partition(self):
        """Verify '*13$1234$E' sequence for arm_exit with partition."""
        mock_send = mock.MagicMock()
        self.proto.send_keypad_sequence = mock_send
        self.proto.execute_command('arm_exit', partition=3, pin='1234')
        mock_send.assert_called_once_with('*13$1234$E', delays=None)

    def test_disarm_no_partition(self):
        """Verify '$1234$' sequence for disarm without partition."""
        mock_send = mock.MagicMock()
        self.proto.send_keypad_sequence = mock_send
        self.proto.execute_command('disarm', pin='1234')
        mock_send.assert_called_once_with('$1234$', delays=None)

    def test_disarm_with_partition(self):
        """Verify '*13$1234$' sequence for disarm with partition."""
        mock_send = mock.MagicMock()
        self.proto.send_keypad_sequence = mock_send
        self.proto.execute_command('disarm', pin='1234', partition=3)
        mock_send.assert_called_once_with('*13$1234$', delays=None)

    def test_bypass_no_partition(self):
        """Verify 'B7B#' sequence for bypass without partition."""
        mock_send = mock.MagicMock()
        self.proto.send_keypad_sequence = mock_send
        self.proto.execute_command('zone_bypass_toggle', zone=7)
        mock_send.assert_called_once_with('B7B#', delays={2: 0.5})

    def test_bypass_with_partition(self):
        """Verify '*13$1234$B7B#' sequence for bypass with partition."""
        mock_send = mock.MagicMock()
        self.proto.send_keypad_sequence = mock_send
        self.proto.execute_command('zone_bypass_toggle', zone=7, partition=3, pin='1234')
        mock_send.assert_called_once_with('*13$1234$B7B#', delays={11: 0.5})

    def test_bypass_missing_zone(self):
        """Verify ValueError when zone not provided for bypass."""
        with self.assertRaises(ValueError):
            self.proto.execute_command('zone_bypass_toggle')

    def test_bypass_missing_pin(self):
        """Verify ValueError when PIN required but not provided for bypass."""
        with self.assertRaises(ValueError):
            self.proto.execute_command('zone_bypass_toggle', zone=7, partition=3)

    def test_police_no_partition(self):
        """Verify 'H' sequence for police without partition."""
        mock_send = mock.MagicMock()
        self.proto.send_keypad_sequence = mock_send
        self.proto.execute_command('police')
        mock_send.assert_called_once_with('H', delays=None)

    def test_police_with_partition(self):
        """Verify '*13$1234$H' sequence for police with partition."""
        mock_send = mock.MagicMock()
        self.proto.send_keypad_sequence = mock_send
        self.proto.execute_command('police', partition=3, pin='1234')
        mock_send.assert_called_once_with('*13$1234$H', delays=None)

    def test_police_missing_pin(self):
        """Verify ValueError when PIN required but not provided for police."""
        with self.assertRaises(ValueError):
            self.proto.execute_command('police', partition=3)

    def test_fire_no_partition(self):
        """Verify 'F' sequence for fire without partition."""
        mock_send = mock.MagicMock()
        self.proto.send_keypad_sequence = mock_send
        self.proto.execute_command('fire')
        mock_send.assert_called_once_with('F', delays=None)

    def test_fire_with_partition(self):
        """Verify '*13$1234$F' sequence for fire with partition."""
        mock_send = mock.MagicMock()
        self.proto.send_keypad_sequence = mock_send
        self.proto.execute_command('fire', partition=3, pin='1234')
        mock_send.assert_called_once_with('*13$1234$F', delays=None)

    def test_fire_missing_pin(self):
        """Verify ValueError when PIN required but not provided for fire."""
        with self.assertRaises(ValueError):
            self.proto.execute_command('fire', partition=3)

    def test_execute_command_unknown(self):
        """Verify ValueError for unknown command."""
        with self.assertRaises(ValueError):
            self.proto.execute_command('unknown_cmd')

    def test_disarm_no_pin(self):
        """Verify disarm without pin sends empty string."""
        mock_send = mock.MagicMock()
        self.proto.send_keypad_sequence = mock_send
        self.proto.execute_command('disarm')
        mock_send.assert_called_once_with('', delays=None)

    # --- send_keypad_sequence ---

    def test_send_keypad_sequence_single_char(self):
        """Verify single character is sent as individual frame."""
        written = []
        self.proto.write_frame = lambda frame: written.append(frame)
        self.proto.send_keypad_sequence('S')
        self.assertEqual(1, len(written))
        self.assertEqual('S', written[0].data)

    def test_send_keypad_sequence_multiple_chars(self):
        """Verify multiple characters are sent individually."""
        written = []
        self.proto.write_frame = lambda frame: written.append(frame)
        self.proto.send_keypad_sequence('ABC')
        self.assertEqual(3, len(written))
        self.assertEqual('A', written[0].data)
        self.assertEqual('B', written[1].data)
        self.assertEqual('C', written[2].data)

    def test_send_keypad_sequence_with_delay(self):
        """Verify delays are respected between characters."""
        delays_called = []
        original_sleep = None

        def mock_sleep(seconds):
            delays_called.append(seconds)

        import time
        with mock.patch.object(time, 'sleep', mock_sleep):
            written = []
            self.proto.write_frame = lambda frame: written.append(frame)
            self.proto.send_keypad_sequence('ABC', delays={1: 2.0})
            self.assertEqual(3, len(written))
            self.assertEqual(1, len(delays_called))
            self.assertEqual(2.0, delays_called[0])

    # --- NXFrame msgtype ---

    def test_msgtype_constant(self):
        """Verify MSGTYPE_NX587 sentinel value."""
        self.assertEqual(0xFF, NX587Protocol.MSGTYPE_NX587)

    def test_enabled_toggles_constant(self):
        """Verify ENABLED_TOGGLES constant."""
        self.assertEqual('taLIPZN', NX587Protocol.ENABLED_TOGGLES)

    # --- Query Response Parsing ---

    def test_parse_query_response_zone(self):
        """Verify parse_query_response dispatches ZN responses."""
        result = self.proto.parse_query_response('ZN017FttBaillb')
        self.assertEqual('ZN', result['prefix'])
        self.assertEqual(17, result['zone_number'])

    def test_parse_query_response_partition(self):
        """Verify parse_query_response dispatches PA responses."""
        result = self.proto.parse_query_response('PA1RasCeEps')
        self.assertEqual('PA', result['prefix'])
        self.assertEqual(1, result['partition_number'])

    def test_parse_query_response_unknown(self):
        """Verify parse_query_response delegates to parse_message for unknown."""
        result = self.proto.parse_query_response('DL1HOME')
        self.assertEqual('DL1', result['prefix'])


if __name__ == '__main__':
    unittest.main()
