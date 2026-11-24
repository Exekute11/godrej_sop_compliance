"""Thread-safe serial output service for final SOP results."""

import logging
import threading
import time

try:
    import serial
except ImportError:
    serial = None


log = logging.getLogger("sop.serial")


class SerialService:
    """
    Serial sender for the final SOP result.

    Default ASCII protocol:
        PASS -> b"0\\r\\n"
        FAIL -> b"1\\r\\n"

    The COM port is opened only when serial output is enabled.
    """

    def __init__(self, config=None):
        self.config = dict(config or {})

        self.enabled = bool(
            self.config.get("enabled", False)
        )

        self.port = str(
            self.config.get("port", "COM3")
        )

        self.baudrate = int(
            self.config.get("baudrate", 9600)
        )

        self.timeout = float(
            self.config.get("timeout_s", 1.0)
        )

        self.write_timeout = float(
            self.config.get("write_timeout_s", 1.0)
        )

        self.pass_value = str(
            self.config.get("pass_value", "0")
        )

        self.fail_value = str(
            self.config.get("fail_value", "1")
        )

        self.line_ending = self._decode_line_ending(
            self.config.get("line_ending", "\\r\\n")
        )

        self._serial = None
        self._lock = threading.RLock()

    @staticmethod
    def _decode_line_ending(value):
        """
        Convert configuration text such as '\\r\\n' into actual
        carriage-return and line-feed characters.
        """

        value = str(value)

        return (
            value
            .replace("\\r", "\r")
            .replace("\\n", "\n")
        )

    @property
    def connected(self):
        return bool(
            self._serial
            and self._serial.is_open
        )

    def open(self):
        with self._lock:
            if not self.enabled:
                return False

            if serial is None:
                log.error(
                    "PySerial is not installed. "
                    "Run: pip install pyserial"
                )
                return False

            if self.connected:
                return True

            try:
                self._serial = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=self.timeout,
                    write_timeout=self.write_timeout,
                )

                # Allow USB serial adapters to stabilize.
                time.sleep(0.15)

                self._serial.reset_output_buffer()

                return True

            except Exception as exc:
                self._serial = None

                log.error(
                    "Unable to open %s at %s baud: %s",
                    self.port,
                    self.baudrate,
                    exc,
                )

                return False

    def send_value(self, value):
        """
        Send one ASCII value followed by the configured line ending.

        Example:
            value="0" -> b"0\\r\\n"
            value="1" -> b"1\\r\\n"
        """

        if not self.enabled:
            return False

        payload = (
            str(value) + self.line_ending
        ).encode("ascii")

        with self._lock:
            if not self.connected and not self.open():
                return False

            try:
                self._serial.write(payload)
                self._serial.flush()

                return True

            except Exception as exc:
                log.error(
                    "Serial write failed on %s: %s",
                    self.port,
                    exc,
                )

                self.close()
                return False

    def send_pass(self):
        return self.send_value(
            self.pass_value
        )

    def send_fail(self):
        return self.send_value(
            self.fail_value
        )

    def close(self):
        with self._lock:
            if self._serial is not None:
                try:
                    if self._serial.is_open:
                        self._serial.close()
                except Exception:
                    pass

            self._serial = None