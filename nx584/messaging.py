"""Abstract base class and composite dispatcher for messaging backends.

Defines the Messaging ABC with 12 publish methods for zone, partition,
and system state.  CompositeMessaging dispatches calls to all registered
backends so that HTTP and MQTT can coexist without if/then branching.
"""

import abc
import logging

from nx584._controller import CONTROLLER

LOG = logging.getLogger('nx584.messaging')


class Messaging(abc.ABC):
    """Abstract base class for messaging backends."""

    @abc.abstractmethod
    def publish_zone_state(self, zone_number: int, state: str) -> None:
        """Publish zone faulted state.

        Args:
            zone_number: 1-based zone number.
            state: One of 'faulted', 'trouble', 'bypassed', 'low_battery',
                   'tamper', 'clean'.
        """

    @abc.abstractmethod
    def publish_zone_bypassed(self, zone_number: int, state: str) -> None:
        """Publish zone bypassed state.

        Args:
            zone_number: 1-based zone number.
            state: 'bypassed' or 'restored'.
        """

    @abc.abstractmethod
    def publish_zone_type_flags(self, zone_number: int, flags: list) -> None:
        """Publish zone type flags.

        Args:
            zone_number: 1-based zone number.
            flags: List of type flag strings.
        """

    @abc.abstractmethod
    def publish_zone_condition_flags(self, zone_number: int, flags: list) -> None:
        """Publish zone condition flags.

        Args:
            zone_number: 1-based zone number.
            flags: List of condition flag strings.
        """

    @abc.abstractmethod
    def publish_zone_name(self, zone_number: int, name: str) -> None:
        """Publish zone name.

        Args:
            zone_number: 1-based zone number.
            name: Zone name string.
        """

    @abc.abstractmethod
    def publish_partition_state(self, partition_number: int, state: str) -> None:
        """Publish partition armed state.

        Args:
            partition_number: 1-based partition number.
            state: One of 'disarmed', 'armed_away', 'armed_home'.
        """

    @abc.abstractmethod
    def publish_partition_condition_flags(self, partition_number: int, flags: list) -> None:
        """Publish partition condition flags.

        Args:
            partition_number: 1-based partition number.
            flags: List of condition flag strings.
        """

    @abc.abstractmethod
    def publish_system_id(self, panel_id: str) -> None:
        """Publish system panel ID.

        Args:
            panel_id: Panel ID string.
        """

    @abc.abstractmethod
    def publish_system_info(self, flags: list) -> None:
        """Publish system info/status flags.

        Args:
            flags: List of status flag strings.
        """

    @abc.abstractmethod
    def publish_system_datetime(self) -> None:
        """Publish system date/time."""

    @abc.abstractmethod
    def publish_system_avail(self, status: str) -> None:
        """Publish system availability.

        Args:
            status: One of 'online', 'offline'.
            'initializing' is planned for future implementation.
        """

    @abc.abstractmethod
    def publish_all(self) -> None:
        """Publish all current state. Full sync."""

    def publish(self, topic: str, msg: str, qos: int = 1, retain: bool = True) -> None:
        """Publish a raw MQTT message.

        Default is a no-op; backends that support raw publishing override this.

        Args:
            topic: MQTT topic.
            msg: Message payload.
            qos: Quality of service (0, 1, or 2).
            retain: Whether to retain the message.
        """
        pass


class CompositeMessaging:
    """Wraps multiple messaging backends; dispatches to all."""

    def __init__(self):
        self._backends = []

    def add_backend(self, backend: Messaging) -> None:
        """Add a backend to the composite."""
        self._backends.append(backend)

    def _dispatch(self, method_name: str, *args, **kwargs) -> None:
        """Call a method on all backends, logging errors but continuing."""
        for backend in self._backends:
            try:
                method = getattr(backend, method_name)
                method(*args, **kwargs)
            except Exception:
                LOG.exception('Error calling %s on backend %s',
                              method_name, backend.__class__.__name__)

    def publish_zone_state(self, zone_number: int, state: str) -> None:
        self._dispatch('publish_zone_state', zone_number, state)

    def publish_zone_bypassed(self, zone_number: int, state: str) -> None:
        self._dispatch('publish_zone_bypassed', zone_number, state)

    def publish_zone_type_flags(self, zone_number: int, flags: list) -> None:
        self._dispatch('publish_zone_type_flags', zone_number, flags)

    def publish_zone_condition_flags(self, zone_number: int, flags: list) -> None:
        self._dispatch('publish_zone_condition_flags', zone_number, flags)

    def publish_zone_name(self, zone_number: int, name: str) -> None:
        self._dispatch('publish_zone_name', zone_number, name)

    def publish_partition_state(self, partition_number: int, state: str) -> None:
        self._dispatch('publish_partition_state', partition_number, state)

    def publish_partition_condition_flags(self, partition_number: int, flags: list) -> None:
        self._dispatch('publish_partition_condition_flags', partition_number, flags)

    def publish_system_id(self, panel_id: str) -> None:
        self._dispatch('publish_system_id', panel_id)

    def publish_system_info(self, flags: list) -> None:
        self._dispatch('publish_system_info', flags)

    def publish_system_datetime(self) -> None:
        self._dispatch('publish_system_datetime')

    def publish_system_avail(self, status: str) -> None:
        self._dispatch('publish_system_avail', status)

    def publish_all(self) -> None:
        self._dispatch('publish_all')
