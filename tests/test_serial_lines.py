from tdserial_iot.serial_lines import CarecvFrameBuffer


def test_carecv_extracts_fragmented_payload_without_treating_control_bytes_as_lines():
    parser = CarecvFrameBuffer()
    parser.begin()
    payload = b"A\r\n\x00\xffZ"

    chunks = parser.feed(
        b"AT+CARECV=0,20\r\n+CARECV: 6\r\nA\r\n\x00"
    )
    chunks += parser.feed(b"\xffZ\r\nOK\r\n")

    assert chunks == [
        ("stream", b"AT+CARECV=0,20\r\n"),
        ("stream", b"+CARECV: 6\r\n"),
        ("payload", payload[:4]),
        ("payload", payload[4:]),
        ("stream", b"\r\n"),
        ("stream", b"OK\r\n"),
    ]


def test_carecv_supports_optional_remote_endpoint_and_zero_length_response():
    parser = CarecvFrameBuffer()
    parser.begin()

    chunks = parser.feed(
        b'+CARECV: 3,"192.0.2.1",4445\r\nxyz\r\nOK\r\n'
        b"+CARECV: 0\r\nOK\r\n"
    )

    assert chunks == [
        ("stream", b'+CARECV: 3,"192.0.2.1",4445\r\n'),
        ("payload", b"xyz"),
        ("stream", b"\r\n"),
        ("stream", b"OK\r\n"),
        ("stream", b"+CARECV: 0\r\n"),
        ("stream", b"OK\r\n"),
    ]


def test_carecv_handles_header_crlf_split_across_reads():
    parser = CarecvFrameBuffer()
    parser.begin()

    assert parser.feed(b"+CARECV: 3\r") == []
    assert parser.feed(b"\nabc\r\nOK\r\n") == [
        ("stream", b"+CARECV: 3\r\n"),
        ("payload", b"abc"),
        ("stream", b"\r\n"),
        ("stream", b"OK\r\n"),
    ]


def test_carecv_keeps_partial_payload_bytes_out_of_line_processing():
    parser = CarecvFrameBuffer()
    parser.begin()
    assert parser.feed(b"+CARECV: 5\r\nab") == [
        ("stream", b"+CARECV: 5\r\n"),
        ("payload", b"ab"),
    ]

    assert parser.finish() == []
    assert not parser.enabled
    assert parser.feed(b"next\r\n") == [("stream", b"next\r\n")]
