NetworX NX584/NX8E Interface Library and Server
===============================================

.. image:: https://github.com/kk7ds/pynx584/actions/workflows/test.yaml/badge.svg

This is a tool to let you interact with your NetworX alarm panel via
the NX584 module (which is built into NX8E panels). You must enable it
in the configuration and enable the operations you want to be able to
do before this will work.

This tool supports both the ASCII or Binary home automation
protocol (and works with the binary only NX590E in addition to the
ASCII or binary NX584/NX8E)

Install Locally
***************

::

 # pip install pynx584


Or to enable MQTT messaging, install the optional dependency::

 # pip install pynx584[mqtt]

The server must be run on a machine with connectivity to the panel,
which can be a local serial port, or a Serial-over-LAN device (i.e. a
TCP socket). For example::

 # nx584_server --serial /dev/ttyS0 --baud 38400

or::

 # nx584_server --connect 192.168.1.101:23

Once that is running, you should be able to do something like this::

 $ nx584_client summary
 +------+-----------------+--------+--------+
 | Zone |       Name      | Bypass | Status |
 +------+-----------------+--------+--------+
 |  1   |    FRONT DOOR   |   -    | False  |
 |  2   |   GARAGE DOOR   |   -    | False  |
 |  3   |     SLIDING     |   -    | False  |
 |  4   | MOTION DETECTOR |   -    | False  |
 +------+-----------------+--------+--------+
 Partition 1 armed

 # Arm for stay with auto-bypass
 $ nx584_client arm-stay

 # Arm for exit (requires tripping an entry zone)
 $ nx584_client arm-exit

 # Auto-arm (no bypass, no entry zone trip required)
 $ nx584_client arm-auto

  # Disarm
  $ nx584_client disarm --master 1234

  # Trigger police panic (partition 1)
  $ nx584_client police --partition 1

  # Trigger fire panic (partition 2)
  $ nx584_client fire --partition 2

  Install via Docker Compose
**************************
Before creating the Docker container, you need to define how you connect to the panel (local serial port, or a Serial-over-LAN device (i.e. a TCP socket)) in the :code:`docker-compose.yml` file. Uncomment and edit the :code:`environment` section to fit your needs::

 version: "3.2"

 services:
   pynx584:
     container_name: pynx584
     image: kk7ds/pynx584
     build:
       context: .docker
       dockerfile: Dockerfile
     restart: unless-stopped
     ports:
       - 5007:5007
     environment:
       # Uncomment these as needed, depending on how you connect to the panel (via Serial or TCP Socket)
       # - SERIAL=/dev/ttyS0
       # - BAUD=38400
       # - CONNECT=192.168.1.101:23

To build the image, create the Docker container and then run it, make sure you're at the root of the checked out repo and run::

 # docker-compose up -d

You should now be able to conect to the pynx584 Docker container via its exposed port (default :code:`5007`).

MQTT with Docker Compose
************************

To enable MQTT with Docker, mount a config.ini with an ``[mqtt]`` section and add the ``--mqtt`` flag::

  version: "3.2"

  services:
    pynx584:
      container_name: pynx584
      image: ghcr.io/kk7ds/pynx584
      devices:
        - "/dev/ttyUSB0:/dev/ttyUSB0"
      volumes:
        - ./config:/config
      command:
        - --serial
        - /dev/ttyUSB0
        - --baudrate
        - "38400"
        - --mqtt
        - --listen
        - ""
      depends_on:
        - mqtt-broker

    mqtt-broker:
      container_name: mqtt-broker
      image: eclipse-mosquitto:latest
      ports:
        - "1883:1883"

The ``config/alarm.ini`` should contain::

  [config]
  max_zone = 5

  [mqtt]
  host = mqtt-broker
  port = 1883
  state_topic_root = home/alarm

Config
******

The `config.ini` should be generated once the controller reports the first
zone name. However, here is a full `config.ini` if you want to pre-populate
it with zone names::

 [config]
 # max_zone is the highest numbered zone you have populated
 max_zone = 5

 # Disable zone name updates from NX8 system. If your system doesn't set zone
 # names and you prefer define them through pynx584, set this value to False.
 # Defaults to True
 # zone_name_update = False

 # Set to true if your unit sends DD/MM dates instead of MM/DD
 euro_date_format = False
 
 # Set To true if using binary protocol
 # Defaults to False
 # use_binary_protocol = True

 # Set To true if using an NX587e device to communicate with your security panel
 # If true, add [nx587e] section to config
 # Defaults to False
 # use_nx587_device = True

 # Length of idle time before sending heartbeat keep alive
 # (This is useful with the NX590 as it disconnects idle connections in a
 # short period of time)
 # Defaults to 120 seconds
 # idle_time_heartbeat_seconds = 20

 [email]
 fromaddr = security@foo.com
 smtphost = imap.foo.com
 
  [zones]
  # Zone names
  1 = Front Door
  2 = Garage Entry
  3 = Garage Side
  4 = Garage Back
  5 = Kitchen

  [nx587e]
  command_delay_seconds = 0.5

  # auto_arm_mode: what the arm-auto command sends for NX587E protocol
  # set this to match the "arm auto in stay mode" setting of your NX Panel
  # Valid values: stay, exit (default: exit)
  # auto_arm_mode = stay

MQTT Support
************

To enable MQTT messaging, install the optional dependency::

 # pip install pynx584[mqtt]

Add an ``[mqtt]`` section to your ``config.ini``::

 [mqtt]
 host = 192.168.1.100
 port = 1883
 username = myuser
 password = mypass
 state_topic_root = home/alarm
 command_topic = home/alarm/set
 tls_active = False
 tls_insecure = False
 timeout = 10

Enable MQTT with the ``--mqtt`` flag::

 # nx584_server --serial /dev/ttyS0 --baud 38400 --mqtt

The server supports three modes:

- **HTTP-only** (default): No ``--mqtt`` flag
- **HTTP+MQTT**: ``--mqtt`` flag (with or without ``--listen``)
- **MQTT-only**: ``--mqtt`` flag with ``--listen ''`` (empty string)

MQTT parameters can be overridden via CLI::

 # nx584_server --serial /dev/ttyS0 --mqtt 192.168.1.100 --mqttPort 1884

MQTT Topic Structure
********************

State topics (published by pynx584, retained)::

 home/alarm/zones/{N}/faulted        — Zone faulted state (true/false)
 home/alarm/zones/{N}/bypassed       — Zone bypassed state (true/false)
 home/alarm/zones/{N}/types          — Zone type flags (list string)
 home/alarm/zones/{N}/conditions     — Zone condition flags (list string)
 home/alarm/zones/{N}/name           — Zone name
  home/alarm/partitions/{N}/state     — Partition state (disarmed/armed_away/armed_home/triggered)
 home/alarm/partitions/{N}/conditions — Partition condition flags (list string)
 home/alarm/system/id                — Panel ID
 home/alarm/system/info              — System status flags (list string)
 home/alarm/system/datetime          — System datetime (not retained)
  home/alarm/system/avail             — Availability (online/offline)

Note: "initializing" state is planned for future implementation but not yet supported.

Command topic (subscribed by pynx584)::

 home/alarm/set

Commands::

  bypass_toggle,{zone}    — Toggle zone bypass
  arm_away,{partition}    — Arm partition away
  arm_home,{partition}    — Arm partition home (stay)
  disarm,{partition},{code}  — Disarm partition
  police,{partition}      — Trigger police panic
  fire,{partition}        — Trigger fire panic
  time                    — Set controller time
  status                  — Trigger full state republish
  nop                     — No operation (clears command)

Panel Configuration
*******************

Your NX panel must be configured properly to allow this software to
work with it. If it was configured by a monitoring company, there is a
very good chance that it will need quite a bit of tweaking first. Here
is a good start to what needs to be set and how:

- Protocol: ASCII
- Speed: 38400 Baud (OR WHICHEVER YOU CHOOSE)
- Enabled Transition Messages:

 * Interface Configuration Message
 * Zone Status Message
 * Partition Status Message
 * System Status Message
 * Log Event Message
 * Keypad Message Received (OPTIONAL)
 * X-10 Message Received (OPTIONAL)

- Enabled Commands:

 * Interface Configuration Request
 * Zone Name Request
 * Zone Status Request
 * Zones Snapshot Request
 * Partition Status Request
 * Partitions Snapshot Request
 * Send X-10 Message (OPTIONAL)
 * Log Event Request
 * Send Keypad Text Message (OPTIONAL)
 * System Status Request
 * Program Data Request (OPTIONAL)
 * Program Data Command (OPTIONAL)
 * Set Clock / Calendar Command
 * Primary Keypad Function with PIN (OPTIONAL)
 * Secondary Keypad Function (OPTIONAL)
 * Zone Bypass Toggle (OPTIONAL)
