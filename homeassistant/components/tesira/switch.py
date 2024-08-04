import logging

import voluptuous as vol

from homeassistant.components.switch import SwitchEntity, PLATFORM_SCHEMA
from homeassistant.components.tesira import get_tesira
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.const import CONF_IP_ADDRESS, CONF_USERNAME, CONF_PASSWORD, CONF_NAME
import homeassistant.helpers.config_validation as cv
from .tesira import Tesira, CommandFailedException

_LOGGER = logging.getLogger(__name__)
DOMAIN = "tesira"
CONF_MUTES = "mutes"

quote = '"'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_IP_ADDRESS): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_MUTES): vol.All(
            cv.ensure_list,
            [cv.string],
        ),
    }
)


async def async_setup_platform(
    hass: HomeAssistant, config: ConfigType, async_add_entities, discovery_info=None
):
    if config == {}:
        return

    _LOGGER.debug("Switch: %s", config)
    t = await get_tesira(
        hass, config[CONF_IP_ADDRESS], config[CONF_USERNAME], config[CONF_PASSWORD]
    )
    serial = await t.serial_number()
    for instance_id in config[CONF_MUTES]:
        try:
            input_map = await t.inputs(instance_id, "numChannels", "label")
            for input_name, input_number in input_map.items():
                async_add_entities(
                    [
                        await TesiraMute.new(
                            t, instance_id, serial, input_number, input_name
                        )
                    ]
                )
        except CommandFailedException as e:
            _LOGGER.error("Error initializing mute control %s: %s", instance_id, str(e))
            continue


class TesiraMute(SwitchEntity):
    def __init__(
        self, tesira: Tesira, instance_id, serial_number, input_number, input_name
    ):
        self._tesira = tesira
        self._serial = serial_number
        self._instance_id = instance_id
        self._input_number = input_number
        self._attr_name = instance_id.split("-", 1)[1] + " - " + input_name
        self._attr_unique_id = f"{serial_number}_{instance_id}_{input_number}"

    @classmethod
    async def new(
        cls, tesira: Tesira, instance_id, serial_number, input_number, input_name
    ):
        mute = cls(tesira, instance_id, serial_number, input_number, input_name)
        await tesira.subscribe(instance_id, f"mute {input_number}", mute._mute_callback)
        return mute

    def try_write_state(self):
        if self.hass:
            self.async_write_ha_state()

    def _mute_callback(self, value):
        self._attr_is_on = value != "true"
        self.try_write_state()

    async def async_turn_on(self, **kwargs):
        """Turn input on."""
        await self._tesira._send_command(
            f'"{self._instance_id}" set mute {self._input_number} false'
        )

    async def async_turn_off(self, **kwargs):
        """Turn input off."""
        await self._tesira._send_command(
            f'"{self._instance_id}" set mute {self._input_number} true'
        )
