from layers.layer8_momentum_override import detect_momentum_override


def test_momentum_override_triggers_on_rapid_low_to_six_figures():
    history = [
        ("2026-09-06T14:00:00+00:00", 8000),
        ("2026-09-06T14:10:00+00:00", 20000),
        ("2026-09-06T14:20:00+00:00", 120000),
    ]
    result = detect_momentum_override("D", history)
    assert result.triggered is True


def test_no_override_for_passing_score_even_with_fast_growth():
    history = [
        ("2026-09-06T14:00:00+00:00", 8000),
        ("2026-09-06T14:20:00+00:00", 120000),
    ]
    result = detect_momentum_override("B", history)
    assert result.triggered is False


def test_no_override_when_growth_too_slow_within_window():
    history = [
        ("2026-09-06T14:00:00+00:00", 8000),
        ("2026-09-06T15:00:00+00:00", 50000),  # outside the 30-min window from the latest point
    ]
    result = detect_momentum_override("D", history)
    assert result.triggered is False


def test_mc_moved_enough_true_on_big_move():
    from layers.layer8_momentum_override import mc_moved_enough
    assert mc_moved_enough(10000, 16000) is True  # +60%


def test_mc_moved_enough_false_on_small_move():
    from layers.layer8_momentum_override import mc_moved_enough
    assert mc_moved_enough(10000, 10500) is False  # +5%


def test_mc_moved_enough_fails_closed_on_missing_data():
    from layers.layer8_momentum_override import mc_moved_enough
    assert mc_moved_enough(None, 10000) is False
    assert mc_moved_enough(10000, None) is False
    assert mc_moved_enough(0, 10000) is False
