from uuid import uuid4
from typing import List, Optional
from functools import wraps
from time import sleep
import json

from requests import Session, codes, post
import time
import brotli

from .types import (
    ChargePointAccount,
    ElectricVehicle,
    HomeChargerStatus,
    HomeChargerStatusV2,
    HomeChargerTechnicalInfo,
    UserChargingStatus,
)
from .exceptions import (
    ChargePointLoginError,
    ChargePointCommunicationException,
    ChargePointBaseException,
    ChargePointInvalidSession,
)
from .global_config import ChargePointGlobalConfiguration
from .session import ChargingSession
from .constants import _LOGGER, DISCOVERY_API
from .token_cache import TokenCache


def _dict_for_query(device_data: dict) -> dict:
    """
    GET requests send device data as a nested object.
    To avoid storing the device data block in two
    formats, we are just going to compute the flat
    dictionary.
    """
    return {f"deviceData[{key}]": value for key, value in device_data.items()}


def _require_login(func):
    @wraps(func)
    def check_login(*args, **kwargs):
        self = args[0]
        if not self._logged_in:
            raise RuntimeError("Must login to use ChargePoint API")
        try:
            return func(*args, **kwargs)
        except ChargePointCommunicationException as exc:
            if exc.response.status_code == codes.unauthorized:
                raise ChargePointInvalidSession(
                    exc.response, "Session token has expired. Please login again!"
                ) from exc
            else:
                raise

    return check_login


class ChargePoint:
    def __init__(
        self,
        username: str,
        password: str,
        session_token: str = "",
        app_version: str = "6.18.0",
        use_token_cache: bool = True,
        cache_dir: Optional[str] = None,
    ):
        self._session = Session()
        self._app_version = app_version
        self._username = username
        self._use_token_cache = use_token_cache
        self._token_cache = TokenCache(cache_dir) if use_token_cache else None
        
        # Try to load cached device data first
        cached_device_data = None
        if self._use_token_cache and self._token_cache:
            cached_device_data = self._token_cache.load_device_data()
        
        # Create device data with cached UDID or generate new one
        if cached_device_data and "udid" in cached_device_data:
            self._device_data = cached_device_data
            _LOGGER.debug("Loaded cached device data with UDID: %s", cached_device_data["udid"])
        else:
            self._device_data = {
                "appId": "com.coulomb.ChargePoint",
                "manufacturer": "Apple",
                "model": "iPhone",
                "notificationId": "",
                "notificationIdType": "",
                "type": "IOS",
                "udid": str(uuid4()),
                "version": app_version,
            }
            # Cache the device data for future use
            if self._use_token_cache and self._token_cache:
                self._token_cache.save_device_data(self._device_data)
                _LOGGER.debug("Generated and cached new device data with UDID: %s", self._device_data["udid"])
        
        self._device_query_params = _dict_for_query(self._device_data)
        self._user_id = None
        self._logged_in = False
        self._session_token = None
        self._global_config = self._get_configuration(username)

        # Determine session token to use
        session_token_to_use = session_token
        
        # If no session token provided, try to load from cache
        if not session_token_to_use and self._use_token_cache:
            cached_token = self._token_cache.load_token(username)
            if cached_token:
                session_token_to_use = cached_token["session_token"]
                _LOGGER.debug("Loaded session token from cache for user: %s", username)

        # Try to use session token if available
        if session_token_to_use:
            self._set_session_token(session_token_to_use)
            self._logged_in = True
            try:
                account: ChargePointAccount = self.get_account()
                self._user_id = str(account.user.user_id)
                if not session_token:  # Only log if we used cached token
                    _LOGGER.info("Successfully loaded cached token for user: %s", username)
                return
            except ChargePointCommunicationException:
                _LOGGER.warning(
                    "Session token is expired, will attempt to re-login"
                )
                self._logged_in = False
                # Clear expired token from cache
                if self._use_token_cache and not session_token:
                    self._token_cache.clear_token(username)

        # Perform fresh login
        self.login(username, password)

    @property
    def user_id(self) -> Optional[str]:
        return self._user_id

    @property
    def session(self) -> Session:
        return self._session

    @property
    def session_token(self) -> Optional[str]:
        return self._session_token

    @property
    def device_data(self) -> dict:
        return self._device_data

    @property
    def global_config(self) -> ChargePointGlobalConfiguration:
        return self._global_config

    def login(self, username: str, password: str) -> None:
        """
        Create a session and login to ChargePoint
        :param username: Account username
        :param password: Account password
        """
        login_url = (
            f"{self._global_config.endpoints.accounts}v2/driver/profile/account/login"
        )
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
            "cp-region": "NA-US",
            "User-Agent": f"com.coulomb.ChargePoint/{self._app_version} CFNetwork/3826.600.31 Darwin/24.6.0",
            "Accept-Language": "en;q=1",
            "Accept-Encoding": "gzip, deflate, br"
        }
        # Create request matching mobile app structure with device data at both top level and nested
        request = {
            "password": password,
            "manufacturer": self._device_data["manufacturer"],
            "notificationId": self._device_data["notificationId"],
            "notificationIdType": self._device_data["notificationIdType"],
            "model": self._device_data["model"],
            "udid": self._device_data["udid"],
            "username": username,
            "version": self._device_data["version"],
            "type": self._device_data["type"],
            "deviceData": self._device_data,
        }
        _LOGGER.debug("Attempting client login with user: %s", username)
        _LOGGER.debug("Login URL: %s", login_url)
        _LOGGER.debug("Request headers: %s", headers)
        _LOGGER.debug("Request body: %s", request)
        
        # Set session headers to match the login request
        self._session.headers.update(headers)
        
        login = self._session.post(login_url, json=request, allow_redirects=False)
        _LOGGER.debug("Response URL: %s", login.url)
        _LOGGER.debug("Response cookies: %s", login.cookies.get_dict())
        _LOGGER.debug("Response headers: %s", login.headers)
        _LOGGER.debug("Response status: %s", login.status_code)
        _LOGGER.debug("Response content-type: %s", login.headers.get('content-type'))
        _LOGGER.debug("Response content-encoding: %s", login.headers.get('content-encoding'))

        # If we get a 403, report the failure and exit
        if login.status_code == 403:
            _LOGGER.error("Login failed with 403 status. Connection failed.")
            raise ChargePointLoginError(login, "Login failed with 403 status. Connection failed.")

        if login.status_code == codes.ok:
            try:
                req = login.json()
            except Exception as e:
                _LOGGER.error("Failed to parse JSON response: %s", e)
                _LOGGER.error("Raw response content: %s", login.content)
                
                # Try manual Brotli decompression if content is compressed
                if login.headers.get('content-encoding') == 'br':
                    try:
                        decompressed_content = brotli.decompress(login.content)
                        _LOGGER.debug("Manually decompressed Brotli content")
                        req = json.loads(decompressed_content.decode('utf-8'))
                    except Exception as brotli_error:
                        _LOGGER.error("Failed to manually decompress Brotli: %s", brotli_error)
                        raise ChargePointLoginError(login, f"Failed to parse response: {e}")
                else:
                    raise ChargePointLoginError(login, f"Failed to parse response: {e}")
            self._user_id = req["user"]["userId"]
            _LOGGER.debug("Authentication success! User ID: %s", self._user_id)
            self._set_session_token(req["sessionId"])
            self._logged_in = True
            
            # Save token to cache if enabled
            if self._use_token_cache and self._token_cache:
                self._token_cache.save_token(
                    username=username,
                    session_token=req["sessionId"],
                    user_id=str(self._user_id)
                )
            
            return

        _LOGGER.error(
            "Failed to get account information! status_code=%s err=%s",
            login.status_code,
            login.text,
        )
        raise ChargePointLoginError(login, "Failed to authenticate to ChargePoint!")

    def logout(self):
        response = self._session.post(
            f"{self._global_config.endpoints.accounts}v1/driver/profile/account/logout",
            json={"deviceData": self._device_data},
        )

        if response.status_code != codes.ok:
            raise ChargePointCommunicationException(
                response=response, message="Failed to log out!"
            )

        # Clear cached token if enabled
        if self._use_token_cache and self._token_cache:
            self._token_cache.clear_token(self._username)

        self._session.headers = {}
        self._session.cookies.clear_session_cookies()
        self._session_token = None
        self._logged_in = False

    def _get_configuration(self, username: str) -> ChargePointGlobalConfiguration:
        _LOGGER.debug("Discovering account region for username %s", username)
        request = {"deviceData": self._device_data, "username": username}
        response = self._session.post(DISCOVERY_API, json=request)
        if response.status_code != codes.ok:
            raise ChargePointCommunicationException(
                response=response,
                message="Failed to discover region for provided username!",
            )
        config = ChargePointGlobalConfiguration.from_json(response.json())
        _LOGGER.debug(
            "Discovered account region: %s / %s (%s)",
            config.region,
            config.default_country.name,
            config.default_country.code,
        )
        return config

    def _set_session_token(self, session_token: str):
        try:
            self._session.headers = {
                "cp-session-type": "CP_SESSION_TOKEN",
                "cp-session-token": session_token,
                # Data:       |------------------Token Data------------------||---?---||-Reg-|
                # Session ID: rAnDomBaSe64EnCodEdDaTaToKeNrAnDomBaSe64EnCodEdD#D???????#RNA-US
                "cp-region": session_token.split("#R")[1],
                "user-agent": "ChargePoint/236 (iPhone; iOS 15.3; Scale/3.00)",
            }
        except IndexError:
            raise ChargePointBaseException("Invalid session token format.")

        self._session_token = session_token
        self._session.cookies.set("coulomb_sess", session_token)

    @_require_login
    def get_account(self) -> ChargePointAccount:
        _LOGGER.debug("Getting ChargePoint Account Details")
        response = self._session.get(
            f"{self._global_config.endpoints.accounts}v1/driver/profile/user",
            params=self._device_query_params,
        )

        if response.status_code != codes.ok:
            _LOGGER.error(
                "Failed to get account information! status_code=%s err=%s",
                response.status_code,
                response.text,
            )
            raise ChargePointCommunicationException(
                response=response, message="Failed to get user information."
            )

        account = response.json()
        return ChargePointAccount.from_json(account)

    @_require_login
    def get_vehicles(self) -> List[ElectricVehicle]:
        _LOGGER.debug("Listing vehicles")
        response = self._session.get(
            f"{self._global_config.endpoints.accounts}v1/driver/vehicle",
            params=self._device_query_params,
        )

        if response.status_code != codes.ok:
            _LOGGER.error(
                "Failed to list vehicles! status_code=%s err=%s",
                response.status_code,
                response.text,
            )
            raise ChargePointCommunicationException(
                response=response, message="Failed to retrieve EVs."
            )

        evs = response.json()
        return [ElectricVehicle.from_json(ev) for ev in evs]

    @_require_login
    def get_home_chargers(self) -> List[int]:
        _LOGGER.debug("Searching for registered pandas")
        get_pandas = {"user_id": self.user_id, "get_pandas": {"mfhs": {}}}
        response = self._session.post(
            f"{self._global_config.endpoints.webservices}mobileapi/v5", json=get_pandas
        )

        if response.status_code != codes.ok:
            _LOGGER.error(
                "Failed to get home chargers! status_code=%s err=%s",
                response.status_code,
                response.text,
            )
            raise ChargePointCommunicationException(
                response=response, message="Failed to retrieve Home Flex chargers."
            )

        # {"get_pandas":{"device_ids":[12345678]}}
        pandas = response.json()["get_pandas"]["device_ids"]
        _LOGGER.debug(
            "Discovered %d connected pandas: %s",
            len(pandas),
            ",".join([str(p) for p in pandas]),
        )
        return pandas

    @_require_login
    def get_home_chargers_v2(self) -> List[dict]:
        """
        Get home chargers using the newer hcpo-charger-management API.
        Returns list of charger objects with id, label, protocolIdentifier, etc.
        """
        _LOGGER.debug("Searching for registered chargers using new API")
        response = self._session.get(
            f"{self._global_config.endpoints.hcpo_hcm}api/v1/configuration/users/{self.user_id}/chargers",
            params=self._device_query_params,
        )

        if response.status_code != codes.ok:
            _LOGGER.error(
                "Failed to get home chargers via new API! status_code=%s err=%s",
                response.status_code,
                response.text,
            )
            raise ChargePointCommunicationException(
                response=response, message="Failed to retrieve Home Flex chargers via new API."
            )

        result = response.json()
        chargers = result.get("data", [])
        _LOGGER.debug(
            "Discovered %d connected chargers via new API: %s",
            len(chargers),
            ",".join([str(c.get("id", "unknown")) for c in chargers]),
        )
        return chargers

    @_require_login
    def get_home_charger_status(self, charger_id: int) -> HomeChargerStatus:
        _LOGGER.debug("Getting status for panda: %s", charger_id)
        get_status = {
            "user_id": self.user_id,
            "get_panda_status": {"device_id": charger_id, "mfhs": {}},
        }
        response = self._session.post(
            f"{self._global_config.endpoints.webservices}mobileapi/v5", json=get_status
        )

        if response.status_code != codes.ok:
            _LOGGER.error(
                "Failed to determine home charger status! status_code=%s err=%s",
                response.status_code,
                response.text,
            )
            raise ChargePointCommunicationException(
                response=response, message="Failed to get home charger status."
            )

        status = response.json()

        _LOGGER.debug(status)

        return HomeChargerStatus.from_json(
            charger_id=charger_id, json=status["get_panda_status"]
        )

    @_require_login
    def get_home_charger_status_v2(self, charger_id: int) -> HomeChargerStatusV2:
        """
        Get home charger status using the newer hcpo-charger-management API.
        This matches the mobile app implementation.
        """
        _LOGGER.debug("Getting status for charger %s using new API", charger_id)
        response = self._session.get(
            f"{self._global_config.endpoints.hcpo_hcm}api/v1/configuration/users/{self.user_id}/chargers/{charger_id}/status",
            params=self._device_query_params,
        )

        if response.status_code != codes.ok:
            _LOGGER.error(
                "Failed to get home charger status via new API! status_code=%s err=%s",
                response.status_code,
                response.text,
            )
            raise ChargePointCommunicationException(
                response=response, message="Failed to get home charger status via new API."
            )

        status = response.json()
        _LOGGER.debug("New API status response: %s", status)

        return HomeChargerStatusV2.from_json(
            charger_id=charger_id, json=status
        )

    @_require_login
    def get_home_charger_technical_info(
        self, charger_id: int
    ) -> HomeChargerTechnicalInfo:
        _LOGGER.debug("Getting tech info for panda: %s", charger_id)
        get_tech_info = {
            "user_id": self.user_id,
            "get_station_technical_info": {"device_id": charger_id, "mfhs": {}},
        }

        response = self._session.post(
            f"{self._global_config.endpoints.webservices}mobileapi/v5",
            json=get_tech_info,
        )

        if response.status_code != codes.ok:
            _LOGGER.error(
                "Failed to determine home charger tech info! status_code=%s err=%s",
                response.status_code,
                response.text,
            )
            raise ChargePointCommunicationException(
                response=response, message="Failed to get home charger tech info."
            )

        status = response.json()

        _LOGGER.debug(status)

        return HomeChargerTechnicalInfo.from_json(
            json=status["get_station_technical_info"]
        )

    @_require_login
    def get_user_charging_status(self) -> Optional[UserChargingStatus]:
        _LOGGER.debug("Checking account charging status")
        request = {"deviceData": self._device_data, "user_status": {"mfhs": {}}}
        response = self._session.post(
            f"{self._global_config.endpoints.mapcache}v2", json=request
        )

        if response.status_code != codes.ok:
            _LOGGER.error(
                "Failed to get account charging status! status_code=%s err=%s",
                response.status_code,
                response.text,
            )
            raise ChargePointCommunicationException(
                response=response, message="Failed to get user charging status."
            )

        status = response.json()
        if not status["user_status"]:
            _LOGGER.debug("No user status returned, assuming not charging.")
            return None

        _LOGGER.debug("Raw status: %s", status)

        return UserChargingStatus.from_json(status["user_status"])

    @_require_login
    def set_amperage_limit(
        self, charger_id: int, amperage_limit: int, max_retry: int = 5
    ) -> None:
        _LOGGER.debug(f"Setting amperage limit for {charger_id} to {amperage_limit}")
        request = {
            "deviceData": self._device_data,
            "chargeAmperageLimit": amperage_limit,
        }
        response = self._session.post(
            f"{self._global_config.endpoints.internal_api}/driver/charger/{charger_id}/config/v1/charge-amperage-limit",
            json=request,
        )

        if response.status_code != codes.ok:
            _LOGGER.error(
                "Failed to set amperage limit! status_code=%s err=%s",
                response.status_code,
                response.text,
            )
            raise ChargePointCommunicationException(
                response=response, message="Failed to set amperage limit."
            )
        status = response.json()
        # The API can return 200 but still have a failure status.
        if status["status"] != "success":
            message = status.get("message", "empty message")
            _LOGGER.error(
                "Failed to set amperage limit! status=%s err=%s",
                status["status"],
                message,
            )
            raise ChargePointCommunicationException(
                response=response, message=f"Failed to set amperage limit: {message}"
            )

        # This is eventually consistent so we wait until the new limit is reflected.
        for _ in range(1, max_retry):  # pragma: no cover
            charger_status = self.get_home_charger_status(charger_id)
            if charger_status.amperage_limit == amperage_limit:
                return
            sleep(1)

        raise ChargePointCommunicationException(
            response=response,
            message="New amperage limit did not persist to charger after retries",
        )

    @_require_login
    def restart_home_charger(self, charger_id: int) -> None:
        _LOGGER.debug("Sending restart command for panda: %s", charger_id)
        restart = {
            "user_id": self.user_id,
            "restart_panda": {"device_id": charger_id, "mfhs": {}},
        }
        response = self._session.post(
            f"{self._global_config.endpoints.webservices}mobileapi/v5", json=restart
        )

        if response.status_code != codes.ok:
            _LOGGER.error(
                "Failed to restart charger! status_code=%s err=%s",
                response.status_code,
                response.text,
            )
            raise ChargePointCommunicationException(
                response=response, message="Failed to restart charger."
            )

        status = response.json()
        _LOGGER.debug(status)
        return

    @_require_login
    def get_charging_session(self, session_id: int, use_alternative_api: bool = False) -> ChargingSession:
        return ChargingSession(session_id=session_id, client=self, use_alternative_api=use_alternative_api)

    @_require_login
    def start_charging_session(
        self, device_id: int, max_retry: int = 30, use_alternative_api: bool = False
    ) -> ChargingSession:

        return ChargingSession.start(
            device_id=device_id, client=self, max_retry=max_retry, use_alternative_api=use_alternative_api
        )
    
    def clear_token_cache(self) -> None:
        """Clear the cached token for the current user."""
        if self._use_token_cache and self._token_cache:
            self._token_cache.clear_token(self._username)
            _LOGGER.info("Cleared token cache for user: %s", self._username)
    
    def clear_device_cache(self) -> None:
        """Clear the cached device data for the current platform."""
        if self._use_token_cache and self._token_cache:
            self._token_cache.clear_device_data()
            _LOGGER.info("Cleared device cache for platform")
    
    def clear_all_token_caches(self) -> None:
        """Clear all cached tokens."""
        if self._use_token_cache and self._token_cache:
            self._token_cache.clear_all_tokens()
            _LOGGER.info("Cleared all token caches")
    
    def clear_all_caches(self) -> None:
        """Clear all cached tokens and device data."""
        if self._use_token_cache and self._token_cache:
            self._token_cache.clear_all_caches()
            _LOGGER.info("Cleared all caches")
