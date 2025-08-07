import sys
import time
import logging
import argparse
from python_chargepoint import ChargePoint
from python_chargepoint.exceptions import ChargePointCommunicationException
from influxdb import InfluxDBClient

# --- GLOBAL VARIABLES ---
current_charging_session = None

# --- FUNCTIONS ---

def parse_args():
    parser = argparse.ArgumentParser(description="Solar Smart Charge Controller for ChargePoint Home Flex (via InFluxDB).")

    # ChargePoint credentials
    parser.add_argument("--username", required=True, help="ChargePoint account username or email")
    parser.add_argument("--password", required=True, help="ChargePoint account password")

    # InfluxDB 1.x parameters
    parser.add_argument("--influxdb-host", default="localhost", help="InfluxDB host (default: localhost)")
    parser.add_argument("--influxdb-port", type=int, default=8086, help="InfluxDB port (default: 8086)")
    parser.add_argument("--influxdb-user", required=True, help="InfluxDB username")
    parser.add_argument("--influxdb-pass", required=True, help="InfluxDB password")
    parser.add_argument("--influxdb-db", default="pvs6", help="InfluxDB database name (default: pvs6)")

    # Control loop options
    parser.add_argument("--control-interval", type=int, default=5, help="Control interval in minutes (default: 5)")
    parser.add_argument("--slope-window", type=int, default=30, help="Time window in minutes for slope calculation (default: 30)")
    parser.add_argument("--log-file", default="solar_charge_controller.log", help="Log file path")
    parser.add_argument("--quiet", action="store_true", help="Suppress console logging")

    return parser.parse_args()

def setup_logging(log_file, quiet):
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    if not quiet:
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        console.setFormatter(formatter)
        logging.getLogger().addHandler(console)

def log_control_metrics_to_influx(influx_client, solar_slope, predicted_excess, current_charging_watts, target_amps, current_amperage):
    json_body = [{
        "measurement": "solar_charge_control",
        "fields": {
            "solar_slope_w_per_s": float(solar_slope),
            "excess_solar_watts": float(predicted_excess),
            "charging_power_watts": float(current_charging_watts),
            "target_amperage": int(target_amps),
            "current_amperage": int(current_amperage),
        }
    }]
    try:
        influx_client.write_points(json_body)
    except Exception as e:
        logging.warning(f"Failed to write control metrics to InfluxDB: {e}")

def get_solar_power_status(influx_client, control_interval_minutes, slope_window_minutes):
    try:
        now = int(time.time())
        control_start_time = now - (control_interval_minutes * 60)
        slope_start_time = now - (slope_window_minutes * 60)
        
        # Time clauses for different queries
        control_time_clause = f"time >= {control_start_time}s and time <= {now}s"
        slope_time_clause = f"time >= {slope_start_time}s and time <= {now}s"

        # Average pv_p (production) over the control window
        production_query = f'SELECT MEAN("pv_p") FROM "sunpower_power" WHERE {control_time_clause}'
        # Average net_p (grid import/export) over the control window
        net_query = f'SELECT MEAN("net_p") FROM "sunpower_power" WHERE {control_time_clause}'
        # Estimate slope via linear regression of pv_p over the longer slope window
        slope_query = (
            f'SELECT DERIVATIVE(MEAN("pv_p"), 1s) FROM "sunpower_power" '
            f'WHERE {slope_time_clause} GROUP BY time(1m) fill(null)'
        )

        prod_result = influx_client.query(production_query)
        net_result = influx_client.query(net_query)
        slope_result = influx_client.query(slope_query)

        prod_point = list(prod_result.get_points())
        net_point = list(net_result.get_points())
        slope_points = list(slope_result.get_points())
        valid_slopes = [pt['derivative'] for pt in slope_points if pt['derivative'] is not None]

        if not prod_point or not net_point or not valid_slopes:
            logging.warning("Influx query returned no data.")
            return None

        # Convert kW → W
        production_watts = prod_point[0]['mean'] * 1000
        net_watts = net_point[0]['mean'] * 1000
        solar_slope_watts_per_s = (sum(valid_slopes) / len(valid_slopes)) * 1000  # kW/s → W/s

        return {
            "production_watts": production_watts,
            "consumption_watts": net_watts,  # note: 'net' = grid power
            "solar_slope_watts_per_second": solar_slope_watts_per_s
        }

    except Exception as e:
        logging.error(f"Failed to query InfluxDB: {e}")
        return None

def determine_target_amperage(avg_excess_solar_watts, allowed_amps, voltage=240):
    if avg_excess_solar_watts <= 0:
        return 0

    # Adjust ideal_amps logic to handle float rounding just below min allowed
    ideal_amps = max(avg_excess_solar_watts / voltage, min(allowed_amps) - 0.5)

    # Round UP to nearest allowed amps
    possible_amps = [amp for amp in allowed_amps if amp >= ideal_amps]
    if not possible_amps:
        return max(allowed_amps)
    return min(possible_amps)

def initialize_charging_session_if_active(client, charger_id):
    """Check if there's an active charging session and initialize the global object."""
    global current_charging_session
    
    try:
        charging_status = client.get_user_charging_status()
        if charging_status:
            current_charging_session = client.get_charging_session(charging_status.session_id)
            logging.info(f"Found and initialized global charging session: {current_charging_session.session_id}")
            return True
        else:
            logging.info("No active charging session found.")
            return False
    except Exception as e:
        logging.warning(f"Failed to check for existing charging session: {e}")
        return False

def is_currently_charging():
    """Check if we're currently charging based on the global session object."""
    global current_charging_session
    return current_charging_session is not None

def get_current_charging_watts(client, charger_id):
    """Fetch current car charging load if charging."""
    global current_charging_session
    
    # If we have a global charging session, try to use it
    if current_charging_session:
        try:
            current_charging_session.refresh()
            charging_watts = current_charging_session.power_kw * 1000
            logging.info(f"Using cached session. Current charging power: {charging_watts}W")
            return charging_watts
        except Exception as e:
            logging.warning(f"Failed to refresh cached charging session: {e}. Falling back to charger status check.")
            current_charging_session = None  # Clear invalid session
    
    # Fallback to original method if no global session or it failed
    return 0

def apply_charging_decision(client, charger_id, charger_status, target_amps, min_amperage):
    global current_charging_session
    current_amperage = charger_status.amperage_limit

    if target_amps == 0:
        if is_currently_charging():
            current_charging_session.stop()
            current_charging_session = None  # Clear global session when stopping
            logging.info("Stopped charging and cleared global session.")
        else:
            logging.info("Already not charging.")
            if current_amperage != min_amperage:
                client.set_amperage_limit(charger_id, min_amperage)
                logging.info(f"Amperage set command set for minimum for {min_amperage}A.")
            current_charging_session = None  # Ensure global session is cleared
        confirmed_amperage = 0
    else:
        if current_amperage != target_amps:
            logging.info(f"Changing amperage from {current_amperage}A to {target_amps}A...")

            was_charging = is_currently_charging()
            if was_charging:
                current_charging_session.stop()
                current_charging_session = None  # Clear global session when stopping

            client.set_amperage_limit(charger_id, target_amps)
            logging.info(f"Amperage set command sent for {target_amps}A.")

            updated_status = client.get_home_charger_status(charger_id)
            confirmed_amperage = updated_status.amperage_limit

            if confirmed_amperage == target_amps:
                logging.info(f"Confirmed amperage: {confirmed_amperage}A.")
            else:
                logging.warning(f"Amperage mismatch! Set {target_amps}A but charger reports {confirmed_amperage}A.")

            if was_charging:
                current_charging_session = client.start_charging_session(charger_id)
                logging.info(f"Restarted charging session: {current_charging_session.session_id}")
        else:
            logging.info(f"Amperage already set correctly ({current_amperage}A). No change needed.")
            confirmed_amperage = current_amperage

        if not is_currently_charging() and charger_status.plugged_in:
            current_charging_session = client.start_charging_session(charger_id)
            logging.info(f"Started charging session: {current_charging_session.session_id}")

    return confirmed_amperage if target_amps != 0 else 0

# --- MAIN ---

def main():
    args = parse_args()

    influx_client = InfluxDBClient(
        host=args.influxdb_host,
        port=args.influxdb_port,
        username=args.influxdb_user,
        password=args.influxdb_pass,
        database=args.influxdb_db
    )

    setup_logging(args.log_file, args.quiet)

    username = args.username
    password = args.password
    control_interval = args.control_interval  # minutes
    slope_window = args.slope_window  # minutes

    logging.info(f"Using {control_interval}-min control interval and {slope_window}-min slope calculation window")

    logging.info("Connecting to ChargePoint...")
    client = ChargePoint(username, password)
    chargers = client.get_home_chargers()

    if not chargers:
        logging.error("No home chargers found.")
        sys.exit(1)

    charger_id = chargers[0]
    logging.info(f"Found charger {charger_id}")

    # Check for existing charging session and initialize global object if active
    initialize_charging_session_if_active(client, charger_id)

    charger_status = client.get_home_charger_status(charger_id)
    allowed_amps = charger_status.possible_amperage_limits
    min_amperage = min(allowed_amps)
    minimum_watts_required = (min_amperage - 0.5) * 240

    logging.info(f"Minimum amperage is {min_amperage}A, requiring at least {minimum_watts_required}W of solar excess to start charging.")

    last_control_change = 0
    last_set_amperage = None

    while True:
        try:
            solar = get_solar_power_status(influx_client, control_interval, slope_window)
            if not solar:
                logging.warning("No solar data. Skipping...")
                time.sleep(control_interval * 60)
                continue

            production = solar["production_watts"]
            consumption = solar["consumption_watts"]
            solar_slope = solar["solar_slope_watts_per_second"]

            charger_status = client.get_home_charger_status(charger_id)
            current_charging_watts = get_current_charging_watts(client, charger_id)
            average_excess = -1 * (consumption - current_charging_watts)
            predicted_excess = average_excess + (solar_slope * control_interval * 60)

            logging.info(f"{control_interval}-min averages - Production: {production:.1f}W, Grid Consumption: {consumption:.1f}W, Current Charging Load: {current_charging_watts:.1f}W, Average Excess: {average_excess:.1f}W, Solar Slope: {solar_slope:.3f}W/s, Predicted Excess: {predicted_excess:.1f}W")

            allowed_amps = charger_status.possible_amperage_limits
            charging_status = charger_status.charging_status

            if production < 500:
                target_amps = max(allowed_amps)
                logging.info(f"Low production ({production:.1f}W). Setting to max amperage {target_amps}A for fast charging.")
            elif predicted_excess >= minimum_watts_required:
                target_amps = determine_target_amperage(predicted_excess, allowed_amps)
                logging.info(f"Predicted excess solar ({predicted_excess:.1f}W). Setting amperage to {target_amps}A.")
            else:
                target_amps = 0
                logging.info(f"Insufficient predicted excess solar ({predicted_excess:.1f}W) < minimum ({minimum_watts_required:.1f}W). Stopping charging.")
                
            if ((time.time() - last_control_change) >  (control_interval * 60)):
                current_amperage = charger_status.amperage_limit
                if charging_status == "CHARGING" and current_amperage == 40 and last_set_amperage != 40:
                    logging.info("User likely set charger to 40A manually. Skipping adjustment.")
                else:
                    try:
                        new_amperage = apply_charging_decision(client, charger_id, charger_status, target_amps, min_amperage)
                        last_set_amperage = new_amperage
                        last_control_change = time.time()
                    except ChargePointCommunicationException as e:
                        logging.error(f"Failed to apply charging decision: {e.message}")

            charger_status = client.get_home_charger_status(charger_id)
            current_amperage = charger_status.amperage_limit
            
            # Update and log the current_charging_watts
            log_control_metrics_to_influx(
                influx_client,
                solar_slope,
                predicted_excess,
                current_charging_watts,
                target_amps,
                current_amperage
            )
            
            time.sleep(60)

        except Exception as e:
            logging.error(f"Error in main loop: {e}")
            time.sleep(control_interval * 60)

if __name__ == "__main__":
    main()
