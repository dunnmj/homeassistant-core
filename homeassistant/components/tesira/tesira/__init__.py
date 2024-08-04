import asyncio
from asyncio import Event, create_task, sleep, wait_for
from asyncio.locks import Lock
from datetime import datetime, timedelta
import logging
import re

import asyncssh

OK_RESPONSE = re.compile("^\\+OK ?(.*)\r\n")
OK_RESPONSE_WITH_VALUE = re.compile('^\\+OK ("value".*)\r\n')
VALUE_REGEX = re.compile('"value":(.*)')
SUBSCRIPTION_REGEX = re.compile('^! "publishToken":"([^"]+)" "value":(.*)\r\n')

quote = '"'


class CommandFailedException(Exception):
    pass


class Tesira:
    def __init__(self, ip: str, username: str, password: str):
        self._ip = ip
        self._username = username
        self._password = password

        self._conn = None
        self._process = None
        self._subscription_process = None
        self._subscription_callbacks = {}
        self._subscription_process_lock = Lock()
        self._subscription_process_event = Event()
        self._connection_management_lock = Lock()
        self._subscription_history = []
        self._command_lock = Lock()

    async def subscription_listen(self):
        while True:
            async with self._subscription_process_lock:
                try:
                    response_task = create_task(
                        self._subscription_process.stdout.readline()
                    )
                    event_task = create_task(self._subscription_process_event.wait())
                    done, pending = await asyncio.wait(
                        [response_task, event_task], return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    if response_task not in done:
                        continue
                    response = response_task.result()
                    logging.debug("Received subscription message: %s", response)
                    await self.handle_subscription_message(response)
                except TimeoutError:
                    continue

    async def handle_subscription_message(self, message):
        match = SUBSCRIPTION_REGEX.match(message)
        if not match:
            logging.debug("Invalid subscription message: %s", message)
            return

        subscription_name = match.group(1)
        handler = self._subscription_callbacks.get(subscription_name)
        if handler is None:
            logging.error("No handler for subscription: %s", subscription_name)
            return

        value = match.group(2)
        handler(value)

    async def subscribe(self, instance_id, attribute, callback):
        subscription_name = (
            f"{instance_id.replace(' ', '_')}_{attribute.replace(' ', '_')}"
        )
        self._subscription_process_event.set()
        try:
            async with self._subscription_process_lock:
                if subscription_name in self._subscription_callbacks:
                    raise ValueError(
                        f"Subscription already exists: {subscription_name}"
                    )

                self._subscription_callbacks[subscription_name] = callback
                subscription_command = (
                    f'"{instance_id}" subscribe {attribute} {subscription_name}\r\n'
                )
                self._subscription_process.stdin.write(subscription_command)
                # todo confirm successful subscription
                self._subscription_history.append(subscription_command)
        finally:
            self._subscription_process_event.clear()

    @property
    def conn(self):
        self._conn

    @staticmethod
    async def _get_process(conn):
        process = await conn.create_process(term_type="vt100")
        start = datetime.now()
        response_accumulator = ""
        while datetime.now() - start < timedelta(seconds=30):
            response, _ = process.collect_output()
            response_accumulator += response
            if "Welcome to the Tesira Text Protocol Server..." in response_accumulator:
                process.stdin.write("SESSION set verbose true\r\n")
                process.stdin.write("SESSION set detailedResponse false\r\n")
                for _ in range(6):
                    await wait_for(process.stdout.readline(), timeout=1)
                return process

            await sleep(0.5)
        raise Exception("device probably isnt a tesira")

    @classmethod
    async def new(cls, ip, user, password):
        new_instance = cls(ip, user, password)
        await new_instance.connect()
        new_instance._subscription_task = create_task(
            new_instance.manage_subscription_connection()
        )
        return new_instance

    async def manage_subscription_connection(self):
        while True:
            try:
                await self.connect()
                await self.subscription_listen()
            except asyncssh.ConnectionLost as e:
                logging.error("Error in managing subscription connection: %s", e)
            except OSError as e:
                logging.error("Error re-establishing subscription connection: %s", e)
            await self.invalidate_connection()
            await asyncio.sleep(5)

    async def invalidate_connection(self):
        async with self._connection_management_lock:
            if self._conn is None:
                return
            self._process.close()
            self._subscription_process.close()
            self._conn.close()
            self._conn = None
            self._process = None
            self._subscription_process = None

    async def connect(self):
        async with self._connection_management_lock:
            if self._conn is not None:
                return
            self._conn = await asyncssh.connect(
                self._ip,
                username=self._username,
                password=self._password,
                client_keys=None,
                known_hosts=None,
                keepalive_interval=10,
            )
            self._process = await self._get_process(self._conn)
            self._subscription_process = await self._get_process(self._conn)
            for command in self._subscription_history:
                self._subscription_process.stdin.write(command)

    async def serial_number(self):
        serial = self.parse_value(
            await self._send_command("DEVICE get serialNumber", expects_value=True)
        )
        return int(serial[1:-1])

    async def _send_command(self, command, expects_value=False):
        async with self._command_lock:
            regex = OK_RESPONSE_WITH_VALUE if expects_value else OK_RESPONSE
            self._process.stdin.write(command + "\r\n")
            response_accumulator = []
            while len(response_accumulator) < 5:
                try:
                    response = await wait_for(
                        self._process.stdout.readline(), timeout=5
                    )
                except TimeoutError as e:
                    raise CommandFailedException(
                        "Recieved so far " + str(response_accumulator)
                    ) from e
                response_accumulator.append(response)
                if match := re.search(regex, response):
                    return match.group(1)
                response = None
            if response is None:
                raise CommandFailedException(
                    f"failed to send command {command} " + str(response_accumulator)
                )

    async def select_source(self, instance_id, source):
        await self._send_command(f'"{instance_id}" set sourceSelection {source}')

    @staticmethod
    def parse_value(value_str):
        if match := re.search(VALUE_REGEX, value_str):
            return match.group(1)
        raise ValueError("Couldn't parse " + value_str)

    async def sources(self, instance_id):
        source_count = int(
            self.parse_value(
                await self._send_command(f'"{instance_id}" get numSources')
            )
        )
        source_map = {}
        for source_number in range(1, source_count + 1):
            source_name = self.parse_value(
                await self._send_command(f'"{instance_id}" get label {source_number}')
            )[1:-1]
            source_map[source_name] = source_number
        return source_map

    async def inputs(
        self, instance_id, num_key="numInputs", input_label_key="inputLabel"
    ):
        input_count = int(
            self.parse_value(await self._send_command(f'"{instance_id}" get {num_key}'))
        )
        input_map = {}
        for input_number in range(1, input_count + 1):
            input_name = self.parse_value(
                await self._send_command(
                    f'"{instance_id}" get {input_label_key} {input_number}'
                )
            )[1:-1]
            input_map[input_name] = input_number
        return input_map

    async def set_volume(self, instance_id, volume):
        await self._send_command(f'"{instance_id}" set outputLevel {volume}')

    async def set_mute(self, instance_id, mute):
        await self._send_command(f'"{instance_id}" set outputMute {str(mute).lower()}')
