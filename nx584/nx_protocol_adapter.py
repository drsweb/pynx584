"""NX Protocol Adapter.

Provides a unified command interface for NX584 and NX587E protocols,
eliminating conditional branching in NXController.
"""

import abc
import logging
import time

from nx584.nxcore import NXFrame

LOG = logging.getLogger('nx584.adapter')


class NXProtocolAdapter(abc.ABC):
    """Abstract base class for protocol adapters.

    Each concrete implementation encapsulates the command dispatch
    logic for a specific protocol (NX584 or NX587E).
    """

    @abc.abstractmethod
    def arm_stay(self, partition):
        pass

    @abc.abstractmethod
    def arm_exit(self, partition):
        pass

    @abc.abstractmethod
    def arm_auto(self, partition):
        pass

    @abc.abstractmethod
    def police_panic(self, partition):
        pass

    @abc.abstractmethod
    def fire_panic(self, partition):
        pass

    @abc.abstractmethod
    def disarm(self, master_pin, partition, armed=True):
        pass

    @abc.abstractmethod
    def zone_bypass_toggle(self, zone, **kwargs):
        pass

    @abc.abstractmethod
    def drain_queue(self):
        pass

    @abc.abstractmethod
    def execute_command(self, cmd, **kwargs):
        pass

    @abc.abstractmethod
    def generate_heartbeat(self):
        pass

    @abc.abstractmethod
    def startup_sequence(self):
        pass

    @abc.abstractmethod
    def notify_extensions(self, callback_name, *args):
        pass

    @abc.abstractmethod
    def get_command_delay(self):
        pass

    @abc.abstractmethod
    def supports_feature(self, feature):
        pass

    @abc.abstractmethod
    def get_unsupported_endpoints(self):
        pass

    @abc.abstractmethod
    def bypass_needs_partition(self):
        pass


class NX584Adapter(NXProtocolAdapter):
    """Adapter for NX584 protocol (ASCII/Binary).

    Commands are queued as raw byte arrays and sent via the controller.
    """

    def __init__(self, controller):
        self._controller = controller

    def arm_stay(self, partition):
        self._controller._queue.append([0x3E, 0x00, partition])

    def arm_exit(self, partition):
        self._controller._queue.append([0x3E, 0x02, partition])

    def arm_auto(self, partition):
        self._controller._queue.append([0x3D, 0x05, 0x01, 0x01])

    def police_panic(self, partition):
        self._controller._queue.append([0x3E, 0x06, 0x01, partition])

    def fire_panic(self, partition):
        self._controller._queue.append([0x3E, 0x04, 0x01, partition])

    def disarm(self, master_pin, partition, armed=True):
        from nx584.controller import make_pin_buffer
        self._controller._queue.append([0x3C] +
                                       make_pin_buffer(master_pin) +
                                       [0x01, partition])

    def zone_bypass_toggle(self, zone, **kwargs):
        self._controller._queue.append([0x3F, zone - 1])

    def drain_queue(self):
        if not self._controller._queue:
            return
        msg = self._controller._queue.pop(0)
        self._controller._send(msg)

    def execute_command(self, cmd, **kwargs):
        pass  # no-op for NX584

    def generate_heartbeat(self):
        self._controller.get_system_status()

    def startup_sequence(self):
        self._controller.set_time()
        self._controller.get_system_status()

        try:
            max_zone = self._controller._config.getint('config', 'max_zone')
        except configparser.NoOptionError:
            max_zone = 8
            self._controller._config.set('config', 'max_zone', str(max_zone))

        for i in range(1, max_zone + 1):
            self._controller.get_zone_status(i)
            if not self._controller._config.has_option('zones', str(i)):
                self._controller.get_zone_name(i)

    def notify_extensions(self, callback_name, *args):
        if callback_name == 'system_status' and args:
            system = args[0]
            for flag in ('ac_fail', 'low_battery', 'phone_fault',
                         'power_fail', 'system_trouble', 'panel_cleared',
                         'local_alarm', 'remote_alarm', 'phone_tier_1',
                         'phone_tier_2', 'fire_alarm', 'burglar_alarm',
                         'gas_alarm', 'water_alarm', 'freeze_alarm',
                         'trouble_alarm', 'supervisory_alarm', 'state_alarm'):
                if hasattr(system, flag):
                    setattr(system, flag, False)
        return True

    def get_command_delay(self):
        return 0.5

    def supports_feature(self, feature):
        return True

    def get_unsupported_endpoints(self):
        return set()

    def bypass_needs_partition(self):
        return False


class NX587Adapter(NXProtocolAdapter):
    """Adapter for NX587E protocol.

    Commands are dispatched directly to the protocol's execute_command method.
    """

    def __init__(self, protocol, config, command_delay_seconds):
        self._protocol = protocol
        self._config = config
        self._command_delay_seconds = command_delay_seconds

        try:
            self._auto_arm_mode = config.get('nx587e', 'auto_arm_mode').lower()
        except Exception:
            self._auto_arm_mode = 'exit'

        if self._auto_arm_mode not in ('stay', 'exit'):
            LOG.warning('Invalid auto_arm_mode %r in [nx587e] section, using default "exit"', self._auto_arm_mode)
            self._auto_arm_mode = 'exit'

    def arm_stay(self, partition):
        self._protocol.execute_command('arm_stay', partition=partition)

    def arm_exit(self, partition):
        self._protocol.execute_command('arm_exit', partition=partition)

    def arm_auto(self, partition):
        if self._auto_arm_mode == 'stay':
            self._protocol.execute_command('arm_stay', partition=partition)
        else:
            self._protocol.execute_command('arm_exit', partition=partition)

    def police_panic(self, partition):
        self._protocol.execute_command('police', partition=partition)

    def fire_panic(self, partition):
        self._protocol.execute_command('fire', partition=partition)

    def disarm(self, master_pin, partition, armed=True):
        self._protocol.execute_command('disarm', pin=master_pin,
                                       partition=partition, armed=armed)

    def zone_bypass_toggle(self, zone, **kwargs):
        self._protocol.execute_command('zone_bypass_toggle', zone=zone,
                                       **kwargs)

    def drain_queue(self):
        pass  # no-op for NX587

    def execute_command(self, cmd, **kwargs):
        self._protocol.execute_command(cmd, **kwargs)

    def generate_heartbeat(self):
        frame = NXFrame()
        frame.msgtype = 0xFF
        frame.data = 'Q201'
        frame.checksum = 0
        frame.ackreq = False
        self._protocol.write_frame(frame)

    def startup_sequence(self):
        try:
            max_zone = self._config.getint('config', 'max_zone')
        except configparser.NoOptionError:
            max_zone = 8
            self._config.set('config', 'max_zone', str(max_zone))
        max_partitions = 8
        toggles = self._protocol.get_feature_toggles()
        frame = NXFrame()
        frame.msgtype = 0xFF
        frame.data = toggles
        frame.checksum = 0
        frame.ackreq = False
        self._protocol.write_frame(frame)
        for zone in range(1, max_zone + 1):
            query = 'Q%03d' % zone
            frame = NXFrame()
            frame.msgtype = 0xFF
            frame.data = query
            frame.checksum = 0
            frame.ackreq = False
            self._protocol.write_frame(frame)
            time.sleep(self._command_delay_seconds)
        for part in range(1, max_partitions + 1):
            query = 'Q%d' % (192 + part)
            frame = NXFrame()
            frame.msgtype = 0xFF
            frame.data = query
            frame.checksum = 0
            frame.ackreq = False
            self._protocol.write_frame(frame)
            time.sleep(self._command_delay_seconds)
        time.sleep(0.5)

    def notify_extensions(self, callback_name, *args):
        if callback_name == 'device_command':
            return False
        if callback_name == 'log_event':
            return False
        if callback_name == 'system_status' and args:
            system = args[0]
            for flag in ('panel_id', 'ac_fail', 'low_battery', 'phone_fault',
                         'power_fail', 'system_trouble', 'panel_cleared',
                         'local_alarm', 'remote_alarm', 'phone_tier_1',
                         'phone_tier_2', 'fire_alarm', 'burglar_alarm',
                         'gas_alarm', 'water_alarm', 'freeze_alarm',
                         'trouble_alarm', 'supervisory_alarm', 'state_alarm'):
                if hasattr(system, flag):
                    setattr(system, flag, None)
        return True

    def get_command_delay(self):
        return self._command_delay_seconds

    def supports_feature(self, feature):
        return self._protocol.supports_feature(feature)

    def get_unsupported_endpoints(self):
        return self._protocol.get_unsupported_endpoints()

    def bypass_needs_partition(self):
        return self._protocol.bypass_needs_partition()
