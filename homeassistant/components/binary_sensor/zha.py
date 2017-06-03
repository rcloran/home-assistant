"""
Binary sensors on Zigbee Home Automation networks.

For more details on this platform, please refer to the documentation
at https://home-assistant.io/components/binary_sensor.zha/
"""
import asyncio
import logging

from homeassistant.components.binary_sensor import DOMAIN, BinarySensorDevice
from homeassistant.components import zha

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = ['zha']

# ZigBee Cluster Library Zone Type to Home Assistant device class
CLASS_MAPPING = {
    0x000d: 'motion',
    0x0015: 'opening',
    0x0028: 'smoke',
    0x002a: 'moisture',
    0x002b: 'gas',
    0x002d: 'vibration',
}


@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the Zigbee Home Automation binary sensors."""
    _LOGGER.debug(">> async_setup_platform")
    discovery_info = zha.get_discovery_info(hass, discovery_info)
    if discovery_info is None:
        return

    from bellows.zigbee.zcl.clusters.security import IasZone
    if IasZone.cluster_id in discovery_info['in_clusters']:
        yield from _async_setup_iaszone(hass, config, async_add_devices, discovery_info)
        return

    from bellows.zigbee.zcl.clusters.general import OnOff 
    _LOGGER.debug("Maybe setting up remote. out_clusters=%s", discovery_info['out_clusters'])
    _LOGGER.debug("OnOff.cluster_id = %s", OnOff.cluster_id)
    if OnOff.cluster_id in discovery_info['out_clusters']:
        yield from _async_setup_remote(hass, config, async_add_devices, discovery_info)
        return


@asyncio.coroutine
def _async_setup_iaszone(hass, config, async_add_devices, discovery_info):
    from bellows.zigbee.zcl.clusters.security import IasZone
    device_class = None
    in_clusters = discovery_info['in_clusters']

    cluster = in_clusters[IasZone.cluster_id]
    if discovery_info['new_join']:
        yield from cluster.bind()
        ieee = cluster.endpoint.device.application.ieee
        yield from cluster.write_attributes({'cie_addr': ieee})

    try:
        zone_type = yield from cluster['zone_type']
        device_class = CLASS_MAPPING.get(zone_type, None)
    except Exception:  # pylint: disable=broad-except
        # If we fail to read from the device, use a non-specific class
        pass

    sensor = BinarySensor(device_class, **discovery_info)
    async_add_devices([sensor])


class BinarySensor(zha.Entity, BinarySensorDevice):
    """THe ZHA Binary Sensor."""

    _domain = DOMAIN

    def __init__(self, device_class, **kwargs):
        """Initialize the ZHA binary sensor."""
        super().__init__(**kwargs)
        self._device_class = device_class
        from bellows.zigbee.zcl.clusters.security import IasZone
        self._ias_zone_cluster = self._in_clusters[IasZone.cluster_id]

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        if self._state == 'unknown':
            return False
        return bool(self._state)

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return self._device_class

    def cluster_command(self, aps_frame, tsn, command_id, args):
        """Handle commands received to this cluster."""
        if command_id == 0:
            self._state = args[0] & 3
            _LOGGER.debug("Updated alarm state: %s", self._state)
            self.schedule_update_ha_state()
        elif command_id == 1:
            _LOGGER.debug("Enroll requested")
            self.hass.add_job(self._ias_zone_cluster.enroll_response(0, 0))


@asyncio.coroutine
def _async_setup_remote(hass, config, async_add_devices, discovery_info):
    _LOGGER.debug(">> async_setup_remote")
    from bellows.zigbee.zcl.clusters import general as zcl_general

    out_clusters = discovery_info['out_clusters']

    @asyncio.coroutine
    def maybe_bind(cluster_class):
        if cluster_class.cluster_id not in out_clusters:
            _LOGGER.debug("Not binding not-present cluster %s", cluster_class.name)
            return

        cluster = out_clusters[cluster_class.cluster_id]
        v = yield from cluster.bind()
        _LOGGER.debug("Bind result (%s): %s", cluster_class.name, v)

    if discovery_info['new_join']:
        yield from maybe_bind(zcl_general.OnOff)
        yield from maybe_bind(zcl_general.LevelControl)

    sensor = OnOffRemote(**discovery_info)
    _LOGGER.debug("Adding sensor %s", sensor)
    async_add_devices([sensor])


class OnOffRemote(zha.Entity, BinarySensorDevice):
    """ZHA On/Off remote"""
    _domain = DOMAIN
    def __init__(self, **kwargs):
        from bellows.zigbee.zcl.clusters import general as zcl_general
        self._out_listeners = {
            zcl_general.OnOff.cluster_id: OnOffListener(self),
            zcl_general.LevelControl.cluster_id: LevelListener(self),
        }
        super().__init__(**kwargs)
        self._state = False
        self._level = 0

    @property
    def is_on(self) -> bool:
        return self._state

    @property
    def device_state_attributes(self):
        return {"level": self._state and self._level or 0}

class OnOffListener:
    def __init__(self, entity):
        self.entity = entity

    def cluster_command(self, aps_frame, tsn, command_id, args):
        """Handle commands received to this cluster."""
        if command_id in (0x0000, 0x0040):
            self.entity._state = False
        elif command_id in (0x0001, 0x0041, 0x0042):
            self.entity._state = True
        elif command_id == 0x0002:
            self.entity._state = not self._state

        self.entity.schedule_update_ha_state()


class LevelListener:
    def __init__(self, entity):
        self.entity = entity

    def cluster_command(self, aps_frame, tsn, command_id, args):
        if command_id == 0x0002:
            if args[0] == 0:
                if not self.entity._state:
                    self.entity._state = True
                    self.entity._level = 0
                self.entity._level += args[1]
            else:
                self.entity._level -= args[1]
            self.entity._level = min(255, max(0, self.entity._level))
        self.entity.schedule_update_ha_state()

