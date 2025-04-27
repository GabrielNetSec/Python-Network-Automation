"""
NETCONF Loopback Configuration Automation Script
Author: Gabriel Naranjo - GabrielNetSec
Description:
    Connects to a list of Cisco routers using NETCONF, creates Loopback interfaces on each device
    (with per-router custom second octet based on the device's last IP octet),
    applies the configuration using the best available datastore (candidate or running),
    and gives the user the option to save the configuration to startup-config.
    Includes robust input validation, summary output, exception handling, and visual preview.
"""

from ncclient import manager, xml_
import xmltodict
import getpass
import logging
import xml.dom.minidom
from typing import List
from tabulate import tabulate

# ------------- User Parameters -------------
ROUTERS_LIST: List[str] = ["192.168.56.110", "192.168.56.111"]  # Update with your router IP addresses

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')

def build_loopback_config(loop_num: int, second_octet: int, desc: str) -> str:
    config_dict = {
        "config": {
            "native": {
                "@xmlns": "http://cisco.com/ns/yang/Cisco-IOS-XE-native",
                "interface": {
                    "Loopback": {
                        "name": str(loop_num),
                        "description": f"Loopback{loop_num} - {desc}",
                        "ip": {
                            "address": {
                                "primary": {
                                    "address": f"10.{second_octet}.{loop_num}.1",
                                    "mask": "255.255.255.255"
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    return xmltodict.unparse(config_dict)

def save_config_rpc() -> str:
    return """
    <cisco-ia:save-config xmlns:cisco-ia="http://cisco.com/yang/cisco-ia"/>
    """

def print_xml_response(xml_response: str) -> None:
    try:
        print(xml.dom.minidom.parseString(xml_response).toprettyxml())
    except Exception:
        print(xml_response)

def has_candidate_capability(session) -> bool:
    for capability in session.server_capabilities:
        if 'candidate' in capability:
            return True
    return False

def get_last_octet(ip_address: str) -> int:
    return int(ip_address.strip().split('.')[-1])

def validate_loopback_count(n: str) -> int:
    try:
        val = int(n)
        if 1 <= val <= 255:
            return val
        else:
            print("Loopback number must be between 1 and 255. Using default 20.")
            return 20
    except Exception:
        print("Invalid number. Using default 20.")
        return 20

def preview_interfaces(router_ip: str, num_loopbacks: int, second_octet: int, desc: str):
    table = []
    for loop_num in range(1, num_loopbacks + 1):
        ip = f"10.{second_octet}.{loop_num}.1/32"
        table.append([f"Loopback{loop_num}", ip, desc])
    print(f"\nPreview for router {router_ip} (Loopbacks to be created):")
    try:
        print(tabulate(table, headers=["Interface", "IP Address", "Description"], tablefmt="github"))
    except Exception:
        # If tabulate not available
        print("{:<12} {:<20} {}".format("Interface", "IP Address", "Description"))
        for row in table:
            print("{:<12} {:<20} {}".format(*row))

def configure_router(router_ip: str, username: str, password: str, num_loopbacks: int, desc: str, print_xml: bool = False):
    results = {
        "router": router_ip,
        "status": "success",
        "interfaces_created": 0,
        "error": ""
    }
    logging.info(f"Connecting to {router_ip} via NETCONF...")
    try:
        with manager.connect(
            host=router_ip,
            port=830,
            username=username,
            password=password,
            hostkey_verify=False,
            allow_agent=False,
            look_for_keys=False,
            timeout=30
        ) as netconf_session:
            second_octet = get_last_octet(router_ip)
            use_candidate = has_candidate_capability(netconf_session)
            target_ds = "candidate" if use_candidate else "running"
            if use_candidate:
                logging.info(f"{router_ip}: Using target datastore 'candidate' (Loopback IP second octet: {second_octet})")
            else:
                logging.warning(f"{router_ip}: Candidate not supported. Using 'running' as target datastore (Loopback IP second octet: {second_octet})")

            preview_interfaces(router_ip, num_loopbacks, second_octet, desc)

            confirm = input(f"Proceed with creation of {num_loopbacks} loopbacks on {router_ip}? (y/n): ").strip().lower()
            if confirm != "y":
                print(f"Skipping {router_ip} as requested.")
                results["status"] = "skipped"
                return results

            for loop_num in range(1, num_loopbacks + 1):
                config_xml = build_loopback_config(loop_num, second_octet, desc)
                response = netconf_session.edit_config(target=target_ds, config=config_xml)
                if print_xml:
                    print_xml_response(response.xml)
                if use_candidate:
                    commit_response = netconf_session.commit()
                    if print_xml:
                        print_xml_response(commit_response.xml)
                results["interfaces_created"] += 1

            # === Confirm save to startup-config ===
            user_choice = input(f"Save config to startup-config on {router_ip}? (y/n): ").strip().lower()
            if user_choice == 'y':
                try:
                    save_response = netconf_session.dispatch(xml_.to_ele(save_config_rpc()))
                    logging.info(f"Configuration saved to startup-config on {router_ip}")
                    if print_xml:
                        print_xml_response(save_response.xml)
                except Exception as e:
                    logging.warning(f"Could not save config to startup-config on {router_ip}: {e}")

    except Exception as e:
        logging.error(f"Error configuring {router_ip}: {e}")
        results["status"] = "error"
        results["error"] = str(e)
    return results

def main():
    print("=== Network NETCONF Loopback Automation Script ===")
    username = input("Username: ")
    password = getpass.getpass("Password: ")
    print_xml = input("Print NETCONF XML responses? (y/n): ").lower() == 'y'
    num_loopbacks = validate_loopback_count(input("Enter the number of loopback interfaces to create per router (1-255): "))
    desc = input("Enter a description for the interfaces (leave empty for default): ").strip()
    if not desc:
        desc = "Automated by Python NETCONF Script"

    summary = []
    for router_ip in ROUTERS_LIST:
        try:
            result = configure_router(router_ip, username, password, num_loopbacks, desc, print_xml)
            if result:
                summary.append(result)
        except Exception as e:
            logging.error(f"Exception for router {router_ip}: {e}")
            summary.append({
                "router": router_ip,
                "status": "error",
                "interfaces_created": 0,
                "error": str(e)
            })

    # Print summary
    print("\n==== SUMMARY REPORT ====")
    headers = ["Router", "Status", "Interfaces Created", "Error"]
    table = []
    for res in summary:
        table.append([res.get("router", ""), res.get("status", ""), res.get("interfaces_created", 0), res.get("error", "")])
    try:
        print(tabulate(table, headers=headers, tablefmt="github"))
    except Exception:
        print("{:<18} {:<10} {:<20} {}".format(*headers))
        for row in table:
            print("{:<18} {:<10} {:<20} {}".format(*row))

if __name__ == "__main__":
    main()
