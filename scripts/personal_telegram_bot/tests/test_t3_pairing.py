from personal_telegram_bot.t3_pairing import (
    T3PairingLogParser,
    format_t3_pairing_message,
    pairing_dedupe_key,
)


def test_parser_emits_pairing_when_url_arrives():
    parser = T3PairingLogParser()

    assert parser.feed("T3 Code server is ready.") is None
    assert parser.feed("Connection string: http://100.64.0.1:3773") is None
    assert parser.feed("Token: ABCD2345EFGH") is None

    pairing = parser.feed("Pairing URL: http://100.64.0.1:3773/pair#token=ABCD2345EFGH")

    assert pairing is not None
    assert pairing.connection_string == "http://100.64.0.1:3773"
    assert pairing.token == "ABCD2345EFGH"
    assert pairing.pairing_url == "http://100.64.0.1:3773/pair#token=ABCD2345EFGH"


def test_parser_recovers_token_from_pairing_url():
    parser = T3PairingLogParser()

    pairing = parser.feed("Pairing URL: http://host/pair#token=ZXCV2345BNMM")

    assert pairing is not None
    assert pairing.token == "ZXCV2345BNMM"


def test_pairing_message_contains_only_expected_target_info():
    parser = T3PairingLogParser()
    parser.feed("Connection string: http://100.64.0.1:3773")
    parser.feed("Token: ABCD2345EFGH")
    pairing = parser.feed("Pairing URL: http://100.64.0.1:3773/pair#token=ABCD2345EFGH")

    text = format_t3_pairing_message(pairing, label="nervous energy")

    assert "nervous energy" in text
    assert "ABCD2345EFGH" in text
    assert "http://100.64.0.1:3773/pair#token=ABCD2345EFGH" in text
    assert "Telegram" not in text


def test_pairing_dedupe_key_does_not_store_raw_token():
    parser = T3PairingLogParser()
    parser.feed("Token: ABCD2345EFGH")
    pairing = parser.feed("Pairing URL: http://host/pair#token=ABCD2345EFGH")

    key = pairing_dedupe_key(pairing)

    assert "ABCD2345EFGH" not in key
    assert len(key) == 64
