import asyncio
from asyncio import Event, create_task, sleep, wait_for
from asyncio.locks import Lock
from datetime import datetime, timedelta
import logging
import re

import asyncssh

OK_RESPONSE = re.compile("^\\+OK ?(.*)\r\n")
VALUE_REGEX = re.compile('"value":(.*)')
SUBSCRIPTION_REGEX = re.compile('^! "publishToken":"([^"]+)" "value":(.*)\r\n')


class Tesira:
    def __init__(
        self, conn: asyncssh.SSHClientConnection, process, subscription_process
    ):
        self._conn = conn
        self._process = process
        self._subscription_process = subscription_process
        self._subscription_callbacks = {}
        self._subscription_process_lock = Lock()
        self._subscription_process_event = Event()
        create_task(self.subscription_listen())

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
        subscription_name = f"{instance_id}_{attribute}"
        self._subscription_process_event.set()
        try:
            async with self._subscription_process_lock:
                if subscription_name in self._subscription_callbacks:
                    raise ValueError(
                        f"Subscription already exists: {subscription_name}"
                    )

                self._subscription_callbacks[subscription_name] = callback
                self._subscription_process.stdin.write(
                    f"{instance_id} subscribe {attribute} {subscription_name}\r\n"
                )
                # todo confirm successful subscription
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
                return process

            await sleep(0.5)
        print(response_accumulator)
        raise Exception("device probably isnt a tesira")

    @classmethod
    async def new(cls, ip, user):
        try:
            conn = await asyncssh.connect(
                ip, username=user, password="", client_keys=None, known_hosts=None
            )
            process = await cls._get_process(conn)
            subscription_process = await cls._get_process(conn)
            return cls(conn, process, subscription_process)
        except ValueError as e:
            print(f"Example of a timeout occurrence: {e}")

    async def serial_number(self):
        self._process.stdin.write("DEVICE get serialNumber\r\n")
        response_accumulator = []
        while len(response_accumulator) < 10:
            response = await wait_for(
                self._process.stdout.readline(), timeout=0.5
            )  # todo handle timeout
            response_accumulator.append(response)
            print(response_accumulator)
            if response.split(" ")[0] == "+OK":
                break
            response = None
        if response is None:
            raise ValueError("no valid response " + str(response_accumulator))
        val = int(response.split(":")[1][1:-3])
        return val

    async def _send_command(self, command):
        self._process.stdin.write(command + "\r\n")
        response_accumulator = []
        while len(response_accumulator) < 5:
            try:
                response = await wait_for(
                    self._process.stdout.readline(), timeout=0.5
                )  # todo handle timeout
            except TimeoutError as e:
                raise ValueError("Recieved so far " + str(response_accumulator)) from e
            response_accumulator.append(response)
            if match := re.search(OK_RESPONSE, response):
                return match.group(1)
            response = None
        if response is None:
            raise ValueError(
                f"failed to send command {command} " + str(response_accumulator)
            )

    async def select_source(self, instance_id, source):
        await self._send_command(f"{instance_id} set sourceSelection {source}")

    @staticmethod
    def parse_value(value_str):
        if match := re.search(VALUE_REGEX, value_str):
            return match.group(1)
        raise ValueError("Couldn't parse " + value_str)

    async def sources(self, instance_id):
        source_count = int(
            self.parse_value(await self._send_command(f"{instance_id} get numSources"))
        )
        source_map = {}
        for source_number in range(1, source_count + 1):
            source_name = self.parse_value(
                await self._send_command(f"{instance_id} get label {source_number}")
            )[1:-1]
            source_map[source_name] = source_number
        return source_map

    async def set_volume(self, instance_id, volume):
        await self._send_command(f"{instance_id} set outputLevel {volume}")

    async def set_mute(self, instance_id, mute):
        await self._send_command(f"{instance_id} set outputMute {str(mute).lower()}")
