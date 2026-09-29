import argparse
import atexit
import logging
import logging.handlers
import os
import re
import sys
import threading
import time

from nx584 import api
from nx584 import controller
from nx584 import mqtt_messaging
from nx584.messaging import CompositeMessaging
from nx584.http_messaging import HttpMessaging

VERSION = '0.8.3'

LOG_FORMAT = '%(asctime)-15s %(module)s %(levelname)s %(message)s'
LOG = logging.getLogger('nx584.main')


def _redact_query_params(msg):
    """Redact sensitive query parameter values from log messages."""
    if isinstance(msg, str):
        for param in ('master_pin', 'pin'):
            msg = re.sub(r'(%s)=[^&]*' % param, r'\1=[REDACTED]', msg)
    return msg


class PinRedactingFilter(logging.Filter):
    """Logging filter that redacts PINs from log messages."""

    def filter(self, record):
        record.msg = _redact_query_params(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _redact_query_params(str(v)) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_redact_query_params(str(v)) if isinstance(v, str) else v for v in record.args)
        return True


class RedactingLogger:
    """Logger wrapper that redacts sensitive query parameters."""

    def __init__(self, logger):
        self._logger = logger

    def info(self, msg, *args, **kwargs):
        self._logger.info(_redact_query_params(msg), *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self._logger.warning(_redact_query_params(msg), *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self._logger.error(_redact_query_params(msg), *args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        self._logger.exception(_redact_query_params(msg), *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        self._logger.debug(_redact_query_params(msg), *args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        self._logger.critical(_redact_query_params(msg), *args, **kwargs)

    def fatal(self, msg, *args, **kwargs):
        self._logger.fatal(_redact_query_params(msg), *args, **kwargs)


def _read_mqtt_config(config):
    """Read MQTT config from config.ini [mqtt] section.

    Returns a dict of MQTT parameters, or None if no [mqtt] section exists.
    """
    if not config.has_section('mqtt'):
        return None
    mqtt = {}
    for key in ('host', 'username', 'password', 'state_topic_root', 'command_topic'):
        try:
            mqtt[key] = config.get('mqtt', key)
        except Exception:
            pass
    for key in ('port', 'tls_active', 'tls_insecure', 'timeout'):
        try:
            val = config.get('mqtt', key)
            if key in ('port', 'timeout'):
                mqtt[key] = int(val)
            elif key in ('tls_active', 'tls_insecure'):
                mqtt[key] = val.lower() in ('true', '1', 'yes')
            else:
                mqtt[key] = val
        except Exception:
            pass
    return mqtt


def _build_messaging(ctrl, mqtt_config, args):
    """Build the messaging backend(s) and assign to ctrl.messaging.

    Always creates HttpMessaging. Conditionally creates MqttMessaging
    if MQTT is enabled via --mqtt flag. Uses CompositeMessaging when
    both are present.

    MQTT parameters are resolved with this precedence:
    CLI args > config.ini [mqtt] section > hardcoded defaults.

    Args:
        ctrl: NXController instance.
        mqtt_config: Dict of MQTT params from config.ini, or None.
        args: Parsed CLI arguments.

    Returns:
        CompositeMessaging or HttpMessaging instance
    """
    backends = [HttpMessaging()]

    if args.mqtt:
        # Resolve broker host: CLI --mqtt HOST > config.ini > error
        mqtt_host = None
        if isinstance(args.mqtt, str):
            mqtt_host = args.mqtt
        elif mqtt_config and mqtt_config.get('host'):
            mqtt_host = mqtt_config['host']

        LOG.debug('MQTT: args.mqtt=%r mqtt_config=%r mqtt_host=%r',
                   args.mqtt, mqtt_config, mqtt_host)

        if not mqtt_host:
            LOG.error(
                'MQTT is enabled but no broker host is configured. '
                'Provide --mqtt HOST or set [mqtt] host in config.ini'
            )
            sys.exit(1)

        try:
            from nx584.mqtt_messaging import MqttMessaging, _HAS_PAHO
        except ImportError:
            _HAS_PAHO = False

        if not _HAS_PAHO:
            LOG.error(
                'MQTT is enabled but paho-mqtt is not installed. '
                'Install it with: pip install paho-mqtt'
            )
            sys.exit(1)

        # Resolve other params: CLI > config.ini > default
        mqtt_port = args.mqttPort if args.mqttPort is not None else mqtt_config.get('port', 1883)
        mqtt_username = args.username if args.username is not None else mqtt_config.get('username')
        mqtt_password = args.password if args.password is not None else mqtt_config.get('password')
        mqtt_state_topic_root = (args.stateTopicRoot if args.stateTopicRoot is not None
                                  else mqtt_config.get('state_topic_root', 'home/alarm'))
        mqtt_command_topic = (args.commandTopic if args.commandTopic is not None
                               else mqtt_config.get('command_topic', 'home/alarm/set'))
        mqtt_tls_active = args.mqttTlsActive if args.mqttTlsActive is not None else mqtt_config.get('tls_active', False)
        mqtt_tls_insecure = args.mqttTlsInsecure if args.mqttTlsInsecure is not None else mqtt_config.get('tls_insecure', False)
        mqtt_timeout = args.timeout if args.timeout is not None else mqtt_config.get('timeout', 10)

        mqtt = MqttMessaging(
            host=mqtt_host,
            port=mqtt_port,
            controller=ctrl,
            username=mqtt_username,
            password=mqtt_password,
            state_topic_root=mqtt_state_topic_root,
            command_topic=mqtt_command_topic,
            tls_active=mqtt_tls_active,
            tls_insecure=mqtt_tls_insecure,
            timeout_sec=mqtt_timeout,
        )
        backends.append(mqtt)

    if len(backends) == 1:
        ctrl.messaging = backends[0]
    else:
        ctrl.messaging = CompositeMessaging()
        for backend in backends:
            ctrl.messaging.add_backend(backend)

    return ctrl.messaging


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', action='store_true',
                        help='Display version')
    parser.add_argument('--config', default='config.ini',
                        metavar='FILE',
                        help='Path to config file')
    parser.add_argument('--debug', default=False, action='store_true',
                        help='Enable debug')
    parser.add_argument('--debug-log', default=None,
                        help='Debug to this log file only')
    parser.add_argument('--log', default=None,
                        metavar='FILE',
                        help='Path to log file')
    levels = ('DEBUG', 'INFO', 'WARNING')
    parser.add_argument('--logLevel', default='INFO', choices=levels,
                        metavar='LOG_LEVEL_CONSOLE',
                        help='Level of log displayed to console')
    parser.add_argument('--connect', default=None,
                        metavar='HOST:PORT',
                        help='Host and port to connect for serial stream')
    parser.add_argument('--serial', default=None,
                        metavar='PORT',
                        help='Serial port to open for stream')
    parser.add_argument('--baudrate', default=38400, type=int,
                        metavar='BAUD',
                        help='Serial baudrate')
    parser.add_argument('--listen', default='127.0.0.1',
                        metavar='ADDR',
                        help='Listen address (defaults to 127.0.0.1)')
    parser.add_argument('--port', default=5007, type=int,
                        help='Listen port (defaults to 5007)')
    # MQTT (Optional)
    parser.add_argument('--mqtt', nargs='?', const=True, default=None,
                        metavar='HOST',
                        help='Enable MQTT (optional HOST overrides config.ini)')
    parser.add_argument('--mqttPort', default=None, type=int,
                        metavar='PORT',
                        help='MQTT broker port (default: 1883)')
    parser.add_argument('--username', default=None,
                        metavar='USER',
                        help='MQTT username')
    parser.add_argument('--password', default=None,
                        metavar='PASS',
                        help='MQTT password')
    parser.add_argument('--stateTopicRoot', default=None,
                        metavar='TOPIC',
                        help='MQTT state topic root (default: home/alarm)')
    parser.add_argument('--commandTopic', default=None,
                        metavar='TOPIC',
                        help='MQTT command topic (default: home/alarm/set)')
    parser.add_argument('--mqttTlsActive', default=None, action='store_true',
                        help='Enable TLS for MQTT')
    parser.add_argument('--mqttTlsInsecure', default=None, action='store_true',
                        help='Skip TLS certificate verification')
    parser.add_argument('--timeout', default=None, type=int,
                        metavar='SECS',
                        help='MQTT connection timeout in seconds (default: 10)')

    args = parser.parse_args()

    if args.version:
        print(VERSION)
        sys.exit(0)

    LOG = logging.getLogger()
    LOG.setLevel(logging.DEBUG)
    formatter = logging.Formatter(LOG_FORMAT)
    istty = os.isatty(0)

    if (args.debug or args.debug_log) and not istty:
        debug_handler = logging.handlers.RotatingFileHandler(
            args.debug_log or 'debug.log',
            maxBytes=1024*1024*10,
            backupCount=3)
        debug_handler.setFormatter(formatter)
        debug_handler.setLevel(logging.DEBUG)
        LOG.addHandler(debug_handler)

    if istty or not args.log:
        verbose_handler = logging.StreamHandler()
        verbose_handler.setFormatter(formatter)
        if args.logLevel == 'DEBUG':
            verbose_handler.setLevel(logging.DEBUG)
        elif args.logLevel == 'WARNING':
            verbose_handler.setLevel(logging.WARNING)
        else:
            verbose_handler.setLevel(logging.INFO)
        LOG.addHandler(verbose_handler)

    if args.log:
        log_handler = logging.handlers.RotatingFileHandler(
            args.log,
            maxBytes=1024*1024*10,
            backupCount=3)
        log_handler.setFormatter(formatter)
        log_handler.setLevel(logging.INFO)
        LOG.addHandler(log_handler)

    LOG.info('Ready')
    logging.getLogger('connectionpool').setLevel(logging.WARNING)

    if args.connect:
        host, port = args.connect.split(':')
        ctrl = controller.NXController((host, int(port)), args.config)
    elif args.serial:
        ctrl = controller.NXController((args.serial, args.baudrate), args.config)
    else:
        LOG.error('Either host:port or serial and baudrate are required')
        return
    # Set controller reference BEFORE building messaging backends
    # (MQTT client starts its event loop during _build_messaging,
    #  so on_message may fire before we get here)
    api.set_controller(ctrl)

    for logger_name in ('werkzeug', 'flask._internal'):
        logger = logging.getLogger(logger_name)
        logger.addFilter(PinRedactingFilter())

    # Read MQTT config from config.ini
    mqtt_config = _read_mqtt_config(ctrl._config)
    LOG.debug('Config sections: %s', ctrl._config.sections())

    # Build messaging backends
    _build_messaging(ctrl, mqtt_config, args)

    # MQTT-only mode: no Flask, poll for MQTT connection
    # MQTT-only when --mqtt is set AND --listen is empty string
    mqtt_only = args.mqtt is not None and (args.listen == '' or args.listen is None)
    if mqtt_only:
        LOG.info('MQTT-only mode enabled')

        def publish_offline():
            if hasattr(ctrl.messaging, '_publishLWT'):
                ctrl.messaging._publishLWT()

        atexit.register(publish_offline)

        # Wait for MQTT connection (max 60 seconds)
        max_wait = 12  # 12 * 5 seconds = 60 seconds
        wait_count = 0
        online_published = False

        while ctrl.running if hasattr(ctrl, 'running') else True:
            if hasattr(ctrl.messaging, 'connected') and ctrl.messaging.connected:
                # Wait for initial sync to complete
                if not ctrl.initial_mqtt_publish_all_completed:
                    time.sleep(1)
                    continue

                # Publish online after sync completes
                if not online_published:
                    if hasattr(ctrl.messaging, 'publish_system_avail'):
                        ctrl.messaging.publish_system_avail('online')
                    online_published = True
                # Continue polling loop, check every 5 seconds
                time.sleep(5)
            else:
                wait_count += 1
                if wait_count >= max_wait:
                    LOG.error('MQTT broker not reachable after %d seconds',
                              max_wait * 5)
                    sys.exit(1)
                time.sleep(5)
                LOG.info('Waiting for MQTT broker... (%d/%d)',
                         wait_count, max_wait)
        return

    # Standard mode: start controller loop + Flask API
    t = threading.Thread(target=ctrl.controller_loop_safe)
    t.daemon = True
    t.start()

    api.app.run(debug=False, host=args.listen, port=args.port, threaded=True)
