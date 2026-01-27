from machine import UART, Pin
import time

class ESPC3:
    STATUS_APCONNECTED = 2
    STATUS_SOCKETOPEN = 3
    STATUS_SOCKETCLOSED = 4
    STATUS_NOTCONNECTED = 5

    MODE_STATION = 1
    MODE_SOFTAP = 2
    MODE_SOFTAPSTATION = 3

    def __init__(self,
                 uart_id=1,
                 tx_pin=8,
                 rx_pin=9,
                 baud_rate=115200,
                 tx_buffer=1024,
                 rx_buffer=2048,
                 debug=False):
        """ Initializes the UART for the ESP32C3 module """
        self._debug = debug
        self._ip = None  # Variable to store the IP
        
        try:
            self._uart = UART(uart_id,
                              baudrate=baud_rate,
                              tx=Pin(tx_pin),
                              rx=Pin(rx_pin),
                              txbuf=tx_buffer,
                              rxbuf=rx_buffer)
            if self._debug:
                print("UART initialized successfully.")
        except Exception as e:
            print("Error initializing UART:", e)
            self._uart = None
            
    def send(self, at, timeout=20, retries=3):
        """ Sends an AT command, checks that we got an OK response,
            and then returns the response text.
        """
        for _ in range(retries):
            if self._debug:
                print("tx ---> ", at)
            
            self._uart.write(bytes(at, "utf-8"))
            self._uart.write(b"\x0d\x0a")  # Send CR+LF
            stamp = time.time()
            response = b""
            
            while (time.time() - stamp) < timeout:
                if self._uart.any():
                    response += self._uart.read(1)
                    if response[-4:] == b"OK\r\n":
                        break
                    if response[-7:] == b"ERROR\r\n":
                        break
            
            if self._debug:
                print("<--- rx ", response)

            if response[-4:] == b"OK\r\n":
                return response[:-4]  # Return the response without 'OK'
            
            time.sleep(1)  # Wait before retrying
        raise Exception("No OK response to " + at)
    
    def enable_ipv6(self):
        """ Enables the use of IPv6 on the ESP32C3 module. """
        self.send("AT+CIPV6=1", timeout=3)
        print("IPv6 enabled.")
    
    def ping(self, host):
        """ Pings the given IP or hostname, returns the time in ms or None on failure """
        
        # Make sure to enable IPv6 if necessary
        self.enable_ipv6()  # Call the function to enable IPv6
        
        reply = self.send('AT+PING="%s"' % host.strip('"'), timeout=5)
        for line in reply.split(b"\r\n"):
            if line and line.startswith(b"+"):
                try:
                    if line[1:5] == b"PING":
                        return int(line[6:])
                    return int(line[1:])
                except ValueError:
                    return None
        raise RuntimeError("Couldn't ping")

    def connect(self, secrets):
        """ Tries to connect to an access point with the details in
            the passed 'secrets' dictionary.
        """
        retries = 3
        while retries > 0:
            try:
                if not self.is_connected:
                    self.join_ap(secrets["ssid"], secrets["password"])
                return True
            except RuntimeError as exp:
                print("Failed to connect, retrying\n", exp)
                retries -= 1
                time.sleep(2)  # Wait before retrying

    def parse_cwjap_response(self, reply):
        """ Parses the response from the +CWJAP? command to extract all values:
            The response comes in the form +CWJAP:<ssid>,<bssid>,<channel>,<rssi>,<pci_en>,<reconn_interval>,<listen_interval>,<scan_mode>,<pmf>
        """
        
        replies = reply.split(b"\r\n")
        
        for line in replies:
            if line.startswith(b"+CWJAP:"):
                # Remove the prefix "+CWJAP:" and split the values
                line = line[7:].split(b",")
                
                # Process and store each field
                parsed_values = {
                    "ssid": str(line[0], "utf-8").strip('"'),          # SSID
                    "bssid": str(line[1], "utf-8").strip('"'),         # BSSID
                    "channel": int(line[2]),                           # Canal
                    "rssi": int(line[3]),                              # RSSI
                    "pci_en": int(line[4]),                            # PCI enabled
                    "reconn_interval": int(line[5]),                   # Reconnection interval
                    "listen_interval": int(line[6]),                   # Listen interval
                    "scan_mode": int(line[7]),                         # Scan mode
                    "pmf": int(line[8])                                # PMF (Frame Management Protection)
                }
                return parsed_values
        return None
    
    def join_ap(self, ssid, password):
        """ Tries to join an access point by name and password. """
        if self.mode != self.MODE_STATION:
            self.mode = self.MODE_STATION
        
        # If already connected to the specified network, return the information directly
        cwjap_reply = self.send("AT+CWJAP?", timeout=10)
        parsed_cwjap = self.parse_cwjap_response(cwjap_reply)
        
        if parsed_cwjap and parsed_cwjap['ssid'] == ssid:
            return parsed_cwjap  # We are already connected, return the information
    
        # Try to connect to the AP
        for _ in range(3):
            reply = self.send(
                f'AT+CWJAP="{ssid}","{password}"', timeout=15, retries=3
            )
            
            # Check if the connection was successful
            if b"WIFI CONNECTED" in reply and b"WIFI GOT IP" in reply:
                # Get connection details
                cwjap_reply = self.send("AT+CWJAP?", timeout=10)
                parsed_cwjap = self.parse_cwjap_response(cwjap_reply)
                if parsed_cwjap:
                    return parsed_cwjap
        # If it fails after 3 attempts, raise an exception
        raise Exception("Could not connect to the network.")

    @property
    def is_connected(self):
        """ Checks if we are connected to an access point. """
        state = self.status
        return state in (self.STATUS_APCONNECTED, self.STATUS_SOCKETOPEN, self.STATUS_SOCKETCLOSED)

    @property
    def status(self):
        """ The state of the IP connection. """
        replies = self.send("AT+CIPSTATUS", timeout=5).split(b"\r\n")
        for reply in replies:
            if reply.startswith(b"STATUS:"):
                return int(reply[7:8])
        return None

    @property
    def remote_AP(self):
        """ The name of the access point we are connected to, as a string. """
        if self.status != self.STATUS_APCONNECTED:
            return [None] * 4

        replies = self.send("AT+CWJAP?", timeout=10).split(b"\r\n")
        for reply in replies:
            if reply.startswith(b"+CWJAP:"):
                reply = reply[7:].split(b",")
                print("Response from +CWJAP:", reply)  # Add debugging here
                try:
                    # Convert each value to the corresponding type
                    ssid = str(reply[0], "utf-8").strip('"')  # SSID as string
                    bssid = str(reply[1], "utf-8")  # BSSID as string
                    channel = int(reply[2])  # Channel as integer
                    rssi = int(reply[3])  # RSSI as integer
                    # Add more fields if necessary according to the format
                    return [ssid, bssid, channel, rssi]
                except (ValueError, IndexError) as e:
                    print("Error parsing the response from +CWJAP:", e)
                    return [None] * 4  # Return default values in case of error
                
        return [None] * 4

    @property
    def mode(self):
        replies = self.send("AT+CWMODE?", timeout=5).split(b"\r\n")
        for reply in replies:
            if reply.startswith(b"+CWMODE:"):
                return int(reply[8:])
        raise RuntimeError("Bad response to CWMODE?")

    @mode.setter
    def mode(self, mode):
        """ Mode selection: can be MODE_STATION, MODE_SOFTAP, or MODE_SOFTAPSTATION. """
        if mode not in (1, 2, 3):
            raise RuntimeError("Invalid Mode")
        self.send("AT+CWMODE=%d" % mode, timeout=3)

    @property
    def local_ip(self):
        """ Our local IP address as a dotted string. """
        reply = self.send("AT+CIFSR").strip(b"\r\n")
        for line in reply.split(b"\r\n"):
            if line.startswith(b'+CIFSR:STAIP,"'):
                return str(line[14:-1], "utf-8")
        raise RuntimeError("Couldn't find IP address")
    
    def get_ip(self):
        try:
            response = self.send("AT+CIFSR")
            # Ensure the response is of bytes type and convert it to string
            response = response.decode('utf-8')  # Convert bytes to string
            # Search for the line that contains the IP
            lines = response.splitlines()
            for line in lines:
                if "CIFSR:STAIP" in line:
                    #Extract the IP
                    ip_start = line.find('"') + 1
                    ip_end = line.rfind('"')
                    ip_address = line[ip_start:ip_end]
                    return ip_address
        except Exception as e:
            print(f"Error getting the IP: {e}")
        return None

    def get_mac_address(self):
        """ Gets the MAC address of the ESP32C3. """
        reply = self.send("AT+CIFSR").strip(b"\r\n")
        for line in reply.split(b"\r\n"):
            if line.startswith(b'+CIFSR:STAMAC,"'):
                return str(line[15:-1], "utf-8")  # Returns the MAC address
        raise RuntimeError("Couldn't find MAC address")
    
    def get_AP(self, retries=3):
        for _ in range(retries):
            try:
                if self.mode != self.MODE_STATION:
                    self.mode = self.MODE_STATION
                # Send AT command to scan for access points
                scan = self.send("AT+CWLAP", timeout=5).split(b"\r\n")
            except RuntimeError:
                continue
            
            routers = []
            
            for line in scan:
                if line.startswith(b"+CWLAP:("):
                    # Parse the response line
                    line = line[8:-1].split(b",")
                    router = ["Unknown"] * 12 # Initialize with default values
                    
                    for i, val in enumerate(line):  # Ignore the first value
                        # Convert the value to string and handle it properly
                        try:
                            if i == 0:  # Encryption method
                                encryption_method = int(val)
                                encryption_mapping = {
                                    0: "Ninguna", 1: "WEP", 2: "WPA-PSK",
                                    3: "WPA2-PSK", 4: "WPA/WPA2-PSK", 5: "WPA2 Enterprise",
                                    6: "WPA3-PSK", 7: "WPA2/WPA3-PSK", 8: "WAPI-PSK", 9: "OWE"
                                }
                                router[0] = encryption_mapping.get(encryption_method, "Unknown")
                            elif i == 1:  # SSID
                                router[1] = str(val, "utf-8").strip('"') # SSID as string
                            elif i == 2:  # RSSI
                                router[2] = int(val)  # RSSI as integer
                            elif i == 3:  # MAC
                                router[3] = str(val, "utf-8")  # MAC as string
                            elif i == 4:  # Channel
                                router[4] = int(val)  # Channel as integer
                            elif i == 5:  # Scan type (can be ignored)
                                router[5] = int(val)  # Can store if necessary
                            elif i == 6:  # Minimum scan time
                                router[6] = int(val)  # Can store if necessary
                            elif i == 7:  # Maximum scan time
                                router[7] = int(val)
                            elif i == 8:  # Pair encryption
                                router[8] = int(val)  # Can store if necessary
                            elif i == 9:  # Group encryption
                                router[9] = int(val)  # Can store if necessary
                            elif i == 10:  # Bands (b/g/n)
                                router[10] = int(val)  # Can store if necessary
                            elif i == 11:  # WPS
                                router[11] = int(val)  # Can store if necessary    
                        except ValueError:
                            # If it can't be converted, store as string
                            router[i] =str(val, "utf-8").strip('"')
                    routers.append(router)
            return routers
        return []
