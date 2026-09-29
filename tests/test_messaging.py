"""Tests for messaging abstraction layer (CompositeMessaging, HttpMessaging)."""

import unittest
from unittest.mock import MagicMock, patch

from nx584.messaging import CompositeMessaging, Messaging
from nx584.http_messaging import HttpMessaging


class MockMessagingBackend(Messaging):
    """Mock backend that records all method calls."""

    def __init__(self):
        self.calls = []

    def _record(self, method_name, *args, **kwargs):
        self.calls.append((method_name, args, kwargs))

    def publish_zone_state(self, zone_number, state):
        self._record('publish_zone_state', zone_number, state)

    def publish_zone_bypassed(self, zone_number, state):
        self._record('publish_zone_bypassed', zone_number, state)

    def publish_zone_type_flags(self, zone_number, flags):
        self._record('publish_zone_type_flags', zone_number, flags)

    def publish_zone_condition_flags(self, zone_number, flags):
        self._record('publish_zone_condition_flags', zone_number, flags)

    def publish_zone_name(self, zone_number, name):
        self._record('publish_zone_name', zone_number, name)

    def publish_partition_state(self, partition_number, state):
        self._record('publish_partition_state', partition_number, state)

    def publish_partition_condition_flags(self, partition_number, flags):
        self._record('publish_partition_condition_flags', partition_number, flags)

    def publish_system_id(self, panel_id):
        self._record('publish_system_id', panel_id)

    def publish_system_info(self, flags):
        self._record('publish_system_info', flags)

    def publish_system_datetime(self):
        self._record('publish_system_datetime')

    def publish_system_avail(self, status):
        self._record('publish_system_avail', status)

    def publish_all(self):
        self._record('publish_all')


class TestCompositeMessaging(unittest.TestCase):
    """Tests for CompositeMessaging dispatch logic."""

    def test_composite_dispatches_to_all(self):
        """CompositeMessaging calls all backends."""
        backend1 = MockMessagingBackend()
        backend2 = MockMessagingBackend()
        composite = CompositeMessaging()
        composite.add_backend(backend1)
        composite.add_backend(backend2)

        composite.publish_zone_state(1, 'true')
        composite.publish_partition_state(2, 'disarmed')
        composite.publish_all()

        self.assertEqual(len(backend1.calls), 3)
        self.assertEqual(len(backend2.calls), 3)
        self.assertEqual(backend1.calls[0], backend2.calls[0])

    def test_composite_ignores_backend_exceptions(self):
        """One backend failure doesn't affect others."""
        good_backend = MockMessagingBackend()

        class BadBackend(Messaging):
            def publish_zone_state(self, zone_number, state):
                raise RuntimeError('simulated failure')

            def publish_zone_bypassed(self, zone_number, state):
                pass

            def publish_zone_type_flags(self, zone_number, flags):
                pass

            def publish_zone_condition_flags(self, zone_number, flags):
                pass

            def publish_zone_name(self, zone_number, name):
                pass

            def publish_partition_state(self, partition_number, state):
                pass

            def publish_partition_condition_flags(self, partition_number, flags):
                pass

            def publish_system_id(self, panel_id):
                pass

            def publish_system_info(self, flags):
                pass

            def publish_system_datetime(self):
                pass

            def publish_system_avail(self, status):
                pass

            def publish_all(self):
                pass

        bad_backend = BadBackend()
        composite = CompositeMessaging()
        composite.add_backend(bad_backend)
        composite.add_backend(good_backend)

        # Should not raise
        composite.publish_zone_state(1, 'true')
        composite.publish_partition_state(2, 'disarmed')
        composite.publish_all()

        # Good backend should have received all calls
        self.assertEqual(len(good_backend.calls), 3)


class TestHttpMessaging(unittest.TestCase):
    """Tests for HttpMessaging no-op methods."""

    def test_http_messaging_all_methods_succeed(self):
        """All HttpMessaging methods callable without error."""
        hm = HttpMessaging()
        hm.publish_zone_state(1, 'true')
        hm.publish_zone_bypassed(1, 'bypassed')
        hm.publish_zone_type_flags(1, ['Fire'])
        hm.publish_zone_condition_flags(1, ['Faulted'])
        hm.publish_zone_name(1, 'Test Zone')
        hm.publish_partition_state(1, 'disarmed')
        hm.publish_partition_condition_flags(1, ['Armed'])
        hm.publish_system_id('12345')
        hm.publish_system_info(['AC fail'])
        hm.publish_system_datetime()
        hm.publish_system_avail('online')
        hm.publish_all()


if __name__ == '__main__':
    unittest.main()
