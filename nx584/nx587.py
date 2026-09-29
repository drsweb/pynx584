"""NX587E Virtual LCD Keypad protocol handler.

Implements LF/CR-delimited ASCII framing for the NetworX NX587E protocol.
"""

import logging
import re
import time

from nx584.nxcore import NXFrame
from nx584.nxcore import NXProtocol

LOG = logging.getLogger('nx587')


class NX587Protocol(NXProtocol):
    """NX587E protocol handler -- LF/CR delimited ASCII framing.

    Frame format: LF (0x0A) + BODY + CR (0x0D)
    - Body is printable ASCII
    - No byte stuffing
    - No checksum
    - No length prefix
    """

    # Protocol capabilities - NX587E does not support these features
    UNSUPPORTED_FEATURES = {'system_status', 'log_events', 'user_codes', 'set_time'}
    UNSUPPORTED_ENDPOINTS = {'users', 'set_time'}
    BYPASS_NEEDS_PARTITION = True

    # Sentinel msgtype for NX587E frames
    MSGTYPE_NX587 = 0xFF

    # Message prefix constants
    PREFIX_DL1 = 'DL1'
    PREFIX_DL2 = 'DL2'
    PREFIX_LDS = 'LDS'
    PREFIX_LDF = 'LDF'
    PREFIX_L = 'L'
    PREFIX_BZ = 'BZ'
    PREFIX_PA = 'PA'
    PREFIX_ZN = 'ZN'
    PREFIX_Q = 'Q'

    # Feature toggle characters
    TOGGLE_LCD_TEXT = 'T'
    TOGGLE_LCD_ATTRIBUTES = 'A'
    TOGGLE_LED_BUZZER = 'L'
    TOGGLE_INDIVIDUAL_LED = 'I'
    TOGGLE_PARTITION_STATUS = 'P'
    TOGGLE_ZONE_STATUS = 'Z'
    TOGGLE_ZONE_NAME = 'N'

    # Feature toggles sent during startup
    # Uppercase enables, lowercase disables
    ENABLED_TOGGLES = 'taLIPZN'

    # Character substitutions for NX587E protocol
    # Maps byte values to display characters
    SUBSTITUTIONS = {
        0x00: '\u00e1',   # á
        0x01: '\u00e9',   # eacute
        0x02: '\u00ed',   # iacute
        0x03: '\u00f3',   # oacute
        0x04: '\u00fa',   # uacute
        0x05: '\u00f1',   # ntilde
        0x06: '\u00d1',   # Ntilde
        0x1F: '\x9c',     # oe ligature (latin-1)
        0x5C: '0',        # zero (instead of backslash)
        0x7E: '\xfe',     # thorn
        0x7F: '\xb3',     # sup3
    }

    # Reverse substitution for encoding
    REVERSE_SUBSTITUTIONS = {v: k for k, v in SUBSTITUTIONS.items()}

    # Zone status field names by position
    ZONE_STATUS_FIELDS = [
        'Faulted',
        'Tamper',
        'Trouble',
        'Bypass',
        'Alarm memory',
        'Inhibit',
        'Low battery',
        'Lost',
        'Bypass memory',
    ]

    # Partition status field names by position
    PARTITION_STATUS_FIELDS = [
        'Ready to arm',
        'Armed',
        'Entryguard (stay mode)',
        'Chime mode on',
        'Entry',
        'Exit 1',
        'Previous alarm',
        'Siren on',
    ]

    def __init__(self, stream, config=None):
        """Initialize the NX587E protocol handler.

        Args:
            stream: The underlying stream wrapper (SerialWrapper or SocketWrapper)
            config: ConfigParser instance for reading config values (optional)
        """
        super(NX587Protocol, self).__init__(stream)
        self._config = config

    def _encode_body(self, body):
        """Encode body to bytes without framing.

        Args:
            body: The ASCII body string

        Returns:
            bytes: Encoded body bytes
        """
        return body.encode('latin-1')

    def encode_frame(self, body):
        """Encode an ASCII body string into a complete LF/CR-delimited frame.

        Args:
            body: The ASCII body string (e.g., "ZN017FttBaillb")

        Returns:
            bytes: LF + body + CR
        """
        return b'\x0a' + self._encode_body(body) + b'\x0d'

    def decode_frame(self, raw):
        """Decode a raw LF/CR-delimited frame to an ASCII body string.

        Args:
            raw: Raw bytes including LF and CR delimiters

        Returns:
            str: The body string (without LF/CR)

        Raises:
            ValueError: If frame doesn't have valid LF/CR delimiters
        """
        if not raw or raw[0:1] != b'\x0a':
            raise ValueError('Frame must start with LF (0x0A)')
        if raw[-1:] != b'\x0d':
            raise ValueError('Frame must end with CR (0x0D)')
        return raw[1:-1].decode('latin-1')

    def read_frame(self):
        """Read a single LF/CR-delimited frame from the stream.

        Process:
        1. Discard bytes until LF (0x0A) is found
        2. Read bytes until CR (0x0D) is found
        3. Extract body (bytes between LF and CR)
        4. Decode body to ASCII string
        5. Parse the body to determine message type and extract data
        6. Return NXFrame with msgtype=0xFF and parsed data

        Returns:
            NXFrame with:
            - msgtype = 0xFF
            - data = dict with parsed message data
            - checksum = 0
            - ackreq = False

        Raises:
            ConnectionLost: If connection is lost while reading
        """
        # Discard bytes until LF
        while True:
            byte = self.read(1)
            if byte == b'\x0a':
                break

        # Read body until CR (LF already consumed)
        body_bytes = b''
        while True:
            b = self.read(1)
            if b == b'\x0d':
                break
            body_bytes += b

        body = body_bytes.decode('latin-1')
        LOG.debug('Received: %r', body)
        parsed = self.parse_message(body)
        frame = NXFrame()
        frame.msgtype = self.MSGTYPE_NX587
        frame.data = parsed
        frame.checksum = 0
        frame.ackreq = False
        return frame

    def write_frame(self, frame):
        """Write an ASCII body string to the stream.

        NX587E sends raw ASCII without LF/CR wrapping.

        Args:
            frame: NXFrame with data containing the ASCII body string,
                   or raw bytes/list for write_frame_raw()
        """
        if isinstance(frame, list):
            body = bytes(frame).decode('latin-1')
        elif isinstance(frame.data, dict):
            body = frame.data.get('_body', '')
        else:
            body = str(frame.data)
        self.write(self._encode_body(body))

    def parse_message(self, body):
        """Parse an NX587E message body into a structured dict.

        Dispatches to specific parsers based on message prefix:
        - DL1, DL2 -> display message
        - LDS -> status LED message
        - LDF -> function LED message
        - L -> individual LED message
        - BZ -> buzzer message
        - PA -> partition status message
        - ZN -> zone status message
        - Q -> query response message

        Args:
            body: The ASCII message body string

        Returns:
            dict: Parsed message data with keys:
            - 'prefix': str (message type prefix)
            - Additional keys depending on message type
        """
        if body.startswith(self.PREFIX_ZN):
            return self.parse_zone_status(body)
        elif body.startswith(self.PREFIX_PA):
            return self.parse_partition_status(body)
        elif body.startswith(self.PREFIX_Q):
            return self.parse_query_response(body)
        elif body.startswith((self.PREFIX_DL1, self.PREFIX_DL2)):
            return {'prefix': body[:3], 'text': body[3:]}
        elif body.startswith(self.PREFIX_LDS):
            return {'prefix': self.PREFIX_LDS, 'state': body[3:]}
        elif body.startswith(self.PREFIX_LDF):
            return {'prefix': self.PREFIX_LDF, 'state': body[3:]}
        elif body.startswith(self.PREFIX_L):
            return {'prefix': self.PREFIX_L, 'id': body[1:3], 'state': body[3:]}
        elif body.startswith(self.PREFIX_BZ):
            return {'prefix': self.PREFIX_BZ, 'state': body[2:]}
        else:
            return self._parse_non_zn_pa(body)


    def parse_zone_status(self, body):
        """Parse a ZN (zone status) message body.

        Format: ZN{3-digit-zone}{16-char-name}{9-status-chars}  (with name)
                ZN{3-digit-zone}{9-status-chars}                 (without name)

        The 9 status positions are (by fixed position, NOT by searching for letters):
          1: Fault (F)
          2: Tamper (T)
          3: Trouble (T)
          4: Bypass (B)
          5: Alarm Memory (A)
          6: Inhibit (I)
          7: Low Battery (L)
          8: Lost (L)
          9: Bypass Memory (B)

        Uppercase = TRUE, lowercase = FALSE.

        Args:
            body: The ZN message body (e.g., "ZN017FttBaillb")

        Returns:
            dict: {
                'prefix': 'ZN',
                'zone_number': int (1-based),
                'status': {
                    'fault': bool,
                    'tamper': bool,
                    'trouble': bool,
                    'bypass': bool,
                    'alarm_memory': bool,
                    'inhibit': bool,
                    'low_battery': bool,
                    'lost': bool,
                    'bypass_memory': bool
                },
                'name': str (optional, 16-char fixed-width, trailing spaces stripped)
            }
        """
        # Verify prefix
        if not body.startswith(self.PREFIX_ZN):
            raise ValueError('Invalid ZN message: missing ZN prefix')

        # Parse zone number (bytes 2-4, 3 decimal digits)
        zone_number = int(body[2:5])

        remaining = body[5:]

        # Determine if name is present:
        # With name: N chars name + 9 chars status
        # Without name: 9 chars status only
        if len(remaining) == 9:
            name = None
            status_str = remaining[0:9]
        elif len(remaining) > 9:
            # Name is everything except the last 9 chars (status)
            # Device may send 8-char status (omits Inhibit flag) with extra space
            name_len = len(remaining) - 9
            name = remaining[0:name_len].replace('\x00', ' ').strip(' ')
            status_str = remaining[name_len:name_len + 9]
            # Handle 8-char status strings (device omits Inhibit flag at pos 5)
            # The extra space from the name field appears as the first char of status_str
            if len(status_str) >= 2 and status_str[0] == ' ' and status_str[1].islower():
                status_str = status_str[1:]  # Strip leading space from name field
                status_str = status_str[:6] + ' ' + status_str[6:]  # Insert space at Inhibit position (pos 6 in 8-char string)
        else:
            raise ValueError('Invalid ZN message: expected 9 or more remaining chars, got %d' % len(remaining))

        # Parse status fields by position (NOT by searching for letters)
        status = {}
        for i, field_name in enumerate(self.ZONE_STATUS_FIELDS):
            char = status_str[i]
            status[field_name] = char.isupper()

        result = {
            'prefix': self.PREFIX_ZN,
            'zone_number': zone_number,
            'status': status,
        }
        if name is not None:
            result['name'] = name

        return result

    def parse_partition_status(self, body):
        """Parse a PA (partition status) message body.

        Format: PA{partition}{8-status-chars}

        The 8 status positions are (by fixed position):
          1: Ready (R)
          2: Armed (A)
          3: Stay (S)
          4: Chime Mode (C)
          5: Entry Delay (E)
          6: Exit Period (E)
          7: Previous Alarm (P)
          8: Siren On (S)

        Uppercase = TRUE, lowercase = FALSE.

        Args:
            body: The PA message body (e.g., "PA1RasCeEps")

        Returns:
            dict: {
                'prefix': 'PA',
                'partition_number': int (1-based),
                'status': {
                    'ready': bool,
                    'armed': bool,
                    'stay': bool,
                    'chime': bool,
                    'entry_delay': bool,
                    'exit_period': bool,
                    'previous_alarm': bool,
                    'siren_on': bool
                }
            }
        """
        # Verify prefix
        if not body.startswith(self.PREFIX_PA):
            raise ValueError('Invalid PA message: missing PA prefix')

        # Parse partition number (byte 2, 1 decimal digit, 1-8)
        partition_number = int(body[2:3])

        # Parse 8 status chars by position
        status_str = body[3:11]
        if len(status_str) < 8:
            raise ValueError('Invalid PA message: insufficient status data')

        status = {}
        for i, field_name in enumerate(self.PARTITION_STATUS_FIELDS):
            char = status_str[i]
            status[field_name] = char.isupper()

        return {
            'prefix': self.PREFIX_PA,
            'partition_number': partition_number,
            'status': status,
        }

    def parse_query_response(self, body):
        """Parse a Q (query response) message body.

        Query responses contain the current state for the queried item.
        The response format depends on what was queried:
        - Q000 -> DL1/DL2 (display text)
        - Q001-Q192 -> ZN (zone status, possibly with name)
        - Q193-Q200 -> PA (partition status)
        - Q201 -> LDS/LDF/L/BZ (LEDs and buzzer)

        Args:
            body: The query response body

        Returns:
            dict: Parsed response data (delegates to appropriate parser)
        """
        # Query responses are actual ZN or PA messages
        if body.startswith(self.PREFIX_ZN):
            result = self.parse_zone_status(body)
        elif body.startswith(self.PREFIX_PA):
            result = self.parse_partition_status(body)
        else:
            # For other query responses (DL1, DL2, LEDs, buzzer)
            # Parse directly without going through parse_message to avoid recursion
            result = self._parse_non_zn_pa(body)
        result['_query'] = True
        return result

    def _parse_non_zn_pa(self, body):
        """Parse non-ZN/PA messages (DL1, DL2, LDS, LDF, L, BZ, Q, unknown)."""
        if body.startswith(self.PREFIX_Q):
            return {'prefix': self.PREFIX_Q, 'query': body[1:]}
        elif body.startswith((self.PREFIX_DL1, self.PREFIX_DL2)):
            return {'prefix': body[:3], 'text': body[3:]}
        elif body.startswith(self.PREFIX_LDS):
            return {'prefix': self.PREFIX_LDS, 'state': body[3:]}
        elif body.startswith(self.PREFIX_LDF):
            return {'prefix': self.PREFIX_LDF, 'state': body[3:]}
        elif body.startswith(self.PREFIX_L):
            return {'prefix': self.PREFIX_L, 'id': body[1:3], 'state': body[3:]}
        elif body.startswith(self.PREFIX_BZ):
            return {'prefix': self.PREFIX_BZ, 'state': body[2:]}
        else:
            return {'prefix': 'UNKNOWN', 'raw': body}

    def get_feature_toggles(self):
        """Get the feature toggle string to send during startup.

        Returns:
            str: All enabled toggles as a single string (e.g., "ZPNTALI")
        """
        return self.ENABLED_TOGGLES

    def get_startup_queries(self, max_zone, max_partitions):
        """Get the list of startup query strings to send.

        Args:
            max_zone: Maximum zone number to query (from config)
            max_partitions: Maximum partition number to query (from config)

        Returns:
            list: List of query strings in order:
            - Q001, Q002, ..., Q{max_zone:03d}
            - Q193, Q194, ..., Q{192+max_partitions}
        """
        queries = []
        for zone in range(1, max_zone + 1):
            queries.append('Q%03d' % zone)
        for part in range(1, max_partitions + 1):
            queries.append('Q%d' % (192 + part))
        return queries

    def execute_command(self, cmd, **kwargs):
        """Execute an NX584 command using NX587E keypad sequences.

        This is the protocol-specific implementation of NX584 commands.
        The controller delegates to this method when using NX587E.

        Command sequences:
        - arm_stay (no partition): 'S'
        - arm_stay (partition=N): '*1{partition}{pin}S' (with 2s delay before 'S')
        - arm_exit (no partition): 'E'
        - arm_exit (partition=N): '*1{partition}{pin}E' (with 2s delay before 'E')
        - disarm (no partition): '{pin}'
        - disarm (partition=N): '*1{partition}{pin}'
        - zone_bypass_toggle (no partition): 'B{zone}B'
        - zone_bypass_toggle (partition=N): '*1{partition}{pin}B{zone}B'
        - police (no partition): 'H'
        - police (partition=N): '*1{partition}{pin}H'
        - fire (no partition): 'F'
        - fire (partition=N): '*1{partition}{pin}F'

        Zone numbers in bypass sequences are plain integers (no leading zeros).

        Args:
            cmd: Command name ('arm_stay', 'arm_exit', 'disarm', 'zone_bypass_toggle',
                          'police', 'fire')
            **kwargs: Command parameters:
                - partition: int (optional, for arm/disarm/bypass/police/fire)
                - pin: str (optional, for partition arm/disarm/bypass/police/fire)
                - zone: int (for bypass)

        Raises:
            ValueError: If required parameters are missing (e.g., PIN for partition arm)
        """
        if self._config:
            command_delay = self._config.getfloat('config', 'command_delay_seconds', fallback=0.5)
        else:
            command_delay = 0.5
        partition = kwargs.get('partition')
        pin = kwargs.get('pin')
        zone = kwargs.get('zone')
        delays = None

        if cmd == 'arm_stay':
            if partition and partition != 1 or pin:
                if not pin:
                    raise ValueError("PIN required for partition arm")
                sequence = '*1%s$%s$S' % (partition, pin)
            else:
                sequence = 'S'

        elif cmd == 'arm_exit':
            if partition and partition != 1 or pin:
                if not pin:
                    raise ValueError("PIN required for partition exit")
                sequence = '*1%s$%s$E' % (partition, pin)
            else:
                sequence = 'E'

        elif cmd == 'disarm':
            if partition and partition != 1 or kwargs.get('master'):
                if not pin:
                    raise ValueError("PIN required for disarm")
                sequence = '*1%s$%s$' % (partition, pin)
            else:
                armed = kwargs.get('armed', True)
                if not armed:
                    LOG.info("Partition is neither armed nor in alarm, skipping disarm")
                    return
                sequence = '$%s$' % pin if pin else ''

        elif cmd == 'zone_bypass_toggle':
            if not zone:
                raise ValueError("zone parameter required for bypass")
            if partition and partition != 1 or pin:
                if not pin:
                    raise ValueError("PIN required for partition bypass")
                sequence = '*1%s$%s$B%sB#' % (partition, pin, zone)
            else:
                sequence = 'B%sB#' % zone
            delays = {len(sequence) - 2: command_delay}

        elif cmd == 'police':
            if partition and partition != 1 or pin:
                if not pin:
                    raise ValueError("PIN required for partition police")
                sequence = '*1%s$%s$H' % (partition, pin)
            else:
                sequence = 'H'

        elif cmd == 'fire':
            if partition and partition != 1 or pin:
                if not pin:
                    raise ValueError("PIN required for partition fire")
                sequence = '*1%s$%s$F' % (partition, pin)
            else:
                sequence = 'F'
        else:
            raise ValueError("Unknown command: %s" % cmd)

        self.send_keypad_sequence(sequence, delays=delays)

    def send_keypad_sequence(self, sequence, delays=None):
        """Send a keypad character sequence to the NX587E device.

        Each character in the sequence is sent as an individual frame.
        Optional delays can be specified between characters.
        PINs in the sequence are wrapped in $ delimiters for redaction.

        Args:
            sequence: String of keypad characters to send (PINs wrapped in $)
            delays: List of (index, delay_seconds) tuples for specific delays
                   e.g., [(5, 2.0)] means 2-second delay after character at index 5
        """
        if delays is None:
            delays = {}

        redacted = re.sub(r'\$\d+\$', '[REDACTED]', sequence)
        redacted = redacted.replace('$', '')
        LOG.debug('Sending: %r', redacted)

        for i, char in enumerate(sequence):
            if char == '$':
                continue
            frame = NXFrame()
            frame.msgtype = self.MSGTYPE_NX587
            frame.data = char
            frame.checksum = 0
            frame.ackreq = False
            self.write_frame(frame)
            if i in delays:
                time.sleep(delays[i])
