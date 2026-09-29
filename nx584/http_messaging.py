"""HTTP messaging backend — all methods are no-ops.

HTTP is pull-based: clients poll the Flask API via GET requests.
No publish mechanism is needed; the Flask API serves state directly.
"""

import logging

from nx584.messaging import Messaging

LOG = logging.getLogger('nx584.http_messaging')


class HttpMessaging(Messaging):
    """HTTP messaging backend — all methods are no-ops.

    HTTP clients retrieve state by calling GET /command?cmd=status
    or GET /zones, /partitions, etc.  No publish mechanism is needed.
    """

    def publish_zone_state(self, zone_number: int, state: str) -> None:
        pass

    def publish_zone_bypassed(self, zone_number: int, state: str) -> None:
        pass

    def publish_zone_type_flags(self, zone_number: int, flags: list) -> None:
        pass

    def publish_zone_condition_flags(self, zone_number: int, flags: list) -> None:
        pass

    def publish_zone_name(self, zone_number: int, name: str) -> None:
        pass

    def publish_partition_state(self, partition_number: int, state: str) -> None:
        pass

    def publish_partition_condition_flags(self, partition_number: int, flags: list) -> None:
        pass

    def publish_system_id(self, panel_id: str) -> None:
        pass

    def publish_system_info(self, flags: list) -> None:
        pass

    def publish_system_datetime(self) -> None:
        pass

    def publish_system_avail(self, status: str) -> None:
        pass

    def publish_all(self) -> None:
        """No-op: HTTP is pull-based, no publish mechanism needed."""
        pass
