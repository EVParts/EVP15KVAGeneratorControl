#!/usr/bin/env python3
import json
import os
import sys
from os.path import join, dirname, exists
from pprint import pprint, pformat
from time import time, sleep

import dbus
from dbus.mainloop.glib import DBusGMainLoop

# our own packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), 'velib_python'))

from vedbus import VeDbusItemImport
from ve_utils import unwrap_dbus_value

INV_SWITCH_OFF = 4
INV_SWITCH_ON = 3
INV_SWITCH_INVERT_ONLY = 2
INV_SWITCH_CHARGE_ONLY = 1

REVERSE_POWER_THRESHOLD = -500  # Watts

DEFAULT_MODE = "Off"

TIMESTEP = 1
REVERSE_POWER_COUNTER_THRESHOLD = 10 / TIMESTEP  # 10s
AC_SAFTEY_LOOP_COUNTER_THRESHOLD = 30 / TIMESTEP # Time for which the Quattro must show disco LEDs before triggering an EStop Shutdown.
INVERTER_ON_DELAY = 60 / TIMESTEP # Delay for which the Battery contactors must be closed before the alarms which can shut down ths system start counting

MIN_LOG_INTERVAL = 1
# Caused a crash on newer generators?
PROFILEMEMORY = False

if PROFILEMEMORY:
    import tracemalloc

class IO_Tester():
    def __init__(self):
        DBusGMainLoop(set_as_default=True)
        self.dbusConn = dbus.SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus()

        self._last_log = {}
        self.duplicate_log_counter = {}


        if PROFILEMEMORY:
            self._initial_snapshot = None
            self._current_snapshot = None
        self.GX_IO_Ex_Serial = self.find_gx_io_extender_serial()

        print(f"GX IO Extender Serial No is {self.GX_IO_Ex_Serial}")

        self.dbus_items_spec = {
            "Digital_Input_1_Type": {"service": "com.victronenergy.digitalinputs",  "path": f"/Devices/{self.GX_IO_Ex_Serial}_input_1/Type"},
            "Digital_Input_2_Type": {"service": "com.victronenergy.digitalinputs",  "path": f"/Devices/{self.GX_IO_Ex_Serial}_input_2/Type"},
            "Digital_Input_3_Type": {"service": "com.victronenergy.digitalinputs",  "path": f"/Devices/{self.GX_IO_Ex_Serial}_input_3/Type"},
            "Digital_Input_4_Type": {"service": "com.victronenergy.digitalinputs",  "path": f"/Devices/{self.GX_IO_Ex_Serial}_input_4/Type"},
            # "Digital_Input_5_Type": {"service": "com.victronenergy.digitalinputs",  "path": f"/Devices/{self.GX_IO_Ex_Serial}_input_5/Type"},
            # "Digital_Input_6_Type": {"service": "com.victronenergy.digitalinputs",  "path": f"/Devices/{self.GX_IO_Ex_Serial}_input_6/Type"},
            # "Digital_Input_7_Type": {"service": "com.victronenergy.digitalinputs",  "path": f"/Devices/{self.GX_IO_Ex_Serial}_input_7/Type"},
            # "Digital_Input_8_Type": {"service": "com.victronenergy.digitalinputs",  "path": f"/Devices/{self.GX_IO_Ex_Serial}_input_8/Type"},
            # "Digital_Input_5_Label": {"service": "com.victronenergy.digitalinputs",  "path": f"/Devices/{self.GX_IO_Ex_Serial}_input_5/Label"},
            # "Digital_Input_6_Label": {"service": "com.victronenergy.digitalinputs",  "path": f"/Devices/{self.GX_IO_Ex_Serial}_input_6/Label"},
            # "Digital_Input_7_Label": {"service": "com.victronenergy.digitalinputs",  "path": f"/Devices/{self.GX_IO_Ex_Serial}_input_7/Label"},
            # "Digital_Input_8_Label": {"service": "com.victronenergy.digitalinputs",  "path": f"/Devices/{self.GX_IO_Ex_Serial}_input_8/Label"},
            # "Digital_Output_1":     {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/output_1/State"},
            # "Digital_Output_2":     {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/output_2/State"},
            # "Digital_Output_3":     {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/output_3/State"},
            # "Digital_Output_4":     {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/output_4/State"},
            "Digital_Output_5":     {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/output_5/State"},
            "Digital_Output_6":     {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/output_6/State"},
            "Digital_Output_7":     {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/output_7/State"},
            "Digital_Output_8":     {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/output_8/State"},
            "PWM_Output_1":         {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/pwm_1/State"},
            "PWM_Output_2":         {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/pwm_2/State"},
            "PWM_Output_3":         {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/pwm_3/State"},
            "PWM_Output_4":         {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/pwm_4/State"},
            "PWM_Output_1_Duty":    {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/pwm_1/Dimming"},
            "PWM_Output_2_Duty":    {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/pwm_2/Dimming"},
            "PWM_Output_3_Duty":    {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/pwm_3/Dimming"},
            "PWM_Output_4_Duty":    {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/pwm_4/Dimming"},
            "Relay_Output_1":       {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/relay_1/State"},
            "Relay_Output_2":       {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/relay_2/State"},
            "Relay_Output_3":       {"service": f"com.victronenergy.switch.{self.GX_IO_Ex_Serial}", "path": "/SwitchableOutput/relay_3/State"},
        }

        self.dbus_items = {}

        self.check_and_create_connections()

        self.setup_input_channels()
        sleep(5)

        self.check_and_create_connections()


    def find_dbus_service(self, prefix):
        """
        Search for D-Bus service names that start with the given prefix
        and return the remaining part of the name after the prefix.

        Args:
            prefix (str): The prefix to match service names against.

        Returns:
            List[str]: A list of the remaining parts of matched service names.
        """
        try:
            print("Finding dbus services")
            all_services = self.dbusConn.list_names()
            matching_services = []

            for service in all_services:
                if service.startswith(prefix):
                    matching_services.append(service)

            return matching_services
        except dbus.DBusException as e:
            print(f"DBus error: {e}")
            return []


    def get_dbus_value(self, dbus_item_name: str):
        if (dbus_item := self.dbus_items.get(dbus_item_name)) is not None:
            # print(f"Get DBus Value () : {dbus_item.serviceName} - {dbus_item.path}", flush=True)
            t0 = time()
            try:
                return unwrap_dbus_value(dbus_item._proxy.GetValue())
            except dbus.exceptions.DBusException as e:
                print(f"Could not get DBUS Item : {dbus_item.serviceName} - {dbus_item.path}", flush=True)
                print(e, flush=True)
                self.clear_dbus_item(dbus_item_name)
                duration = time() - t0
                timeout = 10
                if duration > timeout:
                    print(f"Call took more than {timeout}s, potentially unrecoverable situation! raising exception!")
                    raise

    def set_dbus_value(self, dbus_item_name: str, value):
        if (dbus_item := self.dbus_items.get(dbus_item_name)) is not None:
            # print(f"Set DBus Value () : {dbus_item.serviceName} - {dbus_item.path} : {Value}", flush=True))
            t0 = time()
            try:
                dbus_item.set_value(value)
                return True
            except dbus.exceptions.DBusException as e:
                print(f"Could not set DBUS Item : {dbus_item.serviceName} - {dbus_item.path} : {value}", flush=True)
                print(e, flush=True)
                self.clear_dbus_item(dbus_item_name)
                duration = time() - t0
                timeout = 10
                if duration > timeout:
                    print(f"Call took more than {timeout}s, potentially unrecoverable situation! raising exception!")
                    raise
                return False
        print(f"Dbus Item has been cleared so cannot be set until it is reconnected : {dbus_item_name} ")
        return False

    def clear_dbus_item(self, dbus_item_name):
        print(f"Removing dbus item : {dbus_item_name}", flush=True)
        try: # Try to remove the offending dbus item
            dbus_item = self.dbus_items.pop(dbus_item_name)
            del dbus_item
        except KeyError:
            print("Could not find dbus item to remove", flush=True)

    def find_gx_io_extender_serial(self):
        prefix = "com.victronenergy.switch."
        services = self.find_dbus_service(prefix)
        if len(services) == 1:
            return services[0][len(prefix):]
        elif len(services) > 1:
            raise RuntimeError("Multiple GX IO Extender modules were found, unclear which to use")
        else:
            raise KeyError("The GX IO Extender DBUS service could not be found")

    def setup_input_channels(self):
        """
        The input channels on the GX IO Extender Module need to be configured as specifi types of input in order to
        be used. Touch input (10) seems to be generic enough for just using as a button.
        The process is to look at dbus service : com.victronenergy.digitalinputs
        and set the paths "Devices/{self.GX_IO_Ex_Serial}_input_{input_no}/Type" to 10

        """
        self.set_dbus_value("Digital_Input_1_Type", 10)
        self.set_dbus_value("Digital_Input_2_Type", 10)
        self.set_dbus_value("Digital_Input_4_Type", 10)
        self.set_dbus_value("Digital_Input_3_Type", 10)

        self.dbus_items_spec["Off Switch"] =        {"service": f"com.victronenergy.digitalinput.{self.GX_IO_Ex_Serial}_input_{1}", "path": "/InputState"}
        self.dbus_items_spec["On Switch"] =         {"service": f"com.victronenergy.digitalinput.{self.GX_IO_Ex_Serial}_input_{2}", "path": "/InputState"}
        self.dbus_items_spec["Charge Switch"] =     {"service": f"com.victronenergy.digitalinput.{self.GX_IO_Ex_Serial}_input_{3}", "path": "/InputState"}
        self.dbus_items_spec["Ignition Feedback"] = {"service": f"com.victronenergy.digitalinput.{self.GX_IO_Ex_Serial}_input_{4}", "path": "/InputState"}

        self.dbus_items_spec["Digital_Input_1_Label"] = {"service": f"com.victronenergy.digitalinput.{self.GX_IO_Ex_Serial}_input_{1}", "path": "/CustomName"}
        self.dbus_items_spec["Digital_Input_2_Label"] = {"service": f"com.victronenergy.digitalinput.{self.GX_IO_Ex_Serial}_input_{2}", "path": "/CustomName"}
        self.dbus_items_spec["Digital_Input_3_Label"] = {"service": f"com.victronenergy.digitalinput.{self.GX_IO_Ex_Serial}_input_{3}", "path": "/CustomName"}
        self.dbus_items_spec["Digital_Input_4_Label"] = {"service": f"com.victronenergy.digitalinput.{self.GX_IO_Ex_Serial}_input_{4}", "path": "/CustomName"}

        sleep(2)
        self.check_and_create_connections()

        self.set_dbus_value("Digital_Input_1_Label", "Off Switch")
        self.set_dbus_value("Digital_Input_2_Label", "On Switch")
        self.set_dbus_value("Digital_Input_3_Label", "Charge Switch")
        self.set_dbus_value("Digital_Input_4_Label", "Ignition Feedback")


    def run(self):

        for i in [ 5, 6, 7, 8]:
            self.set_dbus_value(f"Digital_Output_{i}", 0)
        for i in [1, 2, 3]:
            self.set_dbus_value(f"PWM_Output_{i}", 0)
        for i in [1, 2]:
            self.set_dbus_value(f"Relay_Output_{i}", 0)

        t0 = time()
        pwm_duty = 0
        while (time()-t0) < 30:
            switch = self.get_dbus_value("Off Switch")
            print(f"Switch {switch}, pwmduty {pwm_duty}")
            if switch == 1:
                self.set_dbus_value(f"PWM_Output_1_Duty", pwm_duty)
                self.set_dbus_value(f"PWM_Output_1", 1)
                self.set_dbus_value(f"Digital_Output_5", 1)
                pwm_duty = pwm_duty + 2
                pwm_duty = min(100, pwm_duty)
            else:
                self.set_dbus_value(f"PWM_Output_1_Duty", pwm_duty)
                self.set_dbus_value(f"PWM_Output_1", 1)
                self.set_dbus_value(f"Digital_Output_5", 0)
                pwm_duty = pwm_duty - 10
                pwm_duty = max(0, pwm_duty)




        # for i in [1, 2, 3, 4, 5, 6, 7, 8]:
        #     for state in [0, 1, 0] * 3:
        #         self.set_dbus_value(f"Digital_Output_{i}", state)
        #         sleep(0.2)
        #
        # self.check_and_create_connections()
        # for i in [1, 2, 3, 4]:
        #     self.set_dbus_value(f"PWM_Output_{i}_Duty", 0)
        #     self.set_dbus_value(f"PWM_Output_{i}", 1)
        #     for duty in [0, 10, 20, 25, 30, 35, 40, 50, 60, 70, 80, 90, 100, 90, 80, 70, 60, 50, 40, 35, 30, 25, 20, 10, 0] * 4:
        #         self.set_dbus_value(f"PWM_Output_{i}_Duty", duty)
        #         sleep(0.01)
        #     self.set_dbus_value(f"PWM_Output_{i}", 0)
        #     sleep(0.1)
        #
        # self.check_and_create_connections()
        # for i in [1, 2, 3]:
        #     for state in [0, 1] * 5:
        #         self.set_dbus_value(f"Relay_Output_{i}", state)
        #         sleep(1)
        #
        # for i in [1, 2, 3, 4, 5, 6, 7, 8]:
        #     self.set_dbus_value(f"Digital_Output_{i}", 0)
        # for i in [1, 2, 3]:
        #     self.set_dbus_value(f"PWM_Output_{i}", 0)
        # for i in [1, 2, 3]:
        #     self.set_dbus_value(f"Relay_Output_{i}", 0)


    def check_and_create_connections(self):
        for k, v in self.dbus_items_spec.items():
            if self.dbus_items.get(k) is None:
                try:
                    print(f"Creating DBUS Item - {v}")
                    self.dbus_items[k] = VeDbusItemImport(self.dbusConn, v['service'], v['path'])
                except Exception as e:
                    self.dbus_items[k] = None
                    print(f"Could not find DBUS Item - {v}")
                    print(e, flush=True)



if __name__ == "__main__":
    try:
        with open(join(dirname(__file__), "version")) as f_version:
            version = f_version.readline()
        print("\n\n****************************************\n")
        print(f"Running {__file__}.py \t{version}", flush=True)
        print("\n****************************************\n\n")

        print("Running now!", flush=True)
        g = IO_Tester()
        print(g)
        g.run()  # global dbusObjects  #  # print(__file__ + " starting up")
    except Exception as e:
        print("Exception Raised", flush=True)
        print(e, flush=True)
        print("Restart Required, Going Down in 5s!", flush=True)
        g.store_state()
        sleep(5)
        raise
# # Have a mainloop, so we can send/receive asynchronous calls to and from dbus  # DBusGMainLoop(set_as_default=True)
