"""Anonymous WAMP operations shared by WAMP-feed source collectors."""

import asyncio
import json
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol, cast

from websockets.exceptions import WebSocketException

from .protocol import WampFeedProtocolError, decode_frame, encode_hello

_MISSING = object()


class WampConnection(Protocol):
    """The small async transport surface required by the WAMP client."""

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class Invocation:
    """A WAMP invocation received for a procedure registered by this client."""

    request_id: int
    registration_id: int
    details: Mapping[str, object]
    args: tuple[object, ...]
    kwargs: Mapping[str, object]


class WampFeedClient:
    """Correlate anonymous public WAMP requests over one connection."""

    def __init__(self, connection: WampConnection) -> None:
        self._connection = connection
        self._next_request_id = 1
        self._pending_calls: dict[int, asyncio.Future[object]] = {}
        self._pending_registers: dict[int, asyncio.Future[int]] = {}
        self._pending_subscribes: dict[int, asyncio.Future[int]] = {}
        self._invocations: asyncio.Queue[Invocation] = asyncio.Queue()
        self._events: asyncio.Queue[object] = asyncio.Queue()
        self._subscription_ids: set[int] = set()
        self._welcome: asyncio.Future[int] | None = None
        self._terminal_signal: asyncio.Future[WampFeedProtocolError] = (
            asyncio.get_running_loop().create_future()
        )
        self._terminal_error: WampFeedProtocolError | None = None
        self._peer_terminated = False
        self._closed = False
        self._reader = asyncio.create_task(self._read_frames())

    async def hello(self, realm: str) -> int:
        """Perform the anonymous HELLO/WELCOME handshake and return the session ID."""
        self._ensure_active()
        if self._welcome is not None:
            raise RuntimeError("WAMP HELLO has already been sent")
        welcome: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self._welcome = welcome
        try:
            await self._connection.send(encode_hello(realm))
        except BaseException:
            self._welcome = None
            welcome.cancel()
            raise
        return await welcome

    async def call(
        self,
        procedure: str,
        args: Sequence[object] = (),
        kwargs: Mapping[str, object] = (),  # type: ignore[assignment]
    ) -> object:
        """Call a public WAMP procedure and return its matching RESULT value."""
        self._ensure_active()
        request_id = self._new_request_id()
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        message = [48, request_id, {}, procedure, list(args), dict(kwargs)]
        return await self._send_and_wait(self._pending_calls, request_id, future, message)

    async def register(self, procedure: str) -> int:
        """Register a public callback procedure and return its registration ID."""
        self._ensure_active()
        request_id = self._new_request_id()
        future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        message = [64, request_id, {}, procedure]
        return await self._send_and_wait(self._pending_registers, request_id, future, message)

    async def subscribe(self, topic: str) -> int:
        """Subscribe to a public WAMP topic and return its subscription ID."""
        self._ensure_active()
        request_id = self._new_request_id()
        future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        message = [32, request_id, {}, topic]
        return await self._send_and_wait(self._pending_subscribes, request_id, future, message)

    async def recv_invocation(self) -> Invocation:
        """Wait for the next invocation routed by the reader task."""
        return await self._recv_queued(self._invocations)

    async def recv_event(self) -> object:
        """Wait for the next public WAMP EVENT payload value."""
        return await self._recv_queued(self._events)

    async def yield_invocation(self, invocation: Invocation, result: object) -> None:
        """Return one result value for an invocation without retaining frame data."""
        self._ensure_active()
        await self._connection.send(self._encode_message([70, invocation.request_id, {}, [result]]))

    async def close(self) -> None:
        """Send GOODBYE, stop reading, and release all pending operations."""
        if self._closed:
            return
        self._closed = True
        try:
            if not self._peer_terminated:
                with suppress(OSError, WebSocketException):
                    await self._connection.send(self._encode_message([6, {}, "wamp.close.normal"]))
        finally:
            try:
                self._reader.cancel()
                with suppress(asyncio.CancelledError, OSError, WebSocketException):
                    await self._reader
            finally:
                self._cancel_pending()
                await self._connection.close()

    async def _send_and_wait[T](
        self,
        pending: dict[int, asyncio.Future[T]],
        request_id: int,
        future: asyncio.Future[T],
        message: list[object],
    ) -> T:
        encoded = self._encode_message(message)
        pending[request_id] = future
        try:
            await self._connection.send(encoded)
        except BaseException:
            pending.pop(request_id, None)
            future.cancel()
            raise
        return await future

    async def _recv_queued[T](self, queue: asyncio.Queue[T]) -> T:
        self._ensure_active()
        queued = asyncio.create_task(queue.get())
        try:
            done, _ = await asyncio.wait(
                (queued, self._terminal_signal), return_when=asyncio.FIRST_COMPLETED
            )
            if self._terminal_signal in done:
                raise self._terminal_signal.result()
            return queued.result()
        finally:
            if not queued.done():
                queued.cancel()
                with suppress(asyncio.CancelledError):
                    await queued

    def _new_request_id(self) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        return request_id

    def _ensure_active(self) -> None:
        if self._terminal_error is not None:
            raise self._terminal_error
        if self._closed:
            raise WampFeedProtocolError("WAMP client is closed")

    @staticmethod
    def _encode_message(message: list[object]) -> str:
        return json.dumps(message, ensure_ascii=False, separators=(",", ":"))

    async def _read_frames(self) -> None:
        try:
            while True:
                await self._route_frame(decode_frame(await self._connection.recv()))
        except asyncio.CancelledError:
            raise
        except WampFeedProtocolError as error:
            self._fail_pending(error)
        except (OSError, WebSocketException):
            self._fail_pending(WampFeedProtocolError("WAMP transport disconnected"))

    async def _route_frame(self, frame: list[object]) -> None:
        message_type = frame[0]
        assert isinstance(message_type, int)
        if message_type == 2:
            self._route_welcome(frame)
        elif message_type == 3:
            self._route_abort(frame)
        elif message_type == 6:
            await self._route_goodbye(frame)
        elif message_type == 50:
            self._route_result(frame)
        elif message_type == 65:
            self._route_registered(frame)
        elif message_type == 33:
            self._route_subscribed(frame)
        elif message_type == 68:
            self._route_invocation(frame)
        elif message_type == 36:
            self._route_event(frame)
        elif message_type == 8:
            self._route_error(frame)

    def _route_abort(self, frame: list[object]) -> None:
        self._required_mapping(frame, 1, "ABORT")
        self._required_text(frame, 2, "ABORT")
        self._peer_terminated = True
        raise WampFeedProtocolError("peer ABORT message type 3")

    async def _route_goodbye(self, frame: list[object]) -> None:
        self._required_mapping(frame, 1, "GOODBYE")
        self._required_text(frame, 2, "GOODBYE")
        self._peer_terminated = True
        with suppress(OSError, WebSocketException):
            await self._connection.send(self._encode_message([6, {}, "wamp.close.goodbye_and_out"]))
        raise WampFeedProtocolError("peer GOODBYE message type 6")

    def _route_welcome(self, frame: list[object]) -> None:
        if self._welcome is None or self._welcome.done():
            raise WampFeedProtocolError("unexpected WELCOME message type 2")
        session_id = self._required_int(frame, 1, "WELCOME", "session ID")
        self._required_mapping(frame, 2, "WELCOME", session_id)
        self._welcome.set_result(session_id)

    def _route_result(self, frame: list[object]) -> None:
        request_id = self._required_int(frame, 1, "RESULT", "request ID")
        self._required_mapping(frame, 2, "RESULT", request_id)
        args = self._optional_args(frame, 3, "RESULT", request_id)
        kwargs = frame[4] if len(frame) > 4 else _MISSING
        if kwargs is not _MISSING and not self._valid_result_value(kwargs):
            raise WampFeedProtocolError(
                f"invalid RESULT message type request ID {request_id}: kwargs"
            )
        future = self._pending_calls.pop(request_id, None)
        if future is not None and not future.done():
            if args:
                result: object = args[0] if len(args) == 1 else tuple(args)
            elif kwargs is not _MISSING:
                result = kwargs
            else:
                result = ()
            future.set_result(result)

    @staticmethod
    def _valid_result_value(value: object) -> bool:
        return value is None or isinstance(value, (Mapping, bool, int, float, str))

    def _route_registered(self, frame: list[object]) -> None:
        request_id = self._required_int(frame, 1, "REGISTERED", "request ID")
        registration_id = self._required_int(frame, 2, "REGISTERED", "registration ID", request_id)
        self._resolve_identifier(self._pending_registers, request_id, registration_id)

    def _route_subscribed(self, frame: list[object]) -> None:
        request_id = self._required_int(frame, 1, "SUBSCRIBED", "request ID")
        subscription_id = self._required_int(frame, 2, "SUBSCRIBED", "subscription ID", request_id)
        if self._resolve_identifier(self._pending_subscribes, request_id, subscription_id):
            self._subscription_ids.add(subscription_id)

    def _route_invocation(self, frame: list[object]) -> None:
        request_id = self._required_int(frame, 1, "INVOCATION", "request ID")
        registration_id = self._required_int(frame, 2, "INVOCATION", "registration ID", request_id)
        details = self._required_mapping(frame, 3, "INVOCATION", request_id)
        args = self._optional_args(frame, 4, "INVOCATION", request_id)
        kwargs = self._optional_mapping(frame, 5, "INVOCATION", request_id)
        self._invocations.put_nowait(
            Invocation(request_id, registration_id, dict(details), tuple(args), dict(kwargs))
        )

    def _route_event(self, frame: list[object]) -> None:
        subscription_id = self._required_int(frame, 1, "EVENT", "subscription ID")
        if subscription_id not in self._subscription_ids:
            return
        publication_id = self._required_int(frame, 2, "EVENT", "publication ID")
        self._required_mapping(frame, 3, "EVENT", publication_id)
        args = self._optional_args(frame, 4, "EVENT", publication_id)
        self._optional_mapping(frame, 5, "EVENT", publication_id)
        self._events.put_nowait(args[0] if len(args) == 1 else tuple(args))

    def _route_error(self, frame: list[object]) -> None:
        request_type = self._required_int(frame, 1, "ERROR", "request type")
        request_id = self._required_int(frame, 2, "ERROR", "request ID")
        error = WampFeedProtocolError(
            f"WAMP ERROR for message type {request_type} request ID {request_id}"
        )
        if request_type == 48:
            call_future = self._pending_calls.pop(request_id, None)
            if call_future is not None and not call_future.done():
                call_future.set_exception(error)
        elif request_type == 64:
            register_future = self._pending_registers.pop(request_id, None)
            if register_future is not None and not register_future.done():
                register_future.set_exception(error)
        elif request_type == 32:
            subscribe_future = self._pending_subscribes.pop(request_id, None)
            if subscribe_future is not None and not subscribe_future.done():
                subscribe_future.set_exception(error)
        else:
            return

    @staticmethod
    def _required_int(
        frame: list[object],
        index: int,
        message_name: str,
        field_name: str,
        request_id: int | None = None,
    ) -> int:
        value = frame[index] if index < len(frame) else None
        if not isinstance(value, int) or isinstance(value, bool):
            suffix = "unknown" if request_id is None else str(request_id)
            raise WampFeedProtocolError(
                f"invalid {message_name} message type request ID {suffix}: {field_name}"
            )
        return value

    @staticmethod
    def _required_mapping(
        frame: list[object], index: int, message_name: str, request_id: int | None = None
    ) -> Mapping[str, object]:
        value = frame[index] if index < len(frame) else None
        if not isinstance(value, Mapping):
            suffix = "unknown" if request_id is None else str(request_id)
            raise WampFeedProtocolError(
                f"invalid {message_name} message type request ID {suffix}: details"
            )
        return cast(Mapping[str, object], value)

    @staticmethod
    def _required_text(frame: list[object], index: int, message_name: str) -> str:
        value = frame[index] if index < len(frame) else None
        if not isinstance(value, str) or not value:
            raise WampFeedProtocolError(f"invalid {message_name} message type: reason")
        return value

    @staticmethod
    def _optional_args(
        frame: list[object], index: int, message_name: str, request_id: int
    ) -> list[object]:
        if index >= len(frame):
            return []
        value = frame[index]
        if not isinstance(value, list):
            raise WampFeedProtocolError(
                f"invalid {message_name} message type request ID {request_id}: args"
            )
        return cast(list[object], value)

    @staticmethod
    def _optional_mapping(
        frame: list[object], index: int, message_name: str, request_id: int
    ) -> Mapping[str, object]:
        if index >= len(frame):
            return {}
        value = frame[index]
        if not isinstance(value, Mapping):
            raise WampFeedProtocolError(
                f"invalid {message_name} message type request ID {request_id}: kwargs"
            )
        return cast(Mapping[str, object], value)

    @staticmethod
    def _resolve_identifier(
        pending: dict[int, asyncio.Future[int]], request_id: int, identifier: int
    ) -> bool:
        future = pending.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(identifier)
            return True
        return False

    def _fail_pending(self, error: WampFeedProtocolError) -> None:
        self._terminal_error = error
        if not self._terminal_signal.done():
            self._terminal_signal.set_result(error)
        if self._welcome is not None and not self._welcome.done():
            self._welcome.set_exception(error)
        for pending in (self._pending_calls, self._pending_registers, self._pending_subscribes):
            for future in pending.values():
                if not future.done():
                    future.set_exception(error)
            pending.clear()

    def _cancel_pending(self) -> None:
        if self._welcome is not None and not self._welcome.done():
            self._welcome.cancel()
        for pending in (self._pending_calls, self._pending_registers, self._pending_subscribes):
            for future in pending.values():
                if not future.done():
                    future.cancel()
            pending.clear()
