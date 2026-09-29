"""MQTT messaging backend for pynx584.

Provides MQTT publish/subscribe functionality using paho-mqtt.
Integrates with the Messaging ABC and CompositeMessaging pattern
so that MQTT and HTTP can coexist transparently.
"""

import atexit
import datetime
import logging
import ssl
import time

try:
    import paho.mqtt.client as mqtt
    _HAS_PAHO = True
except ImportError:
    _HAS_PAHO = False

from nx584.messaging import Messaging

LOG = logging.getLogger('nx584.mqtt_messaging')


class MqttMessaging(Messaging):
    """MQTT messaging backend using paho-mqtt."""

    def __init__(self, host, port, controller, username=None, password=None,
                 state_topic_root='home/alarm', command_topic='home/alarm/set',
                 tls_active=False, tls_insecure=False, timeout_sec=10):
        """Initialize MQTT messaging backend.

        Args:
            host: MQTT broker hostname or IP.
            port: MQTT broker port.
            controller: The NXController instance.
            username: Optional username for authentication.
            password: Optional password for authentication.
            state_topic_root: Base topic for state publications.
            command_topic: Topic for receiving commands.
            tls_active: Whether to enable TLS.
            tls_insecure: Whether to skip TLS certificate verification.
            timeout_sec: Connection timeout in seconds.
        """
        if not _HAS_PAHO:
            LOG.error(
                'paho-mqtt is not installed. MQTT messaging will not work. '
                'Install it with: pip install paho-mqtt'
            )
            return

        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._state_topic_root = state_topic_root
        self._command_topic = command_topic
        self._tls_active = tls_active
        self._tls_insecure = tls_insecure
        self._timeout = timeout_sec
        self._dt_sync_counter = 0
        self._controller = controller
        self.client = None
        self.connected = False

        LOG.info('MQTT Client %s:%s', host, port)

        self.connect()
        atexit.register(self._publishLWT)

    def connect(self):
        """Connect to the MQTT broker."""
        if not _HAS_PAHO:
            return

        client = mqtt.Client()

        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.on_message = self.on_message

        # Set reconnection time (60 seconds)
        client.reconnect_delay_set(60)

        if self._username:
            LOG.debug('MQTT Client connecting with username %s', self._username)
            client.username_pw_set(self._username, self._password)

        if self._tls_active:
            context = ssl.create_default_context()
            client.tls_set_context(context)
            client.tls_insecure_set(self._tls_insecure)
            if self._tls_insecure:
                LOG.warning('MQTT TLS insecure is active. This is not advised.')

        try:
            LOG.debug('MQTT Client connecting to host %s at port %s',
                      self._host, self._port)
            client.connect(self._host, int(self._port), int(self._timeout))
            client.loop_start()
            # Publish template cmd to command topic
            client.publish(self._command_topic, 'nop', retain=True)
            self.client = client
        except Exception as ex:
            LOG.error('Unable to connect to %s: %s', self._host, ex)

    def on_connect(self, client, userdata, flags, rc):
        """Callback for when the client receives a CONNACK response."""
        if rc == 0:
            LOG.info('Connected to MQTT broker, subscribing to %s',
                     self._command_topic)
            # Publish initial offline state
            topic = self._state_topic_root + '/system/avail'
            client.publish(topic, 'offline', retain=True)
            # Subscribe to command topic
            sub_result = client.subscribe(self._command_topic)
            if isinstance(sub_result, tuple):
                result, mid = sub_result
                LOG.info('Subscribe result=%s mid=%s topic=%s', result, mid,
                         self._command_topic)
            else:
                LOG.info('Subscribe result=%s topic=%s', sub_result,
                         self._command_topic)
            # Set Last Will message
            client.will_set(self._state_topic_root + '/system/avail',
                            'offline', qos=1, retain=True)
            self.connected = True
        else:
            LOG.error('MQTT connect failure %s', str(rc))

    def on_disconnect(self, client, userdata, rc):
        """Callback for when the client disconnects."""
        self.connected = False
        LOG.warning('MQTT disconnected (rc=%s)', str(rc))

    def publish(self, topic, msg=None, qos=1, retain=True):
        """Publish a message to an MQTT topic.

        Args:
            topic: MQTT topic.
            msg: Message payload.
            qos: Quality of service (0, 1, or 2).
            retain: Whether to retain the message.
        """
        if not _HAS_PAHO:
            return
        if self.connected:
            try:
                LOG.debug('MQTT Client Publish "%s" to "%s"', str(msg), topic)
                self.client.publish(topic, msg, qos, retain)
            except Exception as ex:
                LOG.error('Unable to publish to %s: %s', self._host, ex)
        else:
            LOG.error('Not connected yet, missed publish to %s', self._host)

    # --- Topic builders ---

    def _zone_state_topic(self, zone_number):
        return self._state_topic_root + '/zones/' + str(zone_number) + '/faulted'

    def _zone_bypassed_topic(self, zone_number):
        return self._state_topic_root + '/zones/' + str(zone_number) + '/bypassed'

    def _zone_types_topic(self, zone_number):
        return self._state_topic_root + '/zones/' + str(zone_number) + '/types'

    def _zone_conditions_topic(self, zone_number):
        return self._state_topic_root + '/zones/' + str(zone_number) + '/conditions'

    def _zone_name_topic(self, zone_number):
        return self._state_topic_root + '/zones/' + str(zone_number) + '/name'

    def _partition_state_topic(self, partition_number):
        return self._state_topic_root + '/partitions/' + str(partition_number) + '/state'

    def _partition_conditions_topic(self, partition_number):
        return self._state_topic_root + '/partitions/' + str(partition_number) + '/conditions'

    def _system_id_topic(self):
        return self._state_topic_root + '/system/id'

    def _system_info_topic(self):
        return self._state_topic_root + '/system/info'

    def _system_datetime_topic(self):
        return self._state_topic_root + '/system/datetime'

    def _system_avail_topic(self):
        return self._state_topic_root + '/system/avail'

    # --- Publish methods ---

    def publish_zone_state(self, zone_number, state):
        self.publish(self._zone_state_topic(zone_number), state, qos=1, retain=True)

    def publish_zone_bypassed(self, zone_number, state):
        self.publish(self._zone_bypassed_topic(zone_number), state, qos=1, retain=True)

    def publish_zone_type_flags(self, zone_number, flags):
        self.publish(self._zone_types_topic(zone_number), str(flags), qos=1, retain=True)

    def publish_zone_condition_flags(self, zone_number, flags):
        self.publish(self._zone_conditions_topic(zone_number), str(flags), qos=1, retain=True)

    def publish_zone_name(self, zone_number, name):
        self.publish(self._zone_name_topic(zone_number), name, qos=1, retain=True)

    def publish_partition_state(self, partition_number, state):
        self.publish(self._partition_state_topic(partition_number), state, qos=1, retain=True)

    def publish_partition_condition_flags(self, partition_number, flags):
        self.publish(self._partition_conditions_topic(partition_number),
                     str(flags), qos=1, retain=True)

    def publish_system_id(self, panel_id):
        self.publish(self._system_id_topic(), str(panel_id), qos=1, retain=True)

    def publish_system_info(self, flags):
        self.publish(self._system_info_topic(), str(flags), qos=1, retain=True)

    def publish_system_datetime(self):
        now = datetime.datetime.now()
        self.publish(self._system_datetime_topic(),
                     now.strftime('%m/%d/%Y, %H:%M:%S'),
                     qos=1, retain=False)
        # Sync counter for online publication
        if self._dt_sync_counter < 4:
            self._dt_sync_counter += 1
        if self._dt_sync_counter == 3:
            self._dt_sync_counter += 1
            self.publish(self._system_avail_topic(), 'online', qos=1, retain=True)

    def publish_system_avail(self, status):
        self.publish(self._system_avail_topic(), status, qos=1, retain=True)

    def publish_all(self):
        """Publish all current state. Full sync."""
        if not _HAS_PAHO or not self.connected:
            return

        if self._controller is None:
            LOG.error('Controller not connected.')
            return

        try:
            self.publish_system_id(self._controller.system.panel_id)
            self.publish_system_info(self._controller.system.status_flags)
            self.publish_system_datetime()
        except Exception:
            LOG.exception('Error in publish_all system section')

        try:
            for partition in self._controller.partitions.values():
                try:
                    state_str = self._controller.get_partition_state_string(partition)
                    self.publish_partition_state(partition.number, state_str)
                    self.publish_partition_condition_flags(
                        partition.number, partition.condition_flags)
                except Exception:
                    LOG.exception('Error publishing partition %s', partition.number)
        except Exception:
            LOG.exception('Error in publish_all partition section')

        try:
            for zone in self._controller.zones.values():
                try:
                    self.publish_zone_name(zone.number, zone.name)
                    self.publish_zone_type_flags(zone.number, zone.type_flags)
                    self.publish_zone_condition_flags(zone.number, zone.condition_flags)
                    self.publish_zone_bypassed(zone.number, str(zone.bypassed).lower())
                    state_str = 'true' if zone.state else 'false'
                    self.publish_zone_state(zone.number, state_str)
                except Exception:
                    LOG.exception('Error publishing zone %s', zone.number)
        except Exception:
            LOG.exception('Error in publish_all zone section')

    def _publishLWT(self):
        """Publish Last Will and Testament on abnormal termination."""
        if not _HAS_PAHO:
            return
        try:
            if self.client:
                self.client.publish(self._system_avail_topic(),
                                    'offline', qos=1, retain=True)
                LOG.warning('MQTT disconnection normal...')
            else:
                LOG.warning('MQTT client not initialized, LWT not sent.')
        except Exception:
            LOG.fatal('MQTT abnormal disconnection; Last will message not sent.')

    def _redact_pin(self, payload):
        """Redact PIN from disarm command payloads for logging."""
        if payload.lower().startswith('disarm'):
            fields = payload.split(',', 3)
            if len(fields) == 2:
                return 'disarm,[REDACTED]'
            elif len(fields) >= 3:
                return 'disarm,%s,[REDACTED]' % fields[1]
        return payload

    def _parse_partition(self, fields):
        """Parse partition number from fields, returning (partition, valid).

        Args:
            fields: List of strings from payload.split().

        Returns:
            Tuple of (partition_number, is_valid).
            If valid is False, partition_number is None.
        """
        if len(fields) >= 2:
            try:
                return int(fields[1]), True
            except (ValueError, TypeError):
                return None, False
        return 1, True

    def on_message(self, client, userdata, msg):
        """Callback for when a PUBLISH message is received."""
        if not _HAS_PAHO:
            return
        try:
            payload = msg.payload.lower().decode('utf-8').strip()
            LOG.info('MQTT command received on topic %s: %s', msg.topic,
                     self._redact_pin(payload))

            if self._controller is None:
                LOG.error('Controller not connected.')
                return

            if payload.startswith('bypass_toggle'):
                fields = payload.split(',', 2)
                if len(fields) >= 2:
                    try:
                        zone = int(fields[1])
                        self._controller.zone_bypass_toggle(zone)
                    except (ValueError, TypeError):
                        LOG.error('Invalid zone number: %s', fields[1])
            elif payload.startswith('arm_away'):
                fields = payload.split(',', 3)
                partition, valid = self._parse_partition(fields)
                if not valid:
                    LOG.error('Invalid partition number: %s', fields[1])
                else:
                    self._controller.arm_exit(partition)
                    client.publish(self._command_topic, 'nop', retain=True)
            elif payload.startswith('arm_home'):
                fields = payload.split(',', 3)
                partition, valid = self._parse_partition(fields)
                if not valid:
                    LOG.error('Invalid partition number: %s', fields[1])
                else:
                    self._controller.arm_stay(partition)
                    client.publish(self._command_topic, 'nop', retain=True)
            elif payload.startswith('disarm'):
                fields = payload.split(',', 3)
                if len(fields) == 2:
                    pin = fields[1]
                    partition = 1
                elif len(fields) >= 3:
                    partition, valid = self._parse_partition(fields)
                    if not valid:
                        LOG.error('Invalid partition number: %s', fields[1])
                        partition = 1
                    pin = fields[2]
                else:
                    pin = None
                if pin:
                    self._controller.disarm(pin, partition)
                    client.publish(self._command_topic, 'nop', retain=True)
            elif payload == 'time':
                self._controller.set_time()
            elif payload.startswith('police'):
                fields = payload.split(',', 2)
                partition, valid = self._parse_partition(fields)
                if not valid:
                    LOG.error('Invalid partition number: %s', fields[1])
                else:
                    self._controller.police_panic(partition)
                    client.publish(self._command_topic, 'nop', retain=True)
            elif payload.startswith('fire'):
                fields = payload.split(',', 2)
                partition, valid = self._parse_partition(fields)
                if not valid:
                    LOG.error('Invalid partition number: %s', fields[1])
                else:
                    self._controller.fire_panic(partition)
                    client.publish(self._command_topic, 'nop', retain=True)
            elif payload == 'status':
                self._controller.publish_all()
            elif payload == 'nop':
                pass
            else:
                LOG.error("Unknown command: '%s'", payload)
        except Exception:
            LOG.exception('Error processing MQTT command: %s',
                          msg.payload.decode('utf-8', errors='replace'))
