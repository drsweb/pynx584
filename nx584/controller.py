import configparser
import datetime
import logging
import serial
import socket
import time

import stevedore.extension

from nx584 import event_queue
from nx584 import mail
from nx584 import model
from nx584.nx587 import NX587Protocol
from nx584.nxcore import NXASCII
from nx584.nxcore import NXBinary
from nx584.nxcore import NXFrame
from nx584.nxcore import NXProtocol
from nx584.nxcore import ReadFailure
from nx584.nxcore import ReadTimeout
from nx584.nxcore import ConnectionLost
from nx584.nx_protocol_adapter import NX584Adapter
from nx584.nx_protocol_adapter import NX587Adapter
from nx584.nxcore import fletcher
from nx584.nxcore import make_ascii

LOG = logging.getLogger('controller')


def make_pin_buffer(digits):
    pinbuf = []
    for i in range(0, 6, 2):
        try:
            pinbuf.append((int(digits[i + 1]) << 4) | int(digits[i]))
        except (IndexError, TypeError):
            pinbuf.append(0xFF)
    return pinbuf


class StreamWrapper(object):
    """Wraps a connection to the control panel.

    This mostly just manages connecting, reconnecting, and some error handling,
    transparent to the protocol (ASCII, Binary, or NX587E) being used.
    """
    def __init__(self, portspec, config):
        self._portspec = portspec
        self._config = config
        try:
            nx587_val = self._config.get('config', 'use_nx587_device')
            self._use_nx587_device = nx587_val.lower() in ('true', '1', 'yes')
        except configparser.NoOptionError:
            self._use_nx587_device = False

        try:
            self._use_binary_protocol = self._config.getboolean(
                'config', 'use_binary_protocol')
        except configparser.NoOptionError:
            self._use_binary_protocol = False

        self._s = None
        self.protocol = None
        self.connect()

    def connect(self):
        # from nx584.nx587 import NX587Protocol
        sleep_time = 0
        while True:
            connected = self._connect()
            if connected:
                if self._use_nx587_device:
                    self.protocol = NX587Protocol(self._s, self._config)
                elif self._use_binary_protocol:
                    self.protocol = NXBinary(self._s)
                else:
                    self.protocol = NXASCII(self._s)
                LOG.info('Connected')
                return True
            sleep_time = min(60, sleep_time + 5)
            LOG.debug('Waiting %i sec before retry...' % sleep_time)
            time.sleep(sleep_time)

    def reconnect(self):
        self._s.close()
        time.sleep(10)
        self.connect()

    def read_frame_raw(self):
        """Read a raw frame from the stream.

        Returns the raw bytes (inclusive of length and checksum) or None
        if there is no data to read.
        """
        try:
            return self.protocol.read_frame()
        except ConnectionLost as e:
            LOG.warning('Connection terminated: %s' % e)
            self.reconnect()
            return None
        except ReadTimeout:
            return None
        except UnicodeDecodeError as e:
            LOG.error('Failed to decode a line; reconnecting: %s' % e)
            return None

    def write_frame_raw(self, data):
        """Writes a frame to the stream.

        NOTE: length and checksum values are added automatically.
        """
        try:
            self.protocol.write_frame(data)
        except ConnectionLost:
            LOG.warning('Failed to send frame; reconnecting')
            self.reconnect()
            # Try to re-send if we reconnect so we don't lose this event
            self.protocol.write(data)


class SocketWrapper(StreamWrapper):
    def _connect(self):
        if self._s:
            self._s.close()
        try:
            self._s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            LOG.debug('Connecting...')
            self._s.connect(self._portspec)
            self._s.settimeout(0.5)
            return True
        except (socket.error, OSError) as ex:
            LOG.error('Failed to connect: %s' % ex)
            self._s = None
            self.protocol = None
            return False


class SerialWrapper(StreamWrapper):
    def _connect(self):
        port, baudrate = self._portspec
        self._s = serial.Serial(port, baudrate, timeout=0.25)
        return self._s.isOpen();


class NXController(object):
    _FEATURE_LIST = (
        'system_status', 'log_events', 'user_codes', 'set_time',
    )

    def __init__(self, portspec, configfile):
        self._portspec = portspec
        self._configfile = configfile
        self._queue_waiting = False
        self._queue_should_wait = False
        self._queue = []
        self.last_active = 0
        self.zones = {}
        self.partitions = {}
        self.users = {}
        self.system = model.System()
        ext_mgr = stevedore.extension.ExtensionManager(
            'pynx584', invoke_on_load=True, invoke_args=(self,))
        self.extensions = [ext_mgr[name] for name in ext_mgr.names()]
        LOG.info('Loaded extensions %s' % ext_mgr.names())
        self._load_config()
        self.event_queue = event_queue.EventQueue(100)
        try:
            self.zone_name_update = self._config.getboolean('config', 'zone_name_update')
        except configparser.NoOptionError:
            self.zone_name_update = True
        try:
            self._idle_time_heartbeat_seconds = self._config.getint(
                'config', 'idle_time_heartbeat_seconds')
        except configparser.NoOptionError:
            self._idle_time_heartbeat_seconds = 120
        self.messaging = None
        self.initial_mqtt_publish_all_completed = False
        self.use_nx587_device = False
        try:
            nx587_val = self._config.get('config', 'use_nx587_device')
            if isinstance(nx587_val, str):
                self.use_nx587_device = nx587_val.lower() in ('true', '1', 'yes')
        except configparser.NoOptionError:
            self.use_nx587_device = False

    @property
    def protocol_features(self):
        if self._adapter is None:
            return {f: True for f in self._FEATURE_LIST}
        return {f: self._adapter.supports_feature(f) for f in self._FEATURE_LIST}

    @property
    def protocol_unsupported_endpoints(self):
        if self._adapter is None:
            return set()
        return self._adapter.get_unsupported_endpoints()

    def protocol_needs_partition_for_bypass(self):
        if self._adapter is None:
            return False
        return self._adapter.bypass_needs_partition()

    def connect(self):
        if '/' in self._portspec[0] or 'COM' in self._portspec[0]:
            self._ser = SerialWrapper(self._portspec, self._config)
        else:
            self._ser = SocketWrapper(self._portspec, self._config)
            LOG.info('Connected')
        command_delay = 0.5
        if self.use_nx587_device:
            try:
                command_delay = float(
                    self._config.get('nx587e', 'command_delay_seconds',
                                     fallback='0.5'))
            except (configparser.NoSectionError, ValueError):
                command_delay = 0.5
            self._adapter = NX587Adapter(self._ser.protocol, self._config,
                                         command_delay)
        else:
            self._adapter = NX584Adapter(self)

    def _load_config(self):
        self._config = configparser.ConfigParser()
        self._config.read(self._configfile)

        if not self._config.has_section('config'):
            self._config.add_section('config')

        if self._config.has_section('zones'):
            for opt in self._config.options('zones'):
                number = int(opt)
                name = self._config.get('zones', opt)
                zone = self._get_zone(number)
                zone.name = name

    def _write_config(self):
        if not self._config.has_section('zones'):
            self._config.add_section('zones')

        for zone in self.zones.values():
            if (not self._config.has_option('zones', str(zone.number)) and
                    zone.name != 'Unknown'):
                self._config.set('zones', str(zone.number), zone.name)
        try:
            with open(self._configfile, 'w') as configfile:
                self._config.write(configfile)
        except IOError as ex:
            LOG.error('Unable to write %s: %s' % (self._configfile, ex))

    @property
    def interior_zones(self):
        return [x for x in self.zones.values() if 'Interior' in x.type_flags]

    @property
    def interior_bypassed(self):
        return all(['Inhibit' in x.condition_flags
                    for x in self.interior_zones])

    def process_next(self):
        data = self._ser.read_frame_raw()
        if not data:
            return None
        self.last_active = time.time()
        if isinstance(data, NXFrame):
            return data
        try:
            frame = NXFrame.decode_line(data)
        except ReadFailure as e:
            LOG.error(str(e))
            return None
        return frame

    def _send(self, data):
        try:
            self._ser.write_frame_raw(data)
        except Exception:
            LOG.exception('Failed to send frame %r' % data)

    def send_ack(self):
        self._send([0x1D])

    def send_nack(self):
        self._send([0x1E])

    def get_zone_name(self, number):
        self._queue.append([0x23, number - 1])

    def get_zone_status(self, number):
        self._queue.append([0x24, number - 1])

    def arm_stay(self, partition):
        self._adapter.arm_stay(partition)

    def arm_exit(self, partition):
        self._adapter.arm_exit(partition)

    def arm_auto(self, partition):
        self._adapter.arm_auto(partition)

    def police_panic(self, partition):
        self._adapter.police_panic(partition)

    def fire_panic(self, partition):
        self._adapter.fire_panic(partition)

    def disarm(self, master_pin, partition):
        armed = False
        if partition in self.partitions:
            partition_obj = self.partitions[partition]
            armed = partition_obj.armed or partition_obj.alarm
        self._adapter.disarm(master_pin, partition, armed=armed)

    def zone_bypass_toggle(self, zone, **kwargs):
        self._adapter.zone_bypass_toggle(zone, **kwargs)

    def get_system_status(self):
        self._queue.append([0x28])

    def get_partition_status(self, partition):
        self._queue.append([0x26, partition - 1])

    def set_time(self):
        now = datetime.datetime.now()
        self._queue.append([0x3B,
                            now.year - 2000,
                            now.month,
                            now.day,
                            now.hour,
                            now.minute,
                            ((now.weekday() + 1) % 7) + 1])

    def get_user_info(self, master_pin, user_number):
        if len(master_pin) < 6:
            master_pin += '00'
        if len(master_pin) != 6:
            LOG.error('Master pin [REDACTED] incorrect length')
            return False
        digits = make_pin_buffer(master_pin)
        self._queue.append([0x32] + digits + [user_number])
        LOG.debug('Sending for user info %s' % digits)
        return True

    def set_user_info(self, master_pin, user, changed):
        if user.number < 1:
            LOG.error('Unable to set PIN for user %i' % user.number)
            return False
        if 'pin' in changed:
            mstr_digits = make_pin_buffer(master_pin)
            user_digits = make_pin_buffer(user.pin)
            LOG.info('Setting user %i PIN to [REDACTED]', user.number)
            self._queue.append(
                [0x34] + mstr_digits + [user.number] + user_digits)
        return True

    def _get_zone(self, number):
        if number not in self.zones:
            self.zones[number] = model.Zone(number)
        return self.zones[number]

    def _get_partition(self, number):
        if number not in self.partitions:
            self.partitions[number] = model.Partition(number)
        return self.partitions[number]

    def _get_user(self, number):
        if number not in self.users:
            self.users[number] = model.User(number)
        return self.users[number]

    def process_msg_3(self, frame):
        # Zone Name
        number = frame.data[0] + 1
        name = ''.join([chr(x) for x in frame.data[1:]])
        LOG.info('Zone %i: %s' % (number, repr(name.strip())))
        if self.zone_name_update:
            self._get_zone(number).name = name.strip()
            LOG.debug('Zone info from %s' % self.zones.keys())
            self._write_config()
        else:
            LOG.debug('Zone name not updating')

    def process_msg_4(self, frame):
        # Zone Status
        zone = self._get_zone(frame.data[0] + 1)
        condition = frame.data[5]
        types = frame.data[2:5]
        zone.state = bool(condition & 0x01)

        zone.condition_flags = []
        for index, string in enumerate(model.Zone.STATUS_FLAGS):
            if condition & (1 << index):
                zone.condition_flags.append(string)

        zone.type_flags = []
        for byte, flags in enumerate(model.Zone.TYPE_FLAGS):
            for bit, name in enumerate(flags):
                if types[byte] & (1 << bit):
                    zone.type_flags.append(name)

        LOG.info('Zone %i (%s) state is %s' % (
            zone.number, zone.name,
            zone.state and 'FAULT' or 'NORMAL'))
        LOG.debug('Zone %i (%s) %s %s' % (zone.number, zone.name,
                                          zone.condition_flags,
                                          zone.type_flags))
        event = {'type': 'zone_status',
                 'timestamp': datetime.datetime.now().isoformat(),
                 'zone': zone.number,
                 'zone_state': zone.state,
                 'zone_flags': zone.condition_flags,
             }
        self.event_queue.push(event)
        for ext in self.extensions:
            ext.obj.zone_status(zone)
        self._publish_zone_messaging(zone)

    def _send_flag_notifications(self, flags_section, flags_key, asserted,
                                 deasserted, email_fn):
        changed = asserted | deasserted
        try:
            flags = set(self._config.get(flags_section,
                                         flags_key).split(','))
        except (configparser.NoOptionError, configparser.NoSectionError):
            flags = set([])

        if any([flag in changed for flag in flags]):
            if deasserted & flags:
                status = '(restored)'
            else:
                status = ''
            sub = '%s %s' % (list(changed & flags), status)
            msg = ('Asserted: %s\n' % (', '.join(asserted)) +
                   'Deasserted: %s\n' % (', '.join(deasserted)))
            email_fn(sub, msg)

    def process_msg_6(self, frame):
        partition = self._get_partition(frame.data[0] + 1)
        partition.last_user = frame.data[5]
        types = frame.data[1:5] + frame.data[6:8]
        was_armed = partition.armed
        orig_flags = partition.condition_flags
        partition.condition_flags = []
        for byte, flags in enumerate(model.Partition.CONDITION_FLAGS):
            for bit, name in enumerate(flags):
                if types[byte] & (1 << bit):
                    partition.condition_flags.append(name)
        if was_armed != partition.armed:
            LOG.info('Partition %i %s armed' % (
                partition.number,
                '' if partition.armed else 'not'))
        LOG.debug('Partition %i %s' % (partition.number,
                                        partition.condition_flags))
        for ext in self.extensions:
            ext.obj.partition_status(partition)
        self._publish_partition_messaging(partition)

        deasserted = set(orig_flags) - set(partition.condition_flags)
        asserted = set(partition.condition_flags) - set(orig_flags)
        changed = asserted | deasserted

        event = {'type': 'partition',
                 'timestamp': datetime.datetime.now().isoformat(),
                 'partition': partition.number,
                 'partition_flags_asserted': list(asserted),
                 'partition_flags': partition.condition_flags,
                 'partition_was_armed': was_armed,
                 'partition_is_armed': partition.armed,
             }
        self.event_queue.push(event)

        if changed:
            mail.send_partition_email(self._config, partition,
                                      deasserted, asserted)

            def email_status(sub, msg):
                mail.send_partition_status_email(self._config, partition,
                                                 'status', sub, msg)

            def email_alarms(sub, msg):
                mail.send_partition_status_email(self._config, partition,
                                                 'alarms', sub, msg)

            section = 'partition_%i' % partition.number
            self._send_flag_notifications(section, 'status_flags', asserted,
                                          deasserted, email_status)
            self._send_flag_notifications(section, 'alarm_flags', asserted,
                                          deasserted, email_alarms)


    def get_partition_state_string(self, partition):
        if partition.alarm:
            return 'triggered'
        elif partition.armed and partition.armedHome:
            return 'armed_home'
        elif partition.armed:
            return 'armed_away'
        else:
            return 'disarmed'

    def publish_all(self):
        if self.messaging is not None:
            self.messaging.publish_all()

    def _publish_zone_messaging(self, zone):
        """Publish zone state to all messaging backends (MQTT, etc)."""
        if self.messaging is None:
            return
        state_str = 'true' if zone.state else 'false'
        self.messaging.publish_zone_state(zone.number, state_str)
        self.messaging.publish_zone_bypassed(zone.number,
                                             str(zone.bypassed).lower())
        self.messaging.publish_zone_condition_flags(zone.number,
                                                    zone.condition_flags)
        self.messaging.publish_zone_type_flags(zone.number, zone.type_flags)
        self.messaging.publish_zone_name(zone.number, zone.name)

    def _publish_partition_messaging(self, partition):
        """Publish partition state to all messaging backends (MQTT, etc)."""
        if self.messaging is None:
            return
        if not self.initial_mqtt_publish_all_completed:
            self.publish_all()
            self.initial_mqtt_publish_all_completed = True
            self.messaging.publish_system_avail('online')
        else:
            state_str = self.get_partition_state_string(partition)
            self.messaging.publish_partition_state(
                partition.number, state_str)
            self.messaging.publish_partition_condition_flags(
                partition.number, partition.condition_flags)

    def process_msg_8(self, frame):
        errors = model.System.STATUS_FLAGS[1] + model.System.STATUS_FLAGS[2]
        status = frame.data[1:10]
        self.system.panel_id = frame.data[0]
        orig_flags = self.system.status_flags
        self.system.status_flags = []
        for byte, flags in enumerate(model.System.STATUS_FLAGS):
            for bit, name in enumerate(flags):
                if status[byte] & (1 << bit):
                    self.system.status_flags.append(name)
        LOG.debug('System status received (panel id 0x%02x)' % (
            self.system.panel_id))

        def _log(flag, asserted):
            if flag not in errors:
                fn = LOG.info
            else:
                if asserted:
                    fn = LOG.error
                else:
                    fn = LOG.warn
            if asserted:
                pfx = ''
            else:
                pfx = 'de-'
            msg = 'System %sasserts %s' % (pfx, flag)
            fn(msg)

        deasserted = set(orig_flags) - set(self.system.status_flags)
        asserted = set(self.system.status_flags) - set(orig_flags)

        for flag in deasserted:
            _log(flag, False)
        for flag in asserted:
            _log(flag, True)

        self._notify_extensions('system_status', self.system)

        if asserted or deasserted:
            mail.send_system_email(self._config, deasserted, asserted)

        for i in range(1, 9):
            if ('Valid partition %i' % i) in asserted:
                LOG.debug('Requesting partition status for %i' % i)
                self.get_partition_status(i)

    def process_msg_9(self, frame):
        commands = {0x28: 'on', 0x38: 'off'}
        house = chr(ord('A') + frame.data[0])
        unit = frame.data[1]
        cmd = commands.get(frame.data[2], frame.data[2])
        LOG.info('Device %s%02i command %s' % (house, unit, cmd))
        event = {'type': 'device-command',
                 'timestamp': datetime.datetime.now().isoformat(),
                 'device': '%s%02i' % (house, unit),
                 'command': '%s' % cmd}
        self.event_queue.push(event)
        if not self.use_nx587_device:
            for ext in self.extensions:
                ext.obj.device_command(house, unit, cmd)

    def process_msg_10(self, frame):
        event = model.LogEvent()
        event.number = frame.data[0]
        event.log_size = frame.data[1]
        event.event_type = frame.data[2] & 0x7F
        event.reportable = bool(frame.data[2] & 0x80)
        event.zone_user_device = frame.data[3] + 1
        event.partition_number = frame.data[4]
        euro_format = self._config.getboolean('config', 'euro_date_format',
                                              fallback=False)
        if euro_format:
            month = frame.data[6]
            day = frame.data[5]
        else:
            month = frame.data[5]
            day = frame.data[6]

        hour = frame.data[7]
        minute = frame.data[8]
        now = datetime.datetime.now()
        if month > now.month:
            year = now.year - 1
        else:
            year = now.year
        try:
            event.timestamp = datetime.datetime(
                year=year, month=month, day=day,
                hour=hour, minute=minute)
        except ValueError:
            LOG.error('Log event had invalid date, or format needs to be set')
            return
        LOG.info('Log event: %s at %s' % (event.event_string,
                                          event.timestamp))
        _event = {'type': 'log',
                  'event': event.event_string,
                  'timestamp': event.timestamp.isoformat(),
              }
        self.event_queue.push(_event)
        if not self.use_nx587_device:
            for ext in self.extensions:
                ext.obj.log_event(event)

        if not self.use_nx587_device:
            mail.send_log_event_mail(self._config, event)

    def process_msg_18(self, frame):
        user = self._get_user(frame.data[0])
        user.pin = []
        user.authority_flags = []
        user.authorized_partitions = []
        for byte in frame.data[1:4]:
            user.pin.append(byte & 0x0F)
            user.pin.append((byte & 0xF0) >> 4)
        authbytetype = frame.data[4] & 0x80
        authbyte = frame.data[4] & 0x7F
        flags = model.User.AUTHORITY_FLAGS[1 if authbytetype else 0]
        for i, flag in enumerate(flags):
            if authbyte & (1 << i):
                user.authority_flags.append(flag)
        for i in range(0, 8):
            if frame.data[5] & (1 << i):
                user.authorized_partitions.append(i + 1)
        LOG.info('Received information about user %i' % user.number)

    def process_nx587_frame(self, frame):
        prefix = frame.data.get('prefix')
        if prefix == 'ZN':
            self.process_nx587_zone_status(frame)
        elif prefix == 'PA':
            self.process_nx587_partition_status(frame)

    def process_nx587_zone_status(self, frame):
        zone_num = frame.data['zone_number']
        status = frame.data['status']
        name = frame.data.get('name')

        zone = self.zones.setdefault(zone_num, model.Zone(zone_num))
        zone.state = bool(status['Faulted'])
        zone.condition_flags = []
        for flag_name in ('Faulted', 'Tamper', 'Trouble', 'Bypass',
                          'Alarm memory', 'Inhibit', 'Low battery',
                          'Lost', 'Bypass memory'):
            if status[flag_name]:
                zone.condition_flags.append(flag_name)

        if name is not None and not self._config.has_option('zones', str(zone_num)):
            zone.name = name
            if self.zone_name_update:
                self._write_config()

        LOG.info('Zone %i (%s) state is %s' % (
            zone.number, zone.name,
            zone.state and 'FAULT' or 'NORMAL'))
        LOG.debug('Zone %i (%s) %s' % (zone.number, zone.name,
                                        zone.condition_flags))
        event = {'type': 'zone_status',
                 'timestamp': datetime.datetime.now().isoformat(),
                 'zone': zone.number,
                 'zone_state': zone.state,
                 'zone_flags': zone.condition_flags,
                 }
        self.event_queue.push(event)
        for ext in self.extensions:
            ext.obj.zone_status(zone)
        self._publish_zone_messaging(zone)

    def process_nx587_partition_status(self, frame):
        part_num = frame.data['partition_number']
        status = frame.data['status']

        partition = self.partitions.setdefault(part_num, model.Partition(part_num))
        was_armed = partition.armed
        status_names = ['Ready to arm', 'Armed', 'Entryguard (stay mode)',
                        'Chime mode on', 'Entry', 'Exit 1', 'Previous alarm',
                        'Siren on']
        partition.condition_flags = []
        for name in status_names:
            if status.get(name):
                partition.condition_flags.append(name)
        if was_armed != partition.armed:
            LOG.info('Partition %i %s armed' % (
                partition.number,
                '' if partition.armed else 'not'))
        LOG.debug('Partition %i %s' % (partition.number,
                                        partition.condition_flags))
        for ext in self.extensions:
            ext.obj.partition_status(partition)
        self._publish_partition_messaging(partition)

        deasserted = set() - set(partition.condition_flags)
        asserted = set(partition.condition_flags) - set()
        changed = asserted | deasserted

        event = {'type': 'partition',
                 'timestamp': datetime.datetime.now().isoformat(),
                 'partition': partition.number,
                 'partition_flags_asserted': list(asserted),
                 'partition_flags': partition.condition_flags,
                 'partition_was_armed': was_armed,
                 'partition_is_armed': partition.armed,
                 }
        self.event_queue.push(event)

        if changed:
            mail.send_partition_email(self._config, partition,
                                      deasserted, asserted)

    def _execute_command(self, cmd, **kwargs):
        self._adapter.execute_command(cmd, **kwargs)

    def _drain_queue(self, frame=None):
        self._adapter.drain_queue()

    def _run_queue(self):
        self._adapter.drain_queue()

    def _notify_extensions(self, callback_name, *args):
        should_call = self._adapter.notify_extensions(callback_name, *args)
        if not should_call:
            return
        for ext in self.extensions:
            callback = getattr(ext.obj, callback_name, None)
            if callback:
                callback(*args)

    def generate_heartbeat_activity(self):
        self._adapter.generate_heartbeat()

    def controller_loop(self):
        self._adapter.startup_sequence()

        watchdog = time.time()

        while self.running:
            frame = self.process_next()
            if frame is None:
                if time.time() - watchdog < self._idle_time_heartbeat_seconds:
                    self._run_queue()
                else:
                    # After time with no activity - generate
                    # something to make sure we are still alive
                    LOG.info('No activity for a while, heartbeating')
                    self.generate_heartbeat_activity()
                    watchdog = time.time()
                continue
            watchdog = time.time()
            if frame.msgtype < len(model.MSG_TYPES):
                type_name = frame.type_name
            else:
                type_name = 'NX587E'
            LOG.debug('Received: %i %s (data %s)' % (frame.msgtype,
                                                      type_name,
                                                      frame.data))
            if self.use_nx587_device and frame.msgtype == 0xFF:
                self.process_nx587_frame(frame)
                if frame.ack_required:
                    LOG.debug('Sending ACK')
                    self.send_ack()
                continue
            if frame.ack_required:
                LOG.debug('Sending ACK')
                self.send_ack()
            else:
                pass
                # This is sometimes too fast if we get two responses
                # from a single command (like time set). Need to keep
                # track of waiting for replies and re-send things when
                # we don't hear back.
                # self._run_queue()
            name = 'process_msg_%i' % frame.msgtype
            if hasattr(self, name):
                try:
                    getattr(self, name)(frame)
                except Exception as e:
                    LOG.exception('Failed to process message type %i',
                                  frame.msgtype)
            else:
                LOG.debug('Unsupported frame type %i (0x%02x)' % (
                    frame.msgtype, frame.msgtype))

    def controller_loop_safe(self):
        self.running = True

        while self.running:
            try:
                self.connect()
                self.controller_loop()
            except Exception as e:
                LOG.exception('Controller loop exited: %s' % e)
                LOG.warning('Waiting 10s before reconnecting...')
                time.sleep(10)
