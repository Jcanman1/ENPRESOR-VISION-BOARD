"""Background tasks for automatically connecting to OPC machines."""

import logging
from threading import Thread
import asyncio

logger = logging.getLogger(__name__)

# Functions copied from the original dashboard script.
# These helpers are intentionally separate to keep the main dashboard
# file focused on UI logic.

def start_auto_reconnection():
    """Start the background thread that retries lost connections."""
    if not hasattr(app_state, 'reconnection_thread') or not app_state.reconnection_thread.is_alive():
        app_state.reconnection_thread = Thread(target=auto_reconnection_thread)
        app_state.reconnection_thread.daemon = True
        app_state.reconnection_thread.start()
        logger.info("Started auto-reconnection thread")


def startup_auto_connect_machines():
    """Connect to every saved machine once the application launches.

    The function reads persisted machine information, attempts to
    establish OPC connections for each entry and caches the data for
    later reconnection attempts.
    """
    try:
        # Load saved machines data
        floors_data, machines_data = load_floor_machine_data()

        if not machines_data or not machines_data.get("machines"):
            logger.info("No machines found for auto-connection")
            return

        machines = machines_data.get("machines", [])
        connected_count = 0

        logger.info(f"Attempting to auto-connect to {len(machines)} machines on startup...")

        for machine in machines:
            machine_id = machine.get("id")
            machine_ip = machine.get("selected_ip") or machine.get("ip")

            if not machine_ip:
                logger.info(f"Skipping machine {machine_id} - no IP address configured")
                continue

            if machine_id in machine_connections:
                logger.info(f"Machine {machine_id} already connected, skipping")
                continue

            try:
                logger.info(f"Auto-connecting to machine {machine_id} at {machine_ip}...")

                # Create a new event loop for this thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                try:
                    # Use the existing connect function with proper async handling
                    connection_success = loop.run_until_complete(
                        connect_and_monitor_machine(machine_ip, machine_id, "Satake.EvoRGB.1")
                    )

                    if connection_success:
                        logger.info(f"✓ Successfully auto-connected to machine {machine_id}")
                        connected_count += 1
                    else:
                        logger.warning(f"✗ Failed to auto-connect to machine {machine_id} - connection returned False")
                    if trigger == "mode-selector":
                        _debug("[LAB TEST DEBUG] Mode selector changed to lab - lab already prepared!")
                        # Lab environment was prepared when machine connected - just reset state
                        new_running = False
                        new_stop_time = None
                except Exception as conn_error:
                    logger.warning(f"✗ Failed to auto-connect to machine {machine_id}: {conn_error}")
                finally:
                    loop.close()

            except Exception as e:
                logger.error(f"Error in connection setup for machine {machine_id}: {e}")

        logger.info(
            f"Startup auto-connection complete: {connected_count}/{len(machines)} machines connected"
        )

        # Start the main update thread if any machines connected
        try:
            floors_data, machines_data = load_floor_machine_data()
            if machines_data:
                app_state.machines_data_cache = machines_data
                logger.info(
                    f"Populated machines cache with {len(machines_data.get('machines', []))} machines for auto-reconnection"
                )
        except Exception as e:
            logger.error(f"Error populating machines cache: {e}")

        # Start the main update thread if any machines connected
        if connected_count > 0:
            if app_state.update_thread is None or not app_state.update_thread.is_alive():
                app_state.thread_stop_flag = False
                app_state.update_thread = Thread(target=opc_update_thread)
                app_state.update_thread.daemon = True
                app_state.update_thread.start()
                logger.info("Started OPC update thread for auto-connected machines")
        else:
            logger.info("No machines connected - auto-reconnection thread will handle retry attempts")

    except Exception as e:
        logger.error(f"Error in startup auto-connection: {e}")


def delayed_startup_connect():
    """Run ``startup_auto_connect_machines`` after a short delay.

    Sleeping briefly allows the Dash application to finish loading
    before network activity begins.
    """
    import time

    time.sleep(3)  # Wait 3 seconds for app to fully start
    startup_auto_connect_machines()


def initialize_autoconnect():
    """Entry point used by the Dash app to start background threads."""
    start_auto_reconnection()
    startup_thread = Thread(target=delayed_startup_connect)
    startup_thread.daemon = True
    startup_thread.start()
    logger.info("Scheduled startup auto-connection...")

