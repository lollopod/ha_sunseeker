"""
A integration that allows you to get information about next departure from specified stop.
For more details about this component, please refer to the documentation at
https://github.com/lollopod/ha-sunseeker
"""
from datetime import timedelta
import logging

import async_timeout
from pyowm.owm import OWM
from pyowm.config import DEFAULT_CONFIG as OWM_DEFAULT_CONFIG
import voluptuous as vol

from .const import DIREKT_BAHN_BASE_URL

# from homeassistant.helpers.entity import Entity
from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.core import callback
from homeassistant.components.openweathermap.weather_update_coordinator import (
    WeatherUpdateCoordinator,
)

# from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

# CONF_DEPARTURE_STATION = "departure_station"
CONF_DEPARTURE_STATION_ID = "departure_station_id"
CONF_OWM_API_KEY = "owm_api_key"
CONF_OWM_DECISION_PARAMETER = "owm_decision_parameter"
CONF_OWM_DECISION_THRESHOLD = "owm_decision_threshold"
CONF_OWM_FORECAST = "owm_forecast"
CONF_KLIMATICKET = "klimaticket"
CONF_DESTINATION_NUMBER = "number_of_destinations"


SCAN_INTERVAL = timedelta(hours=6)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        # vol.Required(CONF_DEPARTURE_STATION, default=None): cv.string,
        vol.Required(CONF_DEPARTURE_STATION_ID, default=None): cv.Number,
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

    station_id = config.get(CONF_DEPARTURE_STATION_ID)

    destination_number = config.get(CONF_DESTINATION_NUMBER)

    async_session = async_create_clientsession(hass)

    db_api = DBAPI(async_session, hass.loop, station_id)

    # if sunseeker wants to retreive the list of stations, DBAPI can be called, but it should be sufficient to call once or very rarely
    # so for the moment I put this in the initialization of sunseeker? Makes sense...

    sunseeker = weatherFinder(hass, db_api, owm_params)

    coordinator = WeatherCoordinator(hass, sunseeker, destination_number)

    devices = []

    for idx in range(4):
        devices.append(WeatherFinderSensor(coordinator, idx))
    add_devices_callback(devices, True)


class DBAPI:
    """Call API."""

    def __init__(self, session, loop, station_id):
        """Initialize."""
        self.loop = loop
        self.session = session

        self.data = {}

        self.url = DIREKT_BAHN_BASE_URL + "/" + str(station_id)
        self.stations_url = DIREKT_BAHN_BASE_URL + "/stations/" + str(station_id)

    async def async_fetch_data(self):
        """Get json from API endpoint."""
        value = None

        _LOGGER.debug("Inside fetch")

        # try:
        async with self.session.get(self.url) as resp:
            value = await resp.json()

        # except Exception:
        # pass

        return value

    async def fetch_station(self):
        """Get json from API endpoint."""
        value = None

        _LOGGER.debug("Inside fetch")

        try:
            async with self.session.get(self.stations_url) as resp:
                value = await resp.json()

        except Exception:
            pass

        return value


class weatherFinder:
    """Checks and finds good weather"""

    def __init__(self, hass, db_api: DBAPI, owm_params):
        """Initialize"""

        self.data = {}
        self.owm_params = owm_params

        owm_config = OWM_DEFAULT_CONFIG
        owm_config["connection"]["timeout_secs"] = 60

        self.owm = OWM(owm_params["api-key"], owm_config)
        self.owmMgr = self.owm.weather_manager()
        self.train_data = None
        self.db_api = db_api

        self.hass = hass

    # def _getWeatherAtStation(self, station):
    #     """Retreives weather info"""
    #     weather = None

    #     weather = self.owmMgr.one_call(
    #         lat=station["location"]["latitude"],
    #         lon=station["location"]["longitude"],
    #     )

    # return weather

    def checkWeatherAtStation(self, station):
        """Checks if weather matches target weather"""
        # weather = self._getWeatherAtStation(station)

        owm_data = []
        forecast = getattr(owm_data, self.owm_params["forecast"])
        status = getattr(forecast, self.owm_params["decison-parameter"])

        if status <= self.owm_params["decision-threshold"]:
            # parameter to be selected here, I think I will choose cloudiness
            return (1, forecast)  # sun has been found
        else:
            return (0, forecast)  # no sun

    async def async_checkWeatherAtStation(self, station):
        """Checks if weather matches target weather"""
        # weather = self._getWeatherAtStation(station)

        owm_coordinator = WeatherUpdateCoordinator(
            self.owmMgr,
            station["location"]["latitude"],
            station["location"]["longitude"],
            self.owm_params["forecast"],
            self.hass,
        )

        await owm_coordinator.async_config_entry_first_refresh()

        # forecast = getattr(owm_coordinator.data, self.owm_params["forecast"])
        # status = getattr(forecast, self.owm_params["decison-parameter"])

        # if status <= self.owm_params["decision-threshold"]:
        #     # parameter to be selected here, I think I will choose cloudiness
        #     return (1, forecast)  # sun has been found
        # else:
        #     return (0, forecast)  # no sun

    def find_sunny_stations(self, sunny_stations_number):
        """Checks if weather matches target weather"""
        sunny_stations = []

        # async with async_timeout.timeout(30):

        for station in self.train_data:
            (sunnyThere, forecast) = self.checkWeatherAtStation(station)
            if sunnyThere:
                station["forecast"] = forecast
                sunny_stations.append(station)
                if len(sunny_stations) > sunny_stations_number:
                    break
        return sunny_stations

    async def async_find_sunny_stations(self, sunny_stations_number):
        """Checks if weather matches target weather"""
        sunny_stations = []

        # async with async_timeout.timeout(30):

        for station in self.train_data:
            (sunnyThere, forecast) = await self.async_checkWeatherAtStation(station)
            if sunnyThere:
                station["forecast"] = forecast
                sunny_stations.append(station)
                if len(sunny_stations) > sunny_stations_number:
                    break
        return sunny_stations

    def checkStationCountry(self, station):
        """Checks if the station is is Austria"""
        # I can eventually check if the country is Austria (I could also do this upfront for all the stations)
        if station["country"] != "AT":
            return -1
        else:
            return 0

    async def update_train_data(self):
        # store internally all the stations
        async with async_timeout.timeout(20):
            self.train_data = await self.db_api.async_fetch_data()

        return 1


class WeatherCoordinator(DataUpdateCoordinator):
    """My custom coordinator."""

    def __init__(self, hass, sunseeker: weatherFinder, destination_number):
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name="SunSeeker",
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(seconds=30),
        )
        self.sunseeker = sunseeker
        self.destination_number = destination_number

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        sunny_stations = []
        # try:

        await self.sunseeker.update_train_data()

        # sunny_stations = self.sunseeker.find_sunny_stations(self.destination_number)
        # sunny_stations = await self.hass.async_add_executor_job(
        #     self.sunseeker.find_sunny_stations(self.destination_number)
        # )

        sunny_stations = await self.sunseeker.async_find_sunny_stations(
            self.destination_number
        )
        # except:
        #    raise UpdateFailed
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
        # self.icon = "hass:weather-sunny"
        # self.config = config

        self._attr_unique_id = "sunseeker_place_" + str(idx)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        data = self.coordinator.data

        self._state = data[self.idx]["name"]

        self.attributes = {
            "forecast": data[self.idx]["forecast"],
        }

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
