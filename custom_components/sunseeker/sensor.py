"""
A integration that allows you to get information about next departure from specified stop.
For more details about this component, please refer to the documentation at
https://github.com/lollopod/ha-sunseeker
"""
from datetime import timedelta
import json
import logging

import async_timeout
from pyowm.owm import OWM
from requests.models import PreparedRequest
import voluptuous as vol

from config.custom_components.sunseeker.const import DIREKT_BAHN_BASE_URL

# from homeassistant.helpers.entity import Entity
from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.core import callback

# from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

CONF_DEPARTURE_STATION = "departure_station"
CONF_OWM_API_KEY = "owm_api_key"
CONF_OWM_DECISION_PARAMETER = "owm_decision_parameter"
CONF_OWM_DECISION_THRESHOLD = "owm_decision_threshold"
CONF_OWM_FORECAST = "owm_forecast"
CONF_KLIMATICKET = "klimaticket"
CONF_DESTINATION_NUMBER = "number_of_destinations"


SCAN_INTERVAL = timedelta(hours=6)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_DEPARTURE_STATION, default=None): cv.string,
        vol.Required(CONF_OWM_API_KEY, default=None): cv.string,
        vol.Optional(CONF_OWM_DECISION_PARAMETER, default="clouds"): cv.string,
        vol.Optional(CONF_OWM_DECISION_THRESHOLD, default=10): cv.Number,
        vol.Optional(CONF_OWM_FORECAST, default="current"): cv.string,
        vol.Optional(CONF_KLIMATICKET, default=False): cv.boolean,
        vol.Optional(CONF_DESTINATION_NUMBER, default=4): cv.Number,
    }
)


_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, add_devices_callback, discovery_info=None):
    """Setup."""

    owm_params = {
        "api-key": config.get(CONF_OWM_API_KEY),
        "decision-parameter": config.get(CONF_OWM_DECISION_PARAMETER),
        "decision-threshold": config.get(CONF_OWM_DECISION_THRESHOLD),
        "forecast": config.get(CONF_OWM_FORECAST),
    }

    station = config.get(CONF_DEPARTURE_STATION)

    api = DBAPI(async_create_clientsession(hass), hass.loop, station)
    sunseeker = weatherFinder(
        async_create_clientsession(hass), hass.loop, station, owm_params
    )
    coordinator = WeatherCoordinator(hass, api, sunseeker, config)

    # finder_coordinator = FinderCoordinator(hass, sunseeker, config)

    # data = await api.get_json()
    # _LOGGER.debug(len(data["journey"]))

    await coordinator.async_config_entry_first_refresh()

    if config.get(CONF_KLIMATICKET) and sunseeker.checkStationCountry(
        coordinator.dp_api.departure_station
    ):
        _LOGGER.error("Station outside Austria, but klimaticket option is active")

    devices = []

    for idx in range(4):
        devices.append(WeatherFinderSensor(coordinator, idx))
    add_devices_callback(devices, True)


class weatherFinder:
    """Checks and finds good weather"""

    def __init__(self, session, loop, station, owm_params):
        """Initialize"""
        self.dbgendpoint = DIREKT_BAHN_BASE_URL
        self.loop = loop
        self.session = session
        self.data = {}
        self.stationName = station
        self.owm_params = owm_params

        self.owm = OWM(owm_params["api-key"])
        self.owmMgr = self.owm.weather_manager()

    def getWeatherAtStation(self, station):
        """Retreives weather info"""
        return self.owmMgr.one_call(
            lat=station["location"]["latitude"], lon=station["location"]["longitude"]
        )

    def checkWeatherAtStation(self, station):
        """Checks if weather matches target weather"""
        Weather = self.getWeatherAtStation(station)

        forecast = getattr(Weather, self.owm_params["forecast"])
        status = getattr(forecast, self.owm_params["decison-parameter"])

        if status <= self.owm_params["decision-threshold"]:
            # parameter to be selected here, I think I will choose cloudiness
            return (1, forecast)  # sun has been found
        else:
            return (0, forecast)  # no sun

    def checkStationCountry(self, station):
        """Checks if the station is is Austria"""
        station = self.session.get(
            self.dbgendpoint + "/stations/" + station["id"]
        ).json()
        # I can eventually check if the country is Austria (I could also do this upfront for all the stations)
        if station["country"] != "AT":
            return -1
        else:
            return 0


class DBAPI:
    """Call API."""

    def __init__(self, session, loop, station):
        """Initialize."""
        self.params = {"query": station}
        self.loop = loop
        self.session = session

        self.data = {}

        station_req = PreparedRequest()
        station_req.prepare_url(DIREKT_BAHN_BASE_URL + "/stations", self.params)
        self.station_url = station_req.url

        self._attr_unique_id = self.station_url

        self.departure_station = self.get_dep_station()

        self.data_url = DIREKT_BAHN_BASE_URL + "/" + str(self.departure_station)

    async def fetch_data(self):
        """Get json from API endpoint."""
        value = None

        _LOGGER.debug("Inside fetch")

        try:
            async with async_timeout.timeout(10):

                value = await self.session.get(self.data_url)

        except Exception:
            pass

        return value

    async def get_dep_station(self):
        """Get json from API endpoint."""
        value = None

        try:
            async with async_timeout.timeout(10):

                response = await self.session.get(self.station_url)

                value = response[0]

        except Exception:
            pass

        return value


class WeatherCoordinator(DataUpdateCoordinator):
    """My custom coordinator."""

    def __init__(self, hass, db_api: DBAPI, sunseeker: weatherFinder, config):
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name="My sensor",
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(seconds=30),
        )
        self.dp_api = db_api
        self.sunseeker = sunseeker
        self.config = config

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        sunny_stations = []

        try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with async_timeout.timeout(1000):
                data = await self.dp_api.fetch_data()
                for station in data:
                    if self.config.get(
                        CONF_KLIMATICKET
                    ) and self.sunseeker.checkStationCountry(
                        self.dp_api.departure_station
                    ):
                        _LOGGER.debug("Station outside Austria, skipping it")
                        continue
                    (sunnyThere, forecast) = self.sunseeker.checkWeatherAtStation(
                        station
                    )
                    if sunnyThere:
                        station["forecast"] = forecast
                        sunny_stations.append(station)
                        if len(sunny_stations) > self.config.get(
                            CONF_DESTINATION_NUMBER
                        ):
                            break

        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}")
        return sunny_stations


class WeatherFinderSensor(CoordinatorEntity, SensorEntity):
    """OebbSensor."""

    def __init__(self, coordinator: WeatherCoordinator, idx):
        """Pass coordinator to CoordinatorEntity."""
        super().__init__(coordinator)
        self.idx = idx
        self._name = "sunseeker_place_" + str(idx)
        self._state = None
        self.attributes = {}
        self.icon = "mdi:sun"
        self.config = config

        self._attr_unique_id = "sunseeker_place_" + str(idx)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        data = self.coordinator.data

        self._state = data[self.idx]["name"]

        self.attributes = {
            "forecast": data[self.idx]["forecast"],
        }

        # self._name = self.attributes["startTime"]

        # now = datetime.now()
        # date_string = now.strftime("%d/%m/%Y")
        # timestamp_string = date_string + " " + self.attributes["startTime"]

        # self._state = datetime.strptime(timestamp_string, "%d/%m/%Y %H:%M")

        self.async_write_ha_state()

    @property
    def name(self):
        """Return name."""
        return self._name

    @property
    def state(self):
        """Return state."""
        return self._state

    @property
    def icon(self):
        """Return icon."""
        return "mdi:tram"

    @property
    def extra_state_attributes(self):
        """Return attributes."""
        return self.attributes

    @property
    def device_class(self):
        """Return device_class."""
        return "timestamp"


class WeatherFinderHelperSensor(CoordinatorEntity, SensorEntity):
    """OebbSensor."""

    def __init__(self, coordinator: WeatherCoordinator, idx, evaId):
        """Pass coordinator to CoordinatorEntity."""
        super().__init__(coordinator)
        self.idx = idx
        self.formatted_idx = f"{self.idx:02}"
        self._name = "oebb_journey_helper_" + str(idx)
        self._state = None

        self._attr_unique_id = str(evaId) + "_helper_" + str(idx)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        data = self.coordinator.data

        self._state = data["journey"][self.idx]["ti"]

        self.async_write_ha_state()

    @property
    def name(self):
        """Return name."""
        return self._name

    @property
    def state(self):
        """Return state."""
        return self._state

    @property
    def icon(self):
        """Return icon."""
        return "mdi:tram"
