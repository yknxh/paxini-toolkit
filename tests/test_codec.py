from paxkit.device import codec


def test_checksum():
    assert codec.checksum([0x55, 0xAA, 9, 0, 3, 0, 0xFB, 0xF0, 3, 0, 0, 0x7B, 0]) == 0x8C


def test_usb_commands_match_capture():
    # 2026-10-04 S1813E 캡처에서 PXSR이 보낸 바이트
    assert codec.usb_get_version(0).hex() == "55aa09000000fbd41700006400ae"
    assert codec.usb_get_version(3).hex() == "55aa09000300fbd41700006400ab"
    assert codec.usb_get_type_data(3, 31).hex() == "55aa09000300fbf00300007b008c"
    assert codec.usb_set_calibration(3).hex() == "55aa0a000300790300000001000176"


def test_usb_parser_split_frames():
    p = codec.UsbParser()
    ack = bytes.fromhex("aa550a000300790300000001000077")
    assert p.feed(ack[:10], 31).status == -1
    y = p.feed(ack[10:], 31)
    assert (y.status, y.startAddress) == (0, 3)
    assert p.buf == b""


def test_find_sensor_type():
    assert codec.find_sensor_type("PAXINI PXSR-STDDP03F-v1.0.5").label == "S1813E"
    assert codec.find_sensor_type("PXSR-STDDP03G").forces == 52
    assert codec.find_sensor_type("unknown") is None
