from app.api.routes.v1.literature_research.local_library_routes.streaming import (
    analysis_event_sequence,
    decode_pubsub_event,
    sync_event_sequence,
)


def test_sync_stream_uses_the_persisted_summary_sequence_cursor() -> None:
    event = {
        "type": "local_paper_sync_event",
        "data": {"summary_json": {"sequence": "12"}},
    }

    assert sync_event_sequence(event) == 12
    assert sync_event_sequence({"data": {}}) == 0


def test_analysis_stream_uses_the_event_envelope_sequence_cursor() -> None:
    assert analysis_event_sequence({"data": {"sequence": 9}}) == 9
    assert analysis_event_sequence({"data": {"sequence": "invalid"}}) == 0


def test_redis_payload_decode_rejects_non_event_data() -> None:
    assert decode_pubsub_event(b'{"data":{"sequence":1}}') == {"data": {"sequence": 1}}
    assert decode_pubsub_event(b"not-json") is None
