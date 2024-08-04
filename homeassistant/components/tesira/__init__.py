"""The Tesira control component."""

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
import asyncio
from homeassistant.components.tesira.tesira import Tesira
from homeassistant.exceptions import PlatformNotReady
from homeassistant.const import CONF_IP_ADDRESS, CONF_NAME, CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant

DOMAIN = "tesira"
CONF_ZONES = "zones"
CONF_MUTES = "mutes"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.All(
            cv.ensure_list,
            [
                vol.Schema(
                    {
                        vol.Required(CONF_IP_ADDRESS): cv.string,
                        vol.Required(CONF_USERNAME): cv.string,
                        vol.Required(CONF_PASSWORD): cv.string,
                        vol.Required(CONF_NAME): cv.string,
                        vol.Required(CONF_ZONES): vol.All(
                            cv.ensure_list,
                            [cv.string],
                        ),
                        vol.Optional(CONF_MUTES): vol.All(
                            cv.ensure_list,
                            [cv.string],
                        ),
                    }
                )
            ],
        )
    },
    extra=vol.ALLOW_EXTRA,  # Allow extra keys to be present in the configuration.
)


COMMON_CONFIGS = [CONF_IP_ADDRESS, CONF_USERNAME, CONF_PASSWORD, CONF_NAME]


async def async_setup(hass: HomeAssistant, config):
    """Set up entities from config."""
    hass.data[DOMAIN] = hass.data.get(DOMAIN, {})
    reformatted_config = {
        "media_player": [
            {
                "platform": DOMAIN,
                **{
                    k: v
                    for k, v in tesira_device.items()
                    if k in [*COMMON_CONFIGS, CONF_ZONES]
                },
            }
            for tesira_device in config[DOMAIN]
        ],
        "switch": [
            {
                "platform": DOMAIN,
                **{
                    k: v
                    for k, v in tesira_device.items()
                    if k in [*COMMON_CONFIGS, CONF_MUTES]
                },
            }
            for tesira_device in config[DOMAIN]
        ],
    }
    await hass.helpers.discovery.async_load_platform(
        "media_player", DOMAIN, {}, reformatted_config
    )
    await hass.helpers.discovery.async_load_platform(
        "switch", DOMAIN, {}, reformatted_config
    )
    return True


TESIRA_CREATION_LOCK = asyncio.Lock()


class AlreadyConstructedException(Exception):
    def __init__(self, future):
        self.future = future
        super().__init__("Already constructed")


async def get_tesira(hass, ip, username, password) -> Tesira:
    # try and get tesira from hass or create new one
    try:
        async with TESIRA_CREATION_LOCK:
            if ip in hass.data[DOMAIN]:
                raise AlreadyConstructedException(hass.data[DOMAIN][ip])

            hass.data[DOMAIN][ip] = asyncio.create_task(
                Tesira.new(ip, username, password)
            )
    except AlreadyConstructedException as e:
        return await e.future
    try:
        return await hass.data[DOMAIN][ip]
    except (TimeoutError, OSError) as e:
        async with TESIRA_CREATION_LOCK:
            hass.data[DOMAIN].pop(ip)
        raise PlatformNotReady(f"Unable to connect to Tesira: {str(e)}") from e
