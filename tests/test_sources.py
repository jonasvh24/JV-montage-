from pathlib import Path

from nullmontage.effects import check_source, sniff_kind


def test_sniff_and_check(tmp_path, tiny_photos, tiny_clip):
    assert sniff_kind(tiny_photos[0]) == "image" and check_source(tiny_photos[0])[0]
    assert sniff_kind(tiny_clip) == "video" and check_source(tiny_clip)[0]
    fake = tmp_path / "photo.jpeg"
    fake.write_bytes(b"bplist00\x00" * 20)
    assert sniff_kind(fake) is None
    ok, reason = check_source(fake)
    assert not ok and "Photos" in reason
    trunc = tmp_path / "trunc.jpg"
    trunc.write_bytes(tiny_photos[0].read_bytes()[:300])
    assert not check_source(trunc)[0]
    empty = tmp_path / "e.png"
    empty.write_bytes(b"")
    assert not check_source(empty)[0]
