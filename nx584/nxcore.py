"""Core NX protocol abstractions.

Contains the base protocol classes (NXProtocol, NXASCII, NXBinary),
the frame representation (NXFrame), and supporting utilities
(fletcher checksum, ASCII encoding/decoding).
"""

import configparser
import logging
import socket
import time

from nx584 import model

LOG = logging.getLogger('nxcore')


def parse_ascii(data):
    data_bytes = []
    for i in range(0, len(data), 2):
        data_bytes.append(int(data[i:i + 2], 16))
    return data_bytes


def make_ascii(data):
    data_chars = []
    for b in data:
        data_chars.append('%02X' % b)
    return ''.join(data_chars)


def fletcher(data, k=16):
    if k not in (16, 32, 64):
        raise ValueError("Valid choices of k are 16, 32 and 64")
    nbytes = k // 16
    mod = 2 ** (8 * nbytes) - 1
    s = s2 = 0
    for t in data:
        s += t
        s2 += s
    cksum = s % mod + (mod + 1) * (s2 % mod)
    sum1 = cksum & 0xFF
    sum2 = (cksum & 0xFF00) >> 8
    return sum1, sum2


class NXFrame(object):
    def __init__(self):
        self.length = 0
        self.msgtype = 0
        self.checksum = 0
        self.data = []
        self.ackreq = False

    @property
    def ack_required(self):
        return self.ackreq

    @staticmethod
    def decode_line(line_bytes):
        self = NXFrame()

        s1, s2 = fletcher(line_bytes[:-2])
        if [s1, s2] != line_bytes[-2:]:
            raise ReadFailure('Checksum mismatch on received frame')

        self.length = line_bytes[0]
        msgtypefield = line_bytes[1]
        self.checksum = (line_bytes[-2] << 8) & line_bytes[-1]
        self.data = line_bytes[2:-2]
        self.ackreq = bool(msgtypefield & 0x80)
        self.msgtype = msgtypefield & 0x7F
        return self

    @property
    def type_name(self):
        return model.MSG_TYPES[self.msgtype]


class ReadTimeout(Exception):
    pass


class ConnectionLost(Exception):
    pass


class ReadFailure(Exception):
    pass


class NXProtocol(object):
    """Abstract the act of talking to the control panel."""

    # Protocol capabilities
    FEATURE_LIST = (
        'system_status', 'log_events', 'user_codes', 'set_time',
    )
    UNSUPPORTED_FEATURES = set()
    UNSUPPORTED_ENDPOINTS = set()
    BYPASS_NEEDS_PARTITION = False

    def __init__(self, stream):
        self.stream = stream

    def read(self, n):
        """Read bytes from the stream.

        Does the right thing for sockets and serial ports.

        :raises: ReadTimeout if there is no data
        :raises: ConnectionLost if something goes wrong
        """
        if hasattr(self.stream, 'read'):
            r = self.stream.read(n)
            if r == b'':
                raise ReadTimeout()
            return r
        elif hasattr(self.stream, 'recv'):
            try:
                r = self.stream.recv(n)
            except socket.timeout:
                raise ReadTimeout()
            except (socket.error, OSError):
                raise ConnectionLost()
            if r == b'':
                raise ConnectionLost()
            return r
        else:
            raise ConnectionLost('What is this stream?')

    def write(self, data):
        """Write bytes to the stream.

        :raises: ConnectionLost if something goes wrong
        """
        if hasattr(self.stream, 'write'):
            self.stream.write(data)
        elif hasattr(self.stream, 'send'):
            try:
                self.stream.send(data)
            except (socket.error, OSError):
                raise ConnectionLost()
        else:
            raise ConnectionLost('What is this stream?')

    def discard_until(self, byte):
        """Reads any unexpected data until we encounter an expected byte.

        :raises: ReadTimeout (via self.read()) if we never get it.
        """
        while True:
            c = self.read(1)
            if c == byte:
                return c
            LOG.warning('Seeking (discarded %s %02x)' % (c, c[0]))

    def read_frame(self):
        """Read a whole frame from the stream.

        :returns: An array of int values, inclusive of the length and checksum
                  bytes
        :raises: ConnectionLost if something goes wrong or the stream cannot
                 be parsed
        """
        return []

    def write_frame(self, data):
        """Write a whole frame to the stream.

        NOTE: This adds the length and checksum bytes automatically.

        :param: data is an array of integers
        """
        pass

    def supports_feature(self, feature):
        """Check if this protocol supports a named feature.

        :param feature: Feature name (e.g., 'system_status', 'user_codes')
        :returns: True if supported, False if not
        """
        return feature not in self.UNSUPPORTED_FEATURES

    def get_unsupported_endpoints(self):
        """Return set of REST API endpoints not supported by this protocol.

        :returns: Set of endpoint names (e.g., {'users', 'set_time'})
        """
        return self.UNSUPPORTED_ENDPOINTS

    def bypass_needs_partition(self):
        """Check if zone bypass toggle requires partition specification.

        :returns: True if partition is required for bypass operations
        """
        return self.BYPASS_NEEDS_PARTITION


class NXASCII(NXProtocol):
    def read_frame(self):
        self.discard_until(b'\n')

        start = time.time()
        line = ''
        while not line.endswith('\r'):
            try:
                c = self.read(1).decode()
            except ReadTimeout:
                LOG.error('Mid-frame read timeout (got %i: %r)' % (
                    len(line), line))
                raise ConnectionLost()
            line += c
            if time.time() - start > 60:
                LOG.error('Timeout reading a line, killing connection')
                raise ConnectionLost()

        LOG.debug('Parsing ASCII frame %r' % line.strip())
        try:
            return parse_ascii(line.strip())
        except Exception:
            LOG.exception('Failed to parse raw ASCII line %r' % line.strip())
            raise ConnectionLost()

    def write_frame(self, data):
        data = [len(data)] + data
        data += fletcher(data)
        raw = '\n%s\r' % make_ascii(data)
        self.write(raw.encode())


class NXBinary(NXProtocol):
    def read_frame(self):
        self.discard_until(b'\x7e')

        length = self.read(1)[0]
        buffer = [length]
        start = time.time()

        bytestuffed = False

        i = 1
        while (i <= length + 2):
            try:
                c = self.read(1)[0]
            except ReadTimeout:
                LOG.error('Mid-frame read timeout (expected %i got %i)' % (
                    length, i))
                raise ConnectionLost()

            if c == 0x7e:
                raise ConnectionLost('Received start byte mid-frame!')

            # Adjust any byte stuffed bytes - Skip the current byte if so,
            # then XOR the following byte with 0x20
            if c == 0x7d:
                bytestuffed = True
            else:
                if bytestuffed:
                    c = c ^ 0x20
                    bytestuffed = False
                buffer.append(c)
                i += 1

            if time.time() - start > 60:
                LOG.error('Timeout reading a line, killing connection')
                raise ConnectionLost()

        return buffer

    def write_frame(self, data):
        data = [len(data)] + data
        data += fletcher(data)
        # Byte Stuff Any 0x7d, 0x7e bytes
        bytestuff = [i for i, x in enumerate(data) if x == 0x7d]
        for i, index in reversed(list(enumerate(bytestuff))):
            data[index:index+1] = [0x7d, 0x5d]
        bytestuff = [i for i, x in enumerate(data) if x == 0x7e]
        for i, index in reversed(list(enumerate(bytestuff))):
            data[index:index+1] = [0x7d, 0x5e]

        # add the start character 0x7e
        data.insert(0, 0x7e)

        self.write(bytes(data))
