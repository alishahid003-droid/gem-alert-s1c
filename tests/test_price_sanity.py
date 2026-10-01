from executor.price_sanity import accept_dict


def test_fake_spike_ignored_real_move_accepted():
    rec = {}
    assert accept_dict(rec, 100_000)
    assert not accept_dict(rec, 2_500_000)      # 25x in one read: glitch
    assert accept_dict(rec, 110_000)            # back to normal
    for _ in range(3):
        assert not accept_dict(rec, 10_000)     # persistent -90%...
    assert accept_dict(rec, 10_000)             # ...accepted as a real rug after 3 reads
