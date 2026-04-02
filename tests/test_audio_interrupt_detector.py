from iluminaty.audio import AudioInterruptDetector


def test_audio_interrupt_detector_blocks_on_stop_keyword():
    detector = AudioInterruptDetector(hold_ms=2000)
    result = detector.ingest_transcript("por favor stop ahora", source="test")
    assert result["triggered"] is True
    status = detector.status()
    assert status["blocked"] is True
    assert status["events_count"] >= 1


def test_audio_interrupt_detector_ack_clears_block():
    detector = AudioInterruptDetector(hold_ms=2000)
    detector.ingest_transcript("pause", source="test")
    before = detector.status()
    assert before["blocked"] is True
    ack = detector.acknowledge()
    assert ack["acknowledged"] is True
    after = detector.status()
    assert after["blocked"] is False
