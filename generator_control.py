#!/usr/bin/env python3
import datetime
import os
import sys
from os.path import join, abspath, dirname
from pprint import pprint
from time import time, sleep

import dbus
from dbus.mainloop.glib import DBusGMainLoop

# our own packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), 'velib_python'))

from vedbus import VeDbusItemImport
from ve_utils import unwrap_dbus_value

softwareVersion = '1.0'

INV_SWITCH_OFF = 4
INV_SWITCH_ON = 3
INV_SWITCH_INVERT_ONLY = 2
INV_SWITCH_CHARGE_ONLY = 1

REVERSE_POWER_CURRENT_THRESHOLD = -5  # Amps

DEFAULT_MODE = "Off"

TIMESTEP = 1
REVERSE_POWER_COUNTER_THRESHOLD = 10 / TIMESTEP  # 10s

EXCEPTION_THRESHOLD = 10

PROFILEMEMORY = True

if PROFILEMEMORY:
    import tracemalloc

class GeneratorController():
    def __init__(self):
        DBusGMainLoop(set_as_default=True)
        self.dbusConn = dbus.SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus()
        self.Mode = ""
        self._Toggle_State = False
        self._inverter_switch_mode_update_time = 0
        self.Off_Button_Pressed_Counter = 0
        self.BMS_Disable = False
        self.Battery_SOC = 0
        self.Battery_Charge_Limit = 0
        self.Battery_Discharge_Limit = 0
        self.AC_Output_Current = None
        self.Inverter_Switch_Mode = 0
        self.Reverse_Power_Counter = 0
        self.Reverse_Power_Alarm = False
        self.Inverter_Connected = False
        self.BMS_Connected = False
        self.relay_states = {}
        self.inverter_delay = 0

        self.outputs_str = ""


        if PROFILEMEMORY:
            self._initial_snapshot = None
            self._current_snapshot = None

        self.dbus_items_spec = {
            "battery_soc": {"service": "com.victronenergy.system", "path": "/Dc/Battery/Soc"},
            "battery_charge_limit": {"service": "com.victronenergy.battery.socketcan_vecan0", "path": "/Info/MaxChargeCurrent"},
            "battery_discharge_limit": {"service": "com.victronenergy.battery.socketcan_vecan0", "path": "/Info/MaxDischargeCurrent"},
            "ac_output_current":    {"service": "com.victronenergy.vebus.ttyS2", "path": "/Ac/Out/L1/I"},
            "inverter_switch_mode": {"service": "com.victronenergy.vebus.ttyS2", "path": "/Mode"},
            "relay_2": {"service": "com.victronenergy.system", "path": "/Relay/2/State"},
            "relay_3": {"service": "com.victronenergy.system", "path": "/Relay/3/State"},
            "relay_4": {"service": "com.victronenergy.system", "path": "/Relay/4/State"},
            "relay_5": {"service": "com.victronenergy.system", "path": "/Relay/5/State"},
            "relay_6": {"service": "com.victronenergy.system", "path": "/Relay/6/State"},
            "relay_7": {"service": "com.victronenergy.system", "path": "/Relay/7/State"},
            "relay_8": {"service": "com.victronenergy.system", "path": "/Relay/8/State"},
            "relay_9": {"service": "com.victronenergy.system", "path": "/Relay/9/State"},
        }

        self.dbus_items = {}

        self.check_and_create_connections()

    @property
    def Fault_Detected(self):
        if self.Mode not in ["Off", "On", "ChargeOnly"]:
            print("Mode Fault", flush=True)
            return True
        if self.BMS_Connected == False:
            print("BMS Fault", flush=True)
            return True
        if (self.Mode != "Off") and (self.Inverter_Connected == False):
            print("Inverter Fault", flush=True)
            return True
        if (self.Mode != "Off") and (self.Reverse_Power_Alarm == True):
            print("Reverse Power Fault", flush=True)
            return True

    # if self.Relays_Connected == False:
    #     return True

    def update_inputs(self):
        self.input_values = {
            "Off_Button": self.read_input(5),
            "On_Button": self.read_input(6),
            "Charge_Button": self.read_input(7),
            "Off_LED": self.read_input(8),
            "On_LED": self.read_input(9),
            "Charge_LED": self.read_input('a'),
            "BMS_Wake": self.read_input('b'),
        }
        pprint({"Inputs": self.input_values}, width=200)


    def read_input(self, input_no):
        path = f"/dev/gpio/digital_input_{input_no}/value"
        with open(path) as f:
            return 1 if (f.read().strip() == '1') else 0

    @property
    def Off_Button_Pressed(self):
        return self.input_values.get("Off_Button")

    @property
    def On_Button_Pressed(self):
        return self.input_values.get("On_Button")

    @property
    def Charge_Button_Pressed(self):
        return self.input_values.get("Charge_Button")

    @property
    def Off_LED_Feedback(self):
        return self.input_values.get("Off_LED")

    @property
    def On_LED_Feedback(self):
        return self.input_values.get("On_LED")

    @property
    def Charge_LED_Feedback(self):
        return self.input_values.get("Charge_LED")

    @property
    def BMS_Wake_Feedback(self):
        return self.input_values.get("BMS_Wake")

    @property
    def Off_LED(self):
        return self.Mode == "Off"

    @property
    def On_LED(self):
        return self.Mode == "On"

    @property
    def Charge_LED(self):
        return self.Mode == "ChargeOnly"

    @property
    def BMS_Wake(self):
        # BMS Wake set in all modes except off with low SOC
        return not ((self.Mode == "Off") and ((self.Battery_SOC < 50) or (self.BMS_Disable)))

    @property
    def DSE_Remote_Start(self):
        # Remote Start only set in "On" mode
        return (self.Mode == "On")

    @property
    def DSE_Mode_Request(self):
        # Mode Request set in "On" and "ChargeOnly" mode
        return ((self.Mode == "On") or (self.Mode == "ChargeOnly"))

    @property
    def RCD_Reset_Switch(self):
        # If (on and off) or (charge and off) buttons held down then trigger RCD reset relay
        return (
                ((self.Off_Button_Pressed) and (self.On_Button_Pressed) and not (self.Charge_Button_Pressed)) or
                ((self.Off_Button_Pressed) and (self.Charge_Button_Pressed) and not (self.On_Button_Pressed))
        )


    @property
    def Service_Restart_Requested(self):
        # If all 3 buttons held down then trigger script reset
        return ((self.Off_Button_Pressed) and (self.On_Button_Pressed) and (self.Charge_Button_Pressed))

    @property
    def Inverter_Switch_Mode_Target(self):
        if self.Reverse_Power_Alarm:  # Disable the inverter if the Reverse Power Alarm is set
            return INV_SWITCH_OFF
        else:
            modes = {"Off": INV_SWITCH_OFF, "On": INV_SWITCH_ON, "InvertOnly": INV_SWITCH_INVERT_ONLY,
                     "ChargeOnly": INV_SWITCH_CHARGE_ONLY}
            return modes.get(self.Mode)

    @property
    def Reverse_Power_Detected(self):
        if self.AC_Output_Current is not None:
            return self.AC_Output_Current < REVERSE_POWER_CURRENT_THRESHOLD
        else:
            return False

    @property
    def Battery_Contactors_Closed(self):
        val = (self.Battery_Charge_Limit) and (self.Battery_Discharge_Limit) # Non zero current limits means that 48V system is online
        if val == False:
            self.inverter_delay = 10
        return val

    def update_mode(self):
        _last_mode = self.Mode

        if self.Mode == "":
            self.Mode = DEFAULT_MODE

        if self.Off_Button_Pressed:
            self.Off_Button_Pressed_Counter += 1
        else:
            self.Off_Button_Pressed_Counter = 0

        if self.Off_Button_Pressed_Counter >= 10:
            self.BMS_Disable = True

        if self.Off_Button_Pressed and not (self.On_Button_Pressed or self.Charge_Button_Pressed):
            self.Mode = "Off"
        elif self.On_Button_Pressed and not (self.Off_Button_Pressed or self.Charge_Button_Pressed):
            self.Mode = "On"
            self.BMS_Disable = False
        elif self.Charge_Button_Pressed and not (self.On_Button_Pressed or self.Off_Button_Pressed):
            self.Mode = "ChargeOnly"
            self.BMS_Disable = False
        else:
            pass  # Leave mode unchanged
        #
        # if (_last_mode != "Off") and (self.Mode != "Off"):
        #     print(self, flush=True)

    def get_dbus_value(self, dbus_item_name: str):
        if (dbus_item := self.dbus_items.get(dbus_item_name)) is not None:
            # print(f"Get DBus Value () : {dbus_item.serviceName} - {dbus_item.path}", flush=True)
            try:
                return unwrap_dbus_value(dbus_item._proxy.GetValue())
            except dbus.exceptions.DBusException as e:
                print(f"Could not get DBUS Item : {dbus_item.serviceName} - {dbus_item.path}", flush=True)
                print(e, flush=True)
                self.clear_dbus_item(dbus_item_name)

    def set_dbus_value(self, dbus_item_name: str, value):
        if (dbus_item := self.dbus_items.get(dbus_item_name)) is not None:
            # print(f"Set DBus Value () : {dbus_item.serviceName} - {dbus_item.path} : {Value}", flush=True))
            try:
                dbus_item.set_value(value)
            except dbus.exceptions.DBusException as e:
                print(f"Could not set DBUS Item : {dbus_item.serviceName} - {dbus_item.path} : {value}", flush=True)
                print(e, flush=True)
                self.clear_dbus_item(dbus_item_name)

    def clear_dbus_item(self, dbus_item_name):
        dbus_item = self.dbus_items.pop(dbus_item_name)
        del dbus_item

    def update_battery_soc(self):
        val = self.get_dbus_value("battery_soc")
        if val is not None:
            self.BMS_Connected = True
            self.Battery_SOC = val
        else:
            self.BMS_Connected = False
            print("Did not receive data from battery about SOC", flush=True)
            self.Battery_SOC = 0

    def update_battery_limits(self):
        charge_lim = self.get_dbus_value("battery_charge_limit")
        discharge_lim = self.get_dbus_value("battery_discharge_limit")
        if (self.Battery_Charge_Limit is not None) and (self.Battery_Discharge_Limit is not None):
            self.BMS_Connected = True
            self.Battery_Charge_Limit = round(charge_lim,1)
            self.Battery_Discharge_Limit = round(discharge_lim,1)
        else:
            self.BMS_Connected = False
            print("Did not receive data from battery about current limits", flush=True)

    def update_ac_output_current(self):
        val = self.get_dbus_value("ac_output_current")
        if val is not None:
            self.Inverter_Connected = True
            self.AC_Output_Current = round(val, 1)
        else:
            self.Inverter_Connected = False
            print("Did not receive data from inverter", flush=True)
            self.AC_Output_Current = None
            self.clear_dbus_item("ac_output_current")

    def update_inverter_switch_mode(self):
        val = self.get_dbus_value("inverter_switch_mode")
        if val is not None:
            self.Inverter_Connected = True
            self.Inverter_Switch_Mode = val
        else:
            self.Inverter_Connected = False
            print("Did not receive switch mode from inverter", flush=True)
            self.Inverter_Switch_Mode = 0

    def update_relay_states(self):
        self.relay_states = {}
        for relay_no in range(2, 10):
            self.relay_states[relay_no] = self.get_dbus_value(f"relay_{relay_no}")
        pprint({"Relays" : self.relay_states}, width=200)

    def set_off_led(self):
        if (self.Fault_Detected):
            self.set_dbus_value("relay_2", self._Toggle_State)
            self._Toggle_State = not self._Toggle_State
        else:
            self.set_dbus_value("relay_2", self.Off_LED)

    def set_outputs(self):
        if (self.Fault_Detected):
            self.set_relay(2,  self._Toggle_State)
            self._Toggle_State = not self._Toggle_State
        else:
            self.set_relay(2,  self.Off_LED)


        self.set_relay(3, self.On_LED)
        self.set_relay(4, self.Charge_LED)
        self.set_relay(5, self.BMS_Wake)
        self.set_relay(6, self.DSE_Remote_Start)
        self.set_relay(7, self.DSE_Mode_Request)
        self.set_relay(8, self.RCD_Reset_Switch)
        self.set_relay(9, self.Reverse_Power_Alarm)

    def set_inverter_switch_mode(self):
        if self.Inverter_Switch_Mode_Target != self.Inverter_Switch_Mode:  # Only Update the switch mode when it changes.
            if (self.Battery_Contactors_Closed): # Only attempt to contol the inverter if the 48V system has become live already
                if self.inverter_delay == 0:
                    if (time() - self._inverter_switch_mode_update_time) > 5:
                        self._inverter_switch_mode_update_time = time()
                        self.set_dbus_value("inverter_switch_mode", self.Inverter_Switch_Mode_Target)
                        print(f"Updating switch mode from {self.Inverter_Switch_Mode} to {self.Inverter_Switch_Mode_Target}.", flush=True)
                    else:
                        print("Not updating the switch mode until 5s have elapsed.", flush=True)
                else:
                    print(f"Waiting {self.inverter_delay}s before activating the inverter")
                    self.inverter_delay -= 1
                    self.inverter_delay = max(0, self.inverter_delay)

    def set_relay(self, relay_no, target_value):
        if isinstance(target_value, bool):
            target_value = int(target_value)
        if target_value != self.relay_states.get(relay_no): # Only Update the switch mode when it changes.
            print(f"Setting Relay {relay_no} to {target_value}")
            self.set_dbus_value(f"relay_{relay_no}", target_value)


    def check_reverse_power(self):
        if self.Reverse_Power_Detected:
            self.Reverse_Power_Counter += 1
            max(REVERSE_POWER_COUNTER_THRESHOLD, self.Reverse_Power_Counter)
        else:
            self.Reverse_Power_Counter -= 1
            self.Reverse_Power_Counter = max(0, self.Reverse_Power_Counter)

        if (self.Reverse_Power_Counter >= REVERSE_POWER_COUNTER_THRESHOLD):
            self.Reverse_Power_Alarm = True
        elif (self.Mode == "Off") and (
                self.Reverse_Power_Counter == 0):  # Only reset if Off and has counted back down to 0
            self.Reverse_Power_Alarm = False

    def run(self):
        self.snapshot_memory()

        counter = 0
        while True:
            t0 = time()
            self.check_and_create_connections()
            self.update_inputs()
            self.update_mode()
            self.update_battery_soc()
            self.update_battery_limits()
            if self.Mode == "On" or self.Mode == "ChargeOnly":
                self.update_ac_output_current()
                self.check_reverse_power()
            self.update_relay_states()
            self.set_outputs()
            self.update_inverter_switch_mode()
            self.set_inverter_switch_mode()

            if (self.Service_Restart_Requested):
                print("Service Restart Requested, Going Down in 5s!", flush=True)
                sleep(5)
                exit()
            # print(f"{datetime.isoformat(datetime.now())} : {self}", flush=True))
            print(self, flush=True)

            counter+=1
            if counter % 60 == 0:
                self.snapshot_memory()

            sleep(max(0, TIMESTEP - (time() - t0)))

    def snapshot_memory(self):
        if PROFILEMEMORY:
            self._current_snapshot = tracemalloc.take_snapshot()
            if self._initial_snapshot == None:
                self._initial_snapshot = self._current_snapshot
            top_stats = self._current_snapshot.compare_to(self._initial_snapshot, 'lineno')

            print("\n*************** Memory Snapshot Top 20 ***************\n")
            count = 0
            for stat in top_stats:
                if "tracemalloc.py" not in str(stat.traceback[0]):
                    count += 1
                    print(stat)
                if count >= 20:
                    break

            print("\n******************************************************\n")

    def __repr__(self):
        return ',\t'.join([
            # f"Relays {self.outputs_str}",
            f"Mode {self.Mode}",
            f"SOC {self.Battery_SOC}%",
            f"Limits {self.Battery_Charge_Limit}A/{self.Battery_Discharge_Limit}A",
            f"AC Out {self.AC_Output_Current}A",
            f"Switch Mode {self.Inverter_Switch_Mode_Target}/{self.Inverter_Switch_Mode}",
            f"Rev Pwr {self.Reverse_Power_Detected} - {self.Reverse_Power_Counter * TIMESTEP}s",
            # f"Off LED {self.Off_LED}",
            # f"On LED {self.On_LED}",
            # f"Charge LED {self.Charge_LED}",
            # f"BMS Wake {self.BMS_Wake}",
            # f"DSE Start {self.DSE_Remote_Start}",
            # f"DSE Mode {self.DSE_Mode_Request}",
            f"Inv Delay {self.inverter_delay}s",
            f"Fault {self.Fault_Detected}",
            f"off_counter {self.Off_Button_Pressed_Counter}",
        ]
        )

    def check_and_create_connections(self):
        for k, v in self.dbus_items_spec.items():
            if self.dbus_items.get(k) is None:
                try:
                    print(f"Creating DBUS Item - {v['service']} : {v['path']}")
                    self.dbus_items[k] = VeDbusItemImport(self.dbusConn, v['service'], v['path'])
                except Exception as e:
                    self.dbus_items[k] = None
                    print(f"Could not find DBUS Item - {v['service']} : {v['path']}")
                    print(e, flush=True)


if __name__ == "__main__":
    if PROFILEMEMORY:
        tracemalloc.start()
    try:
        with open(join(dirname(__file__), "version")) as f:
            version = f.readline()
        print("\n\n****************************************\n")
        print(f"Running generator_control.py \t{version}", flush=True)
        print("\n****************************************\n\n")
        print("Waiting 10s for system to startup", flush=True)
        sleep(10)
        print("Running now!", flush=True)
        g = GeneratorController()
        print(g)
        g.run()  # global dbusObjects  #  # print(__file__ + " starting up")
    except Exception as e:
        print("Exception Raised", flush=True)
        print(e, flush=True)
        raise
# # Have a mainloop, so we can send/receive asynchronous calls to and from dbus  # DBusGMainLoop(set_as_default=True)
