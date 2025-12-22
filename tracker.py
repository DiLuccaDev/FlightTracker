import time
import json
import configparser
import sys
import requests
import os
import re 
from haversine import haversine, Unit
from datetime import datetime, date, timedelta 
import logging
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))


# --- IMPORT LOCAL LED LIBRARY ---
try:
    import led_display
except ImportError:
    print("WARNING: led_display.py not found. LED output disabled.")

# --- LOGGING SETUP: Ensure the logger is configured correctly for self-containment ---
# Configure the logger to output messages to the console with a clear format.
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
LOGGER = logging.getLogger()
# -----------------------------------------------------------------------------------

# --- 0. BUDGET, USAGE, TIME CONFIGURATION ---
USAGE_FILE = "aero_usage.json"
NOW = datetime.now()
CURRENT_HOUR = NOW.hour

# Budget limits are now loaded from config.ini
MAX_AERO_API_MONTHLY_CALLS = 0 
MAX_AERO_API_DAILY_CALLS = 0 

# --- 1. CONFIGURATION LOADING FUNCTIONS ---

def load_credentials(file_path="credentials.json"):
    """Loads API credentials from a JSON file."""
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            client_id = data["client_id"]
            client_secret = data["client_secret"]
            aero_key = data["aero_key"]
            openweathermap_api_key = data["openweathermap_api_key"]
            return client_id, client_secret, aero_key, openweathermap_api_key
    except FileNotFoundError:
        LOGGER.critical(f"FATAL ERROR: {file_path} not found. Please create it with your credentials.")
        sys.exit(1)
    except json.JSONDecodeError:
        LOGGER.critical(f"FATAL ERROR: {file_path} is corrupted or empty. Check JSON format.")
        sys.exit(1)
    except KeyError as e:
        LOGGER.critical(f"FATAL ERROR: Missing required key in {file_path}: {e}. Ensure 'client_id', 'client_secret', 'aero_key', and 'openweathermap_api_key' are present.")
        sys.exit(1)


def load_settings(file_path="config.ini"):
    """
    Loads user settings from an INI file, including configurable cache lifetime
    and the new AeroAPI budget limits.
    """
    config = configparser.ConfigParser()
    try:
        config.read(file_path)
        
        # Load Location Settings
        HOME_LAT = config.getfloat('LOCATION', 'home_lat')
        HOME_LON = config.getfloat('LOCATION', 'home_lon')
        RANGE_KM = config.getint('LOCATION', 'range_km')
        
        # Burst Limit per Refresh Cycle
        MAX_ROUTE_LOOKUPS_PER_CYCLE = config.getint('LOCATION', 'max_route_lookups_per_cycle', fallback=5)
        
        # Cache Lifetime in Hours, converted to seconds
        ROUTE_CACHE_LIFETIME_SECONDS = config.getint('LOCATION', 'route_cache_lifetime_hours', fallback=24) * 3600 
        
        # --- BUDGET LIMITS ---
        MAX_AERO_API_HOURLY_CALLS = config.getint('BUDGET', 'max_aero_api_hourly_calls', fallback=10)
        MAX_AERO_API_DAILY_CALLS = config.getint('BUDGET', 'max_aero_api_daily_calls', fallback=150)
        MAX_AERO_API_MONTHLY_CALLS = config.getint('BUDGET', 'max_aero_api_monthly_calls', fallback=4500)
        
        # Operational Window Settings
        START_HOUR = config.getint('OPERATIONAL_WINDOW', 'start_hour', fallback=8)  # 8 AM (0-23)
        END_HOUR = config.getint('OPERATIONAL_WINDOW', 'end_hour', fallback=20)    # 8 PM (0-23)
        
        # Load and Calculate Display Settings
        BRIGHTNESS = config.getint('DISPLAY', 'brightness')
        rows = config.getint('DISPLAY', 'matrix_rows')
        cols = config.getint('DISPLAY', 'matrix_cols')
        REFRESH_TIME = config.getint('DISPLAY', 'refresh_time')
        TIME_FORMAT = config.get('DISPLAY', 'time_format', fallback='24H')
        
        # Calculate the total cascaded modules and blocks tuple
        CASCADED = rows * cols 
        BLOCKS = (rows, cols)
        
        return (HOME_LAT, HOME_LON, RANGE_KM, CASCADED, BRIGHTNESS, BLOCKS, 
                REFRESH_TIME, TIME_FORMAT, MAX_ROUTE_LOOKUPS_PER_CYCLE, 
                START_HOUR, END_HOUR, ROUTE_CACHE_LIFETIME_SECONDS,
                MAX_AERO_API_HOURLY_CALLS, 
                MAX_AERO_API_DAILY_CALLS, MAX_AERO_API_MONTHLY_CALLS)
        
    except FileNotFoundError:
        LOGGER.critical(f"FATAL ERROR: {file_path} not found. Ensure config.ini is present.")
        sys.exit(1)
    except configparser.Error as e:
        LOGGER.critical(f"FATAL ERROR: config.ini structure is invalid or missing values. Details: {e}")
        sys.exit(1)


def load_airport_mapping(file_path="airport_data.json"):
    """Loads the comprehensive ICAO to IATA airport code mapping from a JSON file."""
    try:
        with open(file_path, 'r') as f:
            mapping = json.load(f)
            # Convert keys to uppercase for robust lookup
            return {k.upper(): v for k, v in mapping.items()}
    except FileNotFoundError:
        LOGGER.warning(f"AIRPORT WARNING: {file_path} not found. Airport code translation will be limited.")
        return {}
    except json.JSONDecodeError:
        LOGGER.critical(f"FATAL ERROR: {file_path} is corrupted. Cannot load airport data.")
        sys.exit(1)


# --- 2. GLOBAL STATE AND USAGE MANAGEMENT ---

# Load configuration and credentials at module level
CLIENT_ID, CLIENT_SECRET, AERO_KEY, OPENWEATHERMAP_API_KEY = load_credentials()
(HOME_LAT, HOME_LON, RANGE_KM, CASCADED, BRIGHTNESS, BLOCKS, REFRESH_TIME, 
 TIME_FORMAT, MAX_ROUTE_LOOKUPS_PER_CYCLE, START_HOUR, END_HOUR, 
 ROUTE_CACHE_LIFETIME_SECONDS, MAX_AERO_API_HOURLY_CALLS,
 MAX_AERO_API_DAILY_CALLS, MAX_AERO_API_MONTHLY_CALLS) = load_settings()

# Load the comprehensive airport mapping
ICAO_TO_IATA_MAPPING = load_airport_mapping()

# Globals for OpenSky OAuth Token Management
OAUTH_TOKEN = None
TOKEN_EXPIRY_TIME = 0 

# --- CACHING GLOBALS ---
FLIGHT_ROUTE_CACHE = {} 
ROUTE_LOOKUP_COUNTER = 0 

# --- AEROAPI USAGE TRACKING GLOBALS (Daily, Hourly, Monthly) ---
AERO_CALLS_TODAY = 0
AERO_LAST_RESET_DATE = "" 

AERO_CALLS_THIS_HOUR = 0
AERO_LAST_RESET_HOUR = -1 

AERO_CALLS_THIS_MONTH = 0
AERO_LAST_RESET_MONTH = "" # YYYY-MM format


def load_aero_usage():
    """Loads the persistent daily, hourly, and monthly call counts from a local file."""
    global AERO_CALLS_TODAY, AERO_LAST_RESET_DATE
    global AERO_CALLS_THIS_HOUR, AERO_LAST_RESET_HOUR
    global AERO_CALLS_THIS_MONTH, AERO_LAST_RESET_MONTH

    now = datetime.now()
    today_date_str = now.strftime("%Y-%m-%d")
    current_hour = now.hour
    current_month_str = now.strftime("%Y-%m")

    is_reset_needed = False
    
    try:
        if os.path.exists(USAGE_FILE):
            with open(USAGE_FILE, 'r') as f:
                data = json.load(f)
                
                # --- MONTHLY RESET LOGIC (Highest priority reset) ---
                saved_month = data.get("month", "")
                if saved_month == current_month_str:
                    AERO_CALLS_THIS_MONTH = data.get("monthly_count", 0)
                    AERO_LAST_RESET_MONTH = saved_month
                else:
                    AERO_CALLS_THIS_MONTH = 0
                    AERO_LAST_RESET_MONTH = current_month_str
                    is_reset_needed = True 
                
                # --- DAILY RESET LOGIC ---
                saved_date = data.get("date", "")
                if saved_date == today_date_str and not is_reset_needed:
                    AERO_CALLS_TODAY = data.get("count", 0)
                    AERO_LAST_RESET_DATE = saved_date
                else:
                    AERO_CALLS_TODAY = 0
                    AERO_LAST_RESET_DATE = today_date_str
                    is_reset_needed = True
                
                # --- HOURLY RESET LOGIC ---
                saved_hourly_count = data.get("hourly_count", 0)
                saved_hour = data.get("hour", -1)
                
                if saved_date == today_date_str and saved_hour == current_hour and not is_reset_needed:
                    AERO_CALLS_THIS_HOUR = saved_hourly_count
                    AERO_LAST_RESET_HOUR = saved_hour
                else:
                    AERO_CALLS_THIS_HOUR = 0
                    AERO_LAST_RESET_HOUR = current_hour
                    is_reset_needed = True

                if is_reset_needed:
                    save_aero_usage()
        
        else:
            # File doesn't exist, initialize and save
            AERO_CALLS_THIS_MONTH = 0
            AERO_LAST_RESET_MONTH = current_month_str
            AERO_CALLS_TODAY = 0
            AERO_LAST_RESET_DATE = today_date_str
            AERO_CALLS_THIS_HOUR = 0
            AERO_LAST_RESET_HOUR = current_hour
            save_aero_usage()
            
    except Exception as e:
        LOGGER.warning(f"Could not load or parse usage file {USAGE_FILE}. Resetting counts to 0. {e}")
        # Reset all counters on load failure
        AERO_CALLS_THIS_MONTH = 0
        AERO_LAST_RESET_MONTH = current_month_str
        AERO_CALLS_TODAY = 0
        AERO_LAST_RESET_DATE = today_date_str
        AERO_CALLS_THIS_HOUR = 0
        AERO_LAST_RESET_HOUR = current_hour
        save_aero_usage()

    # Log the current usage status every time it's loaded/checked
    LOGGER.info(
        f"Usage Status: Monthly={AERO_CALLS_THIS_MONTH}/{MAX_AERO_API_MONTHLY_CALLS}, "
        f"Daily={AERO_CALLS_TODAY}/{MAX_AERO_API_DAILY_CALLS}, "
        f"Hourly={AERO_CALLS_THIS_HOUR}/{MAX_AERO_API_HOURLY_CALLS}"
    )


def save_aero_usage():
    """Saves the current daily, hourly, and monthly call counts to a local file."""
    global AERO_CALLS_TODAY, AERO_LAST_RESET_DATE
    global AERO_CALLS_THIS_HOUR, AERO_LAST_RESET_HOUR
    global AERO_CALLS_THIS_MONTH, AERO_LAST_RESET_MONTH
    try:
        with open(USAGE_FILE, 'w') as f:
            json.dump({
                "month": AERO_LAST_RESET_MONTH,
                "monthly_count": AERO_CALLS_THIS_MONTH,
                "date": AERO_LAST_RESET_DATE,
                "count": AERO_CALLS_TODAY,
                "hourly_count": AERO_CALLS_THIS_HOUR,
                "hour": AERO_LAST_RESET_HOUR
            }, f)
    except Exception as e:
        LOGGER.error(f"Could not save usage file {USAGE_FILE}. {e}")


# Initialize usage count at startup
load_aero_usage()


# --- 3. ALERTING FUNCTION (Logging Only) ---

def log_critical_alert(subject: str, body: str):
    """
    Logs a critical alert message to the console. 
    This replaces the email alert functionality.
    """
    LOGGER.critical(f"[ALERT] {subject}: {body}")

# --- 4. HELPER FUNCTIONS ---

# Regex to match standard commercial flight numbers: 
COMMERCIAL_CALLSIGN_PATTERN = re.compile(r'^[A-Z]{3}\d{1,4}[A-Z]?$')

# List of ICAO prefixes for known military, cargo, or private/charter operators
NON_COMMERCIAL_PREFIXES = {
    'RCH', 'CNV', 'EJM', 'LXJ', 'FDX', 'UPS', 'ASR', 'CFC', 'FAF', 'KCM', 
    'GTI', 'POE', 'JOS', 'NWS'
}


def is_commercial_callsign(callsign):
    """
    Validates a callsign against the commercial flight pattern AND checks for 
    exclusionary prefixes (military/private/cargo).
    """
    if not callsign or callsign.strip() == '':
        return False
    
    clean_callsign = callsign.strip().upper()

    # 1. Check if the callsign matches the structural pattern (e.g., AAL123)
    if not COMMERCIAL_CALLSIGN_PATTERN.match(clean_callsign):
        return False
    
    # 2. Extract the 3-letter ICAO prefix
    icao_prefix = clean_callsign[:3]
    
    # 3. Check if the prefix is on the exclusion list
    if icao_prefix in NON_COMMERCIAL_PREFIXES:
        return False

    return True


def convert_icao_to_iata(icao_code):
    """Converts a 4-letter ICAO airport code to a 3-letter IATA code using the comprehensive mapping."""
    if icao_code:
        iata_code = ICAO_TO_IATA_MAPPING.get(icao_code.upper())
        if iata_code:
            return iata_code
    return icao_code if icao_code else "N/A"


# --- 5. DATA FETCHING (OpenSky Position, AeroAPI Route) ---

def get_access_token_opensky(client_id, client_secret):
    """Implements the OpenSky OAuth2 Client Credentials Flow for position data."""
    global OAUTH_TOKEN, TOKEN_EXPIRY_TIME
    
    if OAUTH_TOKEN and TOKEN_EXPIRY_TIME > time.time() + 60:
        LOGGER.debug("OpenSky token is valid and refreshed.")
        return OAUTH_TOKEN

    token_url = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret
    }

    try:
        response = requests.post(token_url, headers=headers, data=data)
        response.raise_for_status()
        token_data = response.json()
        
        OAUTH_TOKEN = token_data['access_token']
        TOKEN_EXPIRY_TIME = time.time() + token_data['expires_in'] - 5 
        LOGGER.info("Successfully refreshed OpenSky OAuth Token.")
        return OAUTH_TOKEN

    except requests.exceptions.RequestException as e:
        LOGGER.error(f"Failed to obtain OpenSky token. Auth Error: {e}")
        log_critical_alert(
            subject="CRITICAL: OpenSky Authentication Failure",
            body=f"Failed to obtain new OAuth token for OpenSky. Check client_id/client_secret in credentials.json. Error: {e}"
        )
        OAUTH_TOKEN = None
        TOKEN_EXPIRY_TIME = 0
        return None

def get_aircraft_metadata(icao24):
    """Fetches the aircraft model (type) for a given ICAO24 using the OpenSky metadata endpoint."""
    metadata_url = f"https://opensky-network.org/api/metadata/aircraft?icao24={icao24}"
    
    try:
        response = requests.get(metadata_url, timeout=5)
        response.raise_for_status()
        data = response.json()
        model = data.get('model', 'N/A')
        LOGGER.debug(f"Metadata lookup for {icao24}: Model={model}")
        return model
    except requests.exceptions.RequestException:
        LOGGER.debug(f"Metadata lookup failed for {icao24}.")
        return "N/A"


def get_flight_route_aeroapi(icao24: str, ident: str):
    """
    Queries the AeroAPI for the flight route using the flight IDENT (callsign).
    This function strictly adheres to the monthly, daily, and hourly budget quotas.
    
    Args:
        icao24: The ICAO24 identifier (used as the cache key).
        ident: The callsign/flight identifier (used for the AeroAPI request).
    """
    global AERO_CALLS_TODAY, AERO_CALLS_THIS_HOUR, AERO_CALLS_THIS_MONTH
    global FLIGHT_ROUTE_CACHE
    
    # 1. HARD BUDGET CHECKS (Critical for cost control)
    
    # Check Monthly Limit (The ABSOLUTE Stopper)
    if AERO_CALLS_THIS_MONTH >= MAX_AERO_API_MONTHLY_CALLS:
        message = f"Monthly AeroAPI quota ({MAX_AERO_API_MONTHLY_CALLS}) HIT. STOPPING ALL LOOKUPS."
        LOGGER.critical(f"BUDGET EXCEEDED: {message}")
        log_critical_alert("CRITICAL: Monthly AeroAPI Budget Hit", message)
        return "N/A", "N/A", message
        
    # Check Hourly Limit
    if AERO_CALLS_THIS_HOUR >= MAX_AERO_API_HOURLY_CALLS:
        message = f"Hourly AeroAPI quota ({MAX_AERO_API_HOURLY_CALLS}) hit."
        LOGGER.warning(f"BUDGET EXCEEDED: {message}")
        log_critical_alert("WARNING: Hourly AeroAPI Budget Hit", message)
        return "N/A", "N/A", message

    # Check Daily Limit
    if AERO_CALLS_TODAY >= MAX_AERO_API_DAILY_CALLS:
        message = f"Daily AeroAPI quota ({MAX_AERO_API_DAILY_CALLS}) hit."
        LOGGER.warning(f"BUDGET EXCEEDED: {message}")
        log_critical_alert("WARNING: Daily AeroAPI Budget Hit", message)
        return "N/A", "N/A", message

    aero_url = f"https://aeroapi.flightaware.com/aeroapi/flights/{ident}"
    
    headers = {
        "x-apikey": AERO_KEY,
        "Accept": "application/json"
    }
    
    origin, destination = "N/A", "N/A"
    
    try:
        response = requests.get(aero_url, headers=headers, timeout=10)
        response.raise_for_status() # Raises HTTPError for 4xx/5xx responses
        
        # 2. SUCCESS: Increment all counters and save state
        AERO_CALLS_TODAY += 1
        AERO_CALLS_THIS_HOUR += 1 
        AERO_CALLS_THIS_MONTH += 1
        save_aero_usage()
        
        flight_data = response.json()
        flights = flight_data.get('flights', [])
        
        if flights:
            most_recent_flight = flights[0]
            
            origin_obj = most_recent_flight.get('origin')
            dest_obj = most_recent_flight.get('destination')
            
            dep_airport_icao = origin_obj.get('code') if origin_obj and origin_obj.get('code') else None
            arr_airport_icao = dest_obj.get('code') if dest_obj and dest_obj.get('code') else None
            
            if dep_airport_icao:
                origin = convert_icao_to_iata(dep_airport_icao)
            
            if arr_airport_icao:
                destination = convert_icao_to_iata(arr_airport_icao)
            
            # 3. CACHE RESULT
            FLIGHT_ROUTE_CACHE[icao24] = {
                'origin': origin,
                'destination': destination,
                'timestamp': int(time.time())
            }
            LOGGER.info(f"AeroAPI Success: Route found for {ident} ({icao24}). Cached result: {origin}->{destination}.")
            return origin, destination, "Success"
        
        # If no flights are found by AeroAPI
        LOGGER.info(f"AeroAPI Success: No flight data found for ident {ident}. (Status 200/No Flights)")
        return "N/A", "N/A", "Success (No Data)"
            
    except requests.exceptions.HTTPError as e:
        status_code = response.status_code
        error_reason = f"HTTP Error {status_code}: {e}"
        LOGGER.error(f"AeroAPI Failure for {ident} ({icao24}). Reason: {error_reason}. Response: {response.text[:100]}...")
        if status_code in [401, 403]:
             log_critical_alert("CRITICAL: AeroAPI Key Invalid (401/403)", f"API returned {status_code}. Check your 'aero_key' in credentials.json.")
        return "N/A", "N/A", error_reason
    except requests.exceptions.RequestException as e:
        error_reason = f"Network/Timeout Error: {e}"
        LOGGER.error(f"AeroAPI Failure for {ident} ({icao24}). Reason: {error_reason}")
        return "N/A", "N/A", error_reason
    except json.JSONDecodeError:
        error_reason = "JSON Decode Error (Invalid response format)"
        LOGGER.error(f"AeroAPI Failure for {ident} ({icao24}). Reason: {error_reason}")
        return "N/A", "N/A", error_reason


def _fetch_raw_states_opensky(token):
    """Fetches raw states (position) from the OpenSky API using Bearer Token."""
    # Define bounding box for states/all endpoint
    delta = 0.5 
    lamin = HOME_LAT - delta
    lamax = HOME_LAT + delta
    lomin = HOME_LON - delta
    lomax = HOME_LON + delta
    
    api_url = f"https://opensky-network.org/api/states/all?lamin={lamin}&lomin={lomin}&lamax={lamax}&lomax={lomax}"
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        response = requests.get(api_url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        raw_states = data.get('states')
        
        if raw_states:
            LOGGER.info(f"OpenSky Success: Fetched {len(raw_states)} aircraft states in bounding box.")
            return raw_states
        else:
            LOGGER.warning("OpenSky call succeeded (200 OK) but returned zero states.")
            return []
    
    except requests.exceptions.HTTPError as e:
        status_code = response.status_code
        LOGGER.error(f"OpenSky API failed with status {status_code}. Details: {e}")
        return []
    except requests.exceptions.RequestException as e:
        LOGGER.error(f"OpenSky API request failed: {e}")
        return []

def get_nearby_planes():
    """
    Orchestrates data fetching using OpenSky (position) and AeroAPI (route).
    Implements cache, filtering, and hard budget control.
    """
    global ROUTE_LOOKUP_COUNTER, MAX_ROUTE_LOOKUPS_PER_CYCLE

    # Reset the burst lookup counter for the new cycle
    ROUTE_LOOKUP_COUNTER = 0

    # 1. AUTHENTICATION for OpenSky Position Data
    token = get_access_token_opensky(CLIENT_ID, CLIENT_SECRET)
    if not token:
        LOGGER.error("OpenSky authentication failed, skipping data fetch cycle.")
        return []

    # 2. FETCH RAW STATES (OpenSky API)
    raw_states = _fetch_raw_states_opensky(token)
    LOGGER.info(f"Starting flight processing for {len(raw_states)} raw states.")

    nearby_planes = []
    home_pos = (HOME_LAT, HOME_LON)
    current_time = int(time.time())

    # 3. Check if we are in the valid time window for API calls.
    # --- OPERATIONAL WINDOW CHECK ---
    is_active_mode = False
    if START_HOUR <= END_HOUR:
        if START_HOUR <= CURRENT_HOUR < END_HOUR:
            is_active_mode = True
    else:
        if CURRENT_HOUR >= START_HOUR or CURRENT_HOUR < END_HOUR:
            is_active_mode = True

    LOGGER.debug(f"Operational Check: Current Hour {CURRENT_HOUR:02d}. Active Mode: {is_active_mode}")
    
    # --- COLLECT ALL CANDIDATES FIRST ---
    candidates = []

    if raw_states:
        for s in raw_states:
            icao24 = s[0] 
            callsign = s[1]
            longitude = s[5] 
            latitude = s[6] 
            baro_altitude = s[7] 
            velocity = s[9]      

            if latitude and longitude and callsign and icao24 and baro_altitude and velocity:
                plane_pos = (latitude, longitude)
                distance = haversine(home_pos, plane_pos, unit=Unit.KILOMETERS)
                
                if distance <= RANGE_KM:
                    ident = callsign.strip()
                    candidates.append({
                        'icao24': icao24,
                        'callsign': ident,
                        'distance': distance,
                        'baro_altitude': baro_altitude,
                        'velocity': velocity
                    })
    
    # Sort candidates by distance (closest first)
    candidates.sort(key=lambda p: p['distance'])
    
    # --- ENRICHMENT LOGIC: Only enrich the closest commercial flight per cycle ---
    final_planes = []
    
    # Flag to ensure we only do ONE API lookup in this entire cycle
    api_lookup_performed_this_cycle = False
    
    for i, plane in enumerate(candidates):
        ident = plane['callsign']
        icao24 = plane['icao24']
        
        model = "N/A"
        origin = "N/A"
        destination = "N/A"
        
        # Default data structure
        plane_data = {
            "callsign": ident,
            "distance": int(plane['distance']),
            "altitude": int(plane['baro_altitude'] * 3.28084),
            "speed": int(plane['velocity'] * 1.94384),
            "model": "N/A",
            "origin": "N/A",
            "destination": "N/A"
        }
        
        # --- LOGIC FOR ENRICHMENT ---
        # We only enrich if it's a commercial flight
        if is_commercial_callsign(ident):
            
            # Fetch Model (Cheap/Free call)
            model = get_aircraft_metadata(icao24)
            plane_data['model'] = model
            
            # Check Cache for Route
            is_cached_and_valid = icao24 in FLIGHT_ROUTE_CACHE and \
                                  current_time < FLIGHT_ROUTE_CACHE[icao24]['timestamp'] + ROUTE_CACHE_LIFETIME_SECONDS
            
            if is_cached_and_valid:
                cached_entry = FLIGHT_ROUTE_CACHE.get(icao24)
                plane_data['origin'] = cached_entry['origin']
                plane_data['destination'] = cached_entry['destination']
                LOGGER.info(f"Cache Hit: {ident} route ({plane_data['origin']}->{plane_data['destination']}) served from cache.")
            
            # If not in cache, check if we can perform an API lookup
            # CRITICAL: We only allow ONE API lookup per cycle, and only for the closest plane (i==0)
            elif i == 0 and not api_lookup_performed_this_cycle and is_active_mode:
                
                # We perform the lookup for the FIRST plane in the sorted list
                LOGGER.info(f"Initiating SINGLE AeroAPI lookup for closest plane: {ident}")
                origin, destination, status = get_flight_route_aeroapi(icao24, ident)
                
                plane_data['origin'] = origin
                plane_data['destination'] = destination
                
                # Mark that we have used our one allowed lookup for this cycle
                api_lookup_performed_this_cycle = True
            
            else:
                LOGGER.debug(f"Skipping API lookup for {ident} (Not closest or limit reached).")

        final_planes.append(plane_data)
    
    return final_planes

# --- 6. WEATHER DATA FETCHING (OpenWeatherMap) ---

def get_weather_data(lat, lon, api_key):
    """Fetches current weather data for the given coordinates from OpenWeatherMap."""
    if not api_key:
        LOGGER.error("OpenWeatherMap API Key Missing.")
        return None, "N/A", "Weather API Key Missing"

    weather_url = (
        f"https://api.openweathermap.org/data/2.5/weather?"
        f"lat={lat}&lon={lon}&appid={api_key}&units=imperial" 
    )

    try:
        response = requests.get(weather_url, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        temperature_f = int(data['main']['temp'])
        weather_description = data['weather'][0]['main']
        
        LOGGER.info(f"Weather Data Success: {weather_description}, {temperature_f}F")
        return weather_description, temperature_f, None

    except requests.exceptions.RequestException as e:
        LOGGER.error(f"OpenWeatherMap API request failed: {e}")
        return None, "N/A", "NETWORK ERROR"
    except (KeyError, IndexError, TypeError):
        LOGGER.error("OpenWeatherMap Data Parse Error.")
        return None, "N/A", "DATA PARSE ERROR"


# --- 7. DISPLAY LOOP (Desktop Simulation) ---

def run_display():
    """Main loop that fetches data and prints the result to the console."""
    
    LOGGER.info("--- Starting Flight Tracker Simulation ---")
    LOGGER.info(f"Configuration: Center={HOME_LAT},{HOME_LON} | Range={RANGE_KM}km | Refresh={REFRESH_TIME}s")
    
    while True:
        # Check and potentially reset usage counter at the start of every loop
        load_aero_usage() 
        NOW = datetime.now()
        CURRENT_HOUR = NOW.hour
            
        planes = get_nearby_planes()
        
        closest_plane = None
        if planes:
            # planes are already sorted by distance in get_nearby_planes
            closest_plane = planes[0]
        
        if closest_plane:
            # --- FLIGHT DATA DISPLAY (REVISED TO AVOID 'N/A') ---
            p = closest_plane
            
            # 1. Prepare optional segments (only include if not "N/A")
            parts = [p['callsign']]
            
            # Route Segment
            origin = p['origin']
            destination = p['destination']
            route_segment = ""
            
            if origin != "N/A" and destination != "N/A":
                route_segment = f"{origin} > {destination}"
            elif origin != "N/A":
                route_segment = f"(FROM:{origin})"
            elif destination != "N/A":
                route_segment = f"(TO:{destination})"
                
            if route_segment:
                parts.append(route_segment)
                
            # Model Segment
            #if p['model'] != "N/A":
            #    parts.append(p['model'])
            
            # Append the required dynamic metrics
            #parts.append(f"{p['distance']}KM")
            if route_segment == "":
                parts.append(NOW.strftime('%I:%M'))
            parts.append(f"{p['altitude']}FT")
            if route_segment == "":
                parts.append(f"{p['speed']}KT")
            
            # 2. Join all parts with "  "
            if route_segment == "":
                message = " ".join(parts)
            else:
                message = "   ".join(parts)

            num_flights = len(planes)
            LOGGER.info(f"Display Message (Active): Closest flight: {message}. Total planes: {num_flights}")

        else:
            # Fallback to Time/Weather if no planes are found in range
            weather, temp, error = get_weather_data(HOME_LAT, HOME_LON, OPENWEATHERMAP_API_KEY)

            if error:
                message = f"{NOW.strftime('%m/%d/%y  %I:%M')}  {error}"
            else:
                message = f"{NOW.strftime('%m/%d/%y  %I:%M')}  {weather.upper()}  {temp}F"
            
            num_flights = 0
            LOGGER.info(f"Display Message (Active): No flights in range. Displaying weather: {message}")

        next_start_time = NOW.replace(minute=0, second=0, microsecond=0, hour=START_HOUR)
        
        # Calculate next start time correctly for cross-midnight schedules
        if START_HOUR <= END_HOUR:
            if CURRENT_HOUR >= END_HOUR:
                next_start_time = next_start_time + timedelta(days=1)
        else: # Crosses midnight (e.g., 20 to 8)
            if END_HOUR <= CURRENT_HOUR < START_HOUR:
                # We are in the dead zone (e.g., 8 AM to 8 PM)
                pass 
            else:
                # If current time is after START_HOUR (e.g., 21:00), next start is tomorrow
                if CURRENT_HOUR >= START_HOUR:
                        next_start_time = next_start_time + timedelta(days=1)

        time_to_wait = (next_start_time - NOW).total_seconds()
        sleep_duration = max(1, min(REFRESH_TIME, time_to_wait)) 

        # Print the final output (to console/sysout for user viewing)
        # We use print here intentionally to simulate the display output, separate from the log.
        print("\n" + "=" * 80)
        print(f"--- ACTIVE MODE: {NOW.strftime('%Y-%m-%d %H:%M:%S')} ---")
        print(f"MONTHLY: {AERO_CALLS_THIS_MONTH}/{MAX_AERO_API_MONTHLY_CALLS} | DAILY: {AERO_CALLS_TODAY}/{MAX_AERO_API_DAILY_CALLS}")
        print(f"HOURLY: {AERO_CALLS_THIS_HOUR}/{MAX_AERO_API_HOURLY_CALLS} | BURST: {ROUTE_LOOKUP_COUNTER}/{MAX_ROUTE_LOOKUPS_PER_CYCLE}")
        print(f"NOTICE: Sleeping for {int(sleep_duration)}s until next check. Next Active: {next_start_time.strftime('%Y-%m-%d %H:%M')}")

        if AERO_CALLS_THIS_MONTH >= MAX_AERO_API_MONTHLY_CALLS:
            print("--- CRITICAL WARNING: MONTHLY BUDGET HIT. NO MORE ROUTE LOOKUPS. ---")
        elif AERO_CALLS_TODAY >= MAX_AERO_API_DAILY_CALLS or AERO_CALLS_THIS_HOUR >= MAX_AERO_API_HOURLY_CALLS:
            print("--- WARNING: HARD DAILY/HOURLY BUDGET HIT. NO MORE ROUTE LOOKUPS. ---")
            
        print("DISPLAY TEXT: " + message)
        print("=" * 80)

        # Send to LED Matrix Library
        try:
            import led_display
            led_display.scroll_message(message)
        except ImportError:
            pass # Running on desktop without LED hardware
        
        time.sleep(REFRESH_TIME)

# --- 8. EXECUTION ---
if __name__ == "__main__":
    run_display()
