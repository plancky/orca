from backend.features.conflict import _parse_time, find_overlaps


def test_parse_time():
    assert _parse_time("2024-01-01T10:00:00Z") is not None
    assert _parse_time("2024-01-01T10:00:00+00:00") is not None
    assert _parse_time("invalid") is None

def test_detect_overlaps_with_window():
    events = [
        {
            "id": "1",
            "start_at": "2024-01-01T09:00:00Z",
            "end_at": "2024-01-01T10:00:00Z",
        },
        {
            "id": "2",
            "start_at": "2024-01-01T11:00:00Z",
            "end_at": "2024-01-01T12:00:00Z",
        },
        {
            "id": "3",
            "start_at": "2024-01-01T11:30:00Z",
            "end_at": "2024-01-01T13:00:00Z",
        },
    ]
    window = {"start": "2024-01-01T10:30:00Z", "end": "2024-01-01T11:45:00Z"}
    
    overlaps = find_overlaps(events, window)
    assert len(overlaps) == 2
    ids = {e["id"] for e in overlaps}
    assert ids == {"2", "3"}

def test_detect_overlaps_pairwise():
    events = [
        {
            "id": "A",
            "start_at": "2024-01-01T09:00:00Z",
            "end_at": "2024-01-01T10:00:00Z",
        },
        {
            "id": "B",
            "start_at": "2024-01-01T09:30:00Z",
            "end_at": "2024-01-01T10:30:00Z",
        },
        {
            "id": "C",
            "start_at": "2024-01-01T11:00:00Z",
            "end_at": "2024-01-01T12:00:00Z",
        },
    ]
    overlaps = find_overlaps(events)
    assert len(overlaps) == 2
    ids = {e["id"] for e in overlaps}
    assert ids == {"A", "B"}

def test_back_to_back():
    events = [
        {
            "id": "A",
            "start_at": "2024-01-01T09:00:00Z",
            "end_at": "2024-01-01T10:00:00Z",
        },
        {
            "id": "B",
            "start_at": "2024-01-01T10:00:00Z",
            "end_at": "2024-01-01T11:00:00Z",
        },
    ]
    assert find_overlaps(events) == []
    
    window = {"start": "2024-01-01T10:00:00Z", "end": "2024-01-01T11:00:00Z"}
    overlaps = find_overlaps([events[0]], window)
    assert overlaps == []

def test_malformed_input():
    assert find_overlaps("not a list") == []
    assert find_overlaps([{"id": "bad", "start_at": "f", "end_at": "b"}]) == []
    assert find_overlaps([], time_window={"start": "f", "end": "b"}) == []
    assert find_overlaps([{"start_at": "2024-01-01T10:00:00Z"}]) == []
    bad_time = {
        "start_at": "2024-01-01T10:00:00Z",
        "end_at": "2024-01-01T09:00:00Z",
    }
    assert find_overlaps([bad_time]) == []
