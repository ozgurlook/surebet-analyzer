import asyncio
import json
import traceback

import pytest
from websockets.exceptions import WebSocketException

from surebet.sources.wamp_feed.client import Invocation, WampFeedClient
from surebet.sources.wamp_feed.protocol import WampFeedProtocolError


class FakeConnection:
    """In-memory WAMP transport with only ephemeral, sanitized frames."""

    def __init__(self) -> None:
        self.replies: asyncio.Queue[str | BaseException] = asyncio.Queue()
        self.sent: list[list[object]] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        reply = await self.replies.get()
        if isinstance(reply, BaseException):
            raise reply
        return reply

    async def close(self) -> None:
        self.closed = True


class GoodbyeFailingConnection(FakeConnection):
    async def send(self, message: str) -> None:
        if json.loads(message)[0] == 6:
            raise WebSocketException("peer is already closed")
        await super().send(message)


async def wait_for_sent(connection: FakeConnection, count: int) -> None:
    for _ in range(100):
        if len(connection.sent) >= count:
            return
        await asyncio.sleep(0)
    pytest.fail(f"expected {count} sent WAMP messages")


@pytest.mark.asyncio
async def test_call_correlates_out_of_order_results_by_request_id() -> None:
    """Using arrival order would deliver a public procedure's result to the wrong caller."""
    connection = FakeConnection()
    client = WampFeedClient(connection)
    try:
        first = asyncio.create_task(client.call("/sports#first", ["one"]))
        second = asyncio.create_task(client.call("/sports#second", ["two"]))
        await wait_for_sent(connection, 2)
        requests = {message[3]: message[1] for message in connection.sent}

        await connection.replies.put(json.dumps([50, requests["/sports#second"], {}, ["second"]]))
        await connection.replies.put(json.dumps([50, requests["/sports#first"], {}, ["first"]]))

        assert await first == "first"
        assert await second == "second"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_call_returns_result_kwargs_when_args_are_empty() -> None:
    """The WAMP feed puts normalized result envelopes in the WAMP kwargs slot."""
    connection = FakeConnection()
    client = WampFeedClient(connection)
    try:
        call = asyncio.create_task(client.call("/sports#initialDump"))
        await wait_for_sent(connection, 1)
        request_id = connection.sent[0][1]
        envelope = {"format": "json", "messageType": "RESULT", "records": [], "version": "1"}
        await connection.replies.put(json.dumps([50, request_id, {}, [], envelope]))

        assert await call == envelope
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_call_accepts_scalar_result_kwargs_for_noop_bootstrap() -> None:
    """The public configureFonts call returns a scalar WAMP kwargs value."""
    connection = FakeConnection()
    client = WampFeedClient(connection)
    try:
        call = asyncio.create_task(client.call("/sports#configureFonts"))
        await wait_for_sent(connection, 1)
        request_id = connection.sent[0][1]
        await connection.replies.put(json.dumps([50, request_id, {}, [], "102"]))

        assert await call == "102"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_call_rejects_composite_result_kwargs_without_exposing_value() -> None:
    """Malformed composite kwargs payloads fail with a sanitized protocol error."""
    connection = FakeConnection()
    client = WampFeedClient(connection)
    try:
        call = asyncio.create_task(client.call("/sports#configureFonts"))
        await wait_for_sent(connection, 1)
        request_id = connection.sent[0][1]
        await connection.replies.put(json.dumps([50, request_id, {}, [], ["secret"]]))

        with pytest.raises(WampFeedProtocolError, match="RESULT.*kwargs") as caught:
            await call
        assert "secret" not in str(caught.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_register_and_subscribe_return_router_ids() -> None:
    """Dropping REGISTERED/SUBSCRIBED IDs would prevent later WAMP routing."""
    connection = FakeConnection()
    client = WampFeedClient(connection)
    try:
        register = asyncio.create_task(client.register("/sports/1001/tr/update"))
        subscribe = asyncio.create_task(client.subscribe("/registrationDismissed"))
        await wait_for_sent(connection, 2)
        requests = {message[0]: message[1] for message in connection.sent}

        await connection.replies.put(json.dumps([65, requests[64], 71]))
        await connection.replies.put(json.dumps([33, requests[32], 72]))

        assert await register == 71
        assert await subscribe == 72
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_invalid_result_raises_sanitized_protocol_error() -> None:
    """Treating a malformed RESULT as a value would make later parsing unsafe."""
    connection = FakeConnection()
    client = WampFeedClient(connection)
    try:
        call = asyncio.create_task(client.call("/sports#bootstrap"))
        await wait_for_sent(connection, 1)
        request_id = connection.sent[0][1]

        await connection.replies.put(json.dumps([50, request_id, "not-details", ["ignored"]]))

        with pytest.raises(WampFeedProtocolError, match="RESULT.*request ID") as caught:
            await call
        assert "ignored" not in str(caught.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_transport_failure_fails_all_pending_requests_without_peer_text() -> None:
    """Cancelling a dropped public call would leak CancelledError past the collector boundary."""
    connection = FakeConnection()
    client = WampFeedClient(connection)
    try:
        call = asyncio.create_task(client.call("/sports#bootstrap"))
        await wait_for_sent(connection, 1)
        await connection.replies.put(OSError("private-token-must-never-appear"))

        with pytest.raises(WampFeedProtocolError, match="transport disconnected") as caught:
            await asyncio.wait_for(call, timeout=0.1)
        assert "private-token-must-never-appear" not in str(caught.value)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_frame",
    [
        [3, {}, "wamp.error.system_shutdown"],
        [6, {}, "wamp.close.normal"],
    ],
)
async def test_terminal_router_messages_wake_event_and_invocation_consumers(
    terminal_frame: list[object],
) -> None:
    """Leaving queue consumers blocked after router termination would stall collection cleanup."""
    connection = FakeConnection()
    client = WampFeedClient(connection)
    try:
        event = asyncio.create_task(client.recv_event())
        invocation = asyncio.create_task(client.recv_invocation())
        await connection.replies.put(json.dumps(terminal_frame))

        for consumer in (event, invocation):
            with pytest.raises(WampFeedProtocolError):
                await asyncio.wait_for(consumer, timeout=0.1)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_transport_loss_wakes_event_and_invocation_consumers_without_peer_text() -> None:
    """Transport loss must wake the bootstrap EVENT wait as well as correlated requests."""
    connection = FakeConnection()
    client = WampFeedClient(connection)
    try:
        event = asyncio.create_task(client.recv_event())
        invocation = asyncio.create_task(client.recv_invocation())
        await connection.replies.put(OSError("private-token-must-never-appear"))

        for consumer in (event, invocation):
            with pytest.raises(WampFeedProtocolError, match="transport disconnected") as caught:
                await asyncio.wait_for(consumer, timeout=0.1)
            assert "private-token-must-never-appear" not in "".join(
                traceback.format_exception(caught.value)
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_event_from_an_unsubscribed_router_id_is_ignored() -> None:
    """Accepting an EVENT from another subscription could mix unrelated public data into a batch."""
    connection = FakeConnection()
    client = WampFeedClient(connection)
    try:
        subscription = asyncio.create_task(client.subscribe("/registrationDismissed"))
        await wait_for_sent(connection, 1)
        await connection.replies.put(json.dumps([33, connection.sent[0][1], 72]))
        assert await subscription == 72

        await connection.replies.put(json.dumps([36, 73, 1, {}, ["wrong"]]))
        await connection.replies.put(json.dumps([36, 72, 2, {}, ["right"]]))

        assert await client.recv_event() == "right"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_external_call_cancellation_still_propagates() -> None:
    """Turning caller cancellation into a source failure would break orderly shutdown."""
    connection = FakeConnection()
    client = WampFeedClient(connection)
    try:
        call = asyncio.create_task(client.call("/sports#bootstrap"))
        await wait_for_sent(connection, 1)

        call.cancel()

        with pytest.raises(asyncio.CancelledError):
            await call
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_abort_fails_welcome_without_exposing_peer_details() -> None:
    """Ignoring ABORT would leave the anonymous handshake waiting forever."""
    connection = FakeConnection()
    client = WampFeedClient(connection)
    try:
        hello = asyncio.create_task(client.hello("www.example.test"))
        await wait_for_sent(connection, 1)
        await connection.replies.put(
            json.dumps([3, {"private_value": "must-not-appear"}, "wamp.error.system_shutdown"])
        )

        with pytest.raises(WampFeedProtocolError, match="ABORT.*type 3") as caught:
            await asyncio.wait_for(hello, timeout=0.1)
        assert "must-not-appear" not in str(caught.value)

        with pytest.raises(WampFeedProtocolError, match="ABORT.*type 3"):
            await asyncio.wait_for(client.call("/sports#bootstrap"), timeout=0.1)
    finally:
        await client.close()

    assert connection.closed


@pytest.mark.asyncio
async def test_peer_goodbye_fails_inflight_call_and_receives_goodbye_reply() -> None:
    """Ignoring a peer GOODBYE would leave an in-flight public call waiting forever."""
    connection = FakeConnection()
    client = WampFeedClient(connection)
    try:
        call = asyncio.create_task(client.call("/sports#bootstrap"))
        await wait_for_sent(connection, 1)
        await connection.replies.put(
            json.dumps([6, {"private_value": "must-not-appear"}, "wamp.close.normal"])
        )

        with pytest.raises(WampFeedProtocolError, match="GOODBYE.*type 6") as caught:
            await asyncio.wait_for(call, timeout=0.1)
        assert "must-not-appear" not in str(caught.value)
        await wait_for_sent(connection, 2)
        assert connection.sent[-1] == [6, {}, "wamp.close.goodbye_and_out"]
    finally:
        await client.close()

    assert connection.closed


@pytest.mark.asyncio
async def test_close_cleans_up_when_goodbye_send_raises_websocket_exception() -> None:
    """A best-effort GOODBYE failure must not prevent transport and future cleanup."""
    connection = GoodbyeFailingConnection()
    client = WampFeedClient(connection)
    call = asyncio.create_task(client.call("/sports#bootstrap"))
    await wait_for_sent(connection, 1)

    await client.close()

    with pytest.raises(asyncio.CancelledError):
        await call
    assert connection.closed


@pytest.mark.asyncio
async def test_hello_invocation_yield_and_orderly_close() -> None:
    """Skipping HELLO, INVOCATION routing, YIELD, or GOODBYE breaks the WAMP lifecycle."""
    connection = FakeConnection()
    client = WampFeedClient(connection)
    try:
        hello = asyncio.create_task(client.hello("www.example.test"))
        await wait_for_sent(connection, 1)
        await connection.replies.put(json.dumps([2, 99, {}]))
        assert await hello == 99
        assert connection.sent[0][0] == 1

        await connection.replies.put(json.dumps([68, 10, 20, {}, ["work"], {"kind": "test"}]))
        invocation = await client.recv_invocation()
        assert invocation == Invocation(10, 20, {}, ("work",), {"kind": "test"})

        await client.yield_invocation(invocation, {"status": "ok"})
        assert connection.sent[-1] == [70, 10, {}, [{"status": "ok"}]]
    finally:
        await client.close()

    assert connection.sent[-1] == [6, {}, "wamp.close.normal"]
    assert connection.closed
