import logging
import math

import voluptuous as vol

from homeassistant.components.media_player import (
    PLATFORM_SCHEMA,
    MediaPlayerEntity,
    MediaPlayerState,
)
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.const import CONF_IP_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .tesira import Tesira

DOMAIN = "tesira"

_LOGGER = logging.getLogger(__name__)


DEFAULT_PORT = 23

CONF_ZONES = "zones"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_IP_ADDRESS): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_ZONES): vol.All(
            cv.ensure_list,
            [cv.string],
        ),
    }
)


async def async_setup_platform(
    hass: HomeAssistant, config: ConfigType, async_add_entities, discovery_info=None
):
    """Set up the Tesira platform."""
    ip = config[CONF_IP_ADDRESS]
    name = config[CONF_NAME]
    source_selector_instance_ids = config[CONF_ZONES]
    _LOGGER.debug("Setting up Tesira platform")
    t = await Tesira.new(ip, "default")
    serial = await t.serial_number()
    _LOGGER.error("Serial number is " + str(serial))

    for instance_id in source_selector_instance_ids:
        source_map = await t.sources(instance_id)
        async_add_entities(
            [await TesiraSourceSelector.new(t, instance_id, serial, source_map)]
        )


class TesiraSourceSelector(MediaPlayerEntity):
    """Representation of a Tesira Source Selector."""

    _attr_supported_features = (
        MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.VOLUME_SET
    )
    _attr_should_poll = False

    def __init__(self, tesira: Tesira, instance_id, serial_number, source_map):
        """Initialize the Sky Remote."""
        self._tesira = tesira
        self._serial = serial_number
        self._instance_id = instance_id
        self._attr_unique_id = f"{serial_number}_{instance_id}"
        self._source_map = source_map
        self._attr_source_list = list(source_map.keys())
        self._attr_source = self._attr_source_list[0]

    @classmethod
    async def new(cls, tesira: Tesira, instance_id, serial_number, source_map):
        player = cls(tesira, instance_id, serial_number, source_map)
        await tesira.subscribe(instance_id, "outputLevel", player._volume_callback)
        await tesira.subscribe(instance_id, "outputMute", player._mute_callback)
        await tesira.subscribe(instance_id, "sourceSelection", player._source_callback)
        return player

    def try_write_state(self):
        if self.hass:
            self.async_write_ha_state()

    def _volume_callback(self, value):
        self._attr_volume_level = self.db_to_volume(float(value))
        self.try_write_state()

    def _mute_callback(self, value):
        self._attr_is_volume_muted = value == "true"
        print(f"'{value}'")
        self.try_write_state()

    def _source_callback(self, value):
        value = int(value)  # assuming value is a source ID
        for source, source_id in self._source_map.items():
            if source_id == value:
                self._attr_source = source
                break
        else:
            _LOGGER.error(f"Unknown source ID: {value}")
        self.try_write_state()

    # @property
    # def name(self) -> str:
    #     """Return the display name of the sky box remote."""
    #     return self._name

    @property
    def state(self):
        return MediaPlayerState.ON

    async def async_select_source(self, source):
        """Select input source."""
        source_id = self._source_map[source]
        await self._tesira.select_source(self._instance_id, source_id)
        self._attr_source = source
        self.async_write_ha_state()

    @staticmethod
    def volume_to_db(volume):
        return max(21 * (math.log(max(volume, 0.001), 4)), -100)

    @staticmethod
    def db_to_volume(db):
        return math.pow(2, ((2 * db) / 21))

    async def async_set_volume_level(self, volume):
        await self._tesira.set_volume(self._instance_id, self.volume_to_db(volume))
        self._attr_volume_level = volume
        self.async_write_ha_state()

    async def async_mute_volume(self, mute: bool) -> None:
        await self._tesira.set_mute(self._instance_id, mute)
        self._attr_is_volume_muted = mute
        self.async_write_ha_state()
