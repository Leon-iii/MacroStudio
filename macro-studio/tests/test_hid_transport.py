from struct import pack

import pytest

from macro_studio.external_hid import Frame, Opcode, decode, encode
from macro_studio.external_hid.simulator import McuSimulator
from macro_studio.external_hid.transport import RequestTracker, TransportError


def test_streaming_session_runs_over_simulated_link_and_releases_inputs() -> None:
    device = McuSimulator()
    host = RequestTracker(session_id=123, timeout_ms=50)

    def exchange(opcode, payload=b""):
        response = device.receive(host.request(opcode, payload, now_ms=0))
        assert host.receive(response[:4], now_ms=0) is None
        return host.receive(response[4:], now_ms=0)

    assert exchange(Opcode.HELLO, pack("<BBI", 1, 0, 123)).opcode is Opcode.HELLO_ACK
    assert exchange(Opcode.GET_CAPS).opcode is Opcode.CAPS
    assert exchange(Opcode.ARM_SESSION).opcode is Opcode.ACK
    assert exchange(Opcode.INPUT_EVENT, pack("<BBHH", 1, 1, 7, 4)).opcode is Opcode.ACK
    assert exchange(Opcode.INPUT_EVENT, pack("<BBB", 2, 1, 1)).opcode is Opcode.ACK
    assert device.pressed_keys == {(7, 4)}
    assert device.pressed_buttons == {1}
    assert exchange(Opcode.RELEASE_ALL).opcode is Opcode.ACK
    assert not device.pressed_keys
    assert not device.pressed_buttons
    with pytest.raises(TransportError, match="closed"):
        host.request(Opcode.INPUT_EVENT, b"", now_ms=2)


def test_single_inflight_request_ignores_other_sessions_and_sequences() -> None:
    host = RequestTracker(session_id=123, timeout_ms=50)
    request = decode(host.request(Opcode.GET_STATUS, b"", now_ms=0))
    with pytest.raises(TransportError, match="in flight"):
        host.request(Opcode.GET_CAPS, b"", now_ms=0)
    stale_session = Frame(Opcode.STATUS, seq=request.seq, session_id=124)
    stale_seq = Frame(Opcode.STATUS, seq=request.seq + 1, session_id=123)
    response = Frame(Opcode.STATUS, pack("<BHH", 1, 0, 0), seq=request.seq, session_id=123)
    assert host.receive(encode(stale_session) + encode(stale_seq), now_ms=10) is None
    assert host.receive(encode(response), now_ms=20) == response


def test_lost_input_ack_closes_transport_without_retry_and_watchdog_releases() -> None:
    device = McuSimulator()
    host = RequestTracker(session_id=123, timeout_ms=50)
    for opcode, payload in ((Opcode.HELLO, pack("<BBI", 1, 0, 123)), (Opcode.ARM_SESSION, b"")):
        response = device.receive(host.request(opcode, payload, now_ms=0))
        assert host.receive(response, now_ms=0) is not None
    lost = device.receive(host.request(Opcode.INPUT_EVENT, pack("<BBHH", 1, 1, 7, 4), now_ms=2))
    assert device.pressed_keys == {(7, 4)}
    with pytest.raises(TransportError, match="timed out"):
        host.check_timeout(now_ms=52)
    with pytest.raises(TransportError, match="closed"):
        host.receive(lost, now_ms=53)
    assert decode(device.advance(1500)).opcode is Opcode.FAULT
    assert not device.pressed_keys


@pytest.mark.parametrize("reply", [Opcode.FAULT, Opcode.NACK, Opcode.ACK])
def test_fault_rejection_and_wrong_ack_end_session(reply) -> None:
    host = RequestTracker(session_id=123, timeout_ms=50)
    sent = decode(host.request(Opcode.ARM_SESSION, b"", now_ms=0))
    payload = pack("<BH", sent.opcode, 3) if reply is Opcode.NACK else b""
    with pytest.raises(TransportError):
        host.receive(encode(Frame(reply, payload, seq=sent.seq, session_id=123)), now_ms=1)
    with pytest.raises(TransportError, match="closed"):
        host.check_timeout(now_ms=2)
