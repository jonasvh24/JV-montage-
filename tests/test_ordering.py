from pathlib import Path

from PIL import Image

from nullmontage.ordering import OrderPlan, contact_sheets, heuristic_order, repair


def _thumbs(tmp_path: Path, colors) -> list[Path]:
    out = []
    for i, c in enumerate(colors):
        p = tmp_path / f"{i:03d}_t.jpg"
        Image.new("RGB", (320, 180), c).save(p)
        out.append(p)
    return out


def test_heuristic_is_a_permutation_that_follows_visual_similarity(tmp_path):
    # black, white, dark grey, light grey: flow should go black -> dark grey -> light grey -> white
    thumbs = _thumbs(tmp_path, [(0, 0, 0), (255, 255, 255), (40, 40, 40), (200, 200, 200)])
    order, why = heuristic_order(thumbs)
    assert sorted(order) == [0, 1, 2, 3]
    assert order == [0, 2, 3, 1]
    assert "heuristic" in why


def test_repair_fixes_claude_output():
    assert repair([3, 1, 1, 2, 99, 0], 4) == [2, 0, 1, 3]
    assert repair([], 3) == [0, 1, 2]
    assert repair([2, 3, 1], 3) == [1, 2, 0]


def test_contact_sheets_cover_every_shot(tmp_path):
    thumbs = _thumbs(tmp_path, [(i * 4, 100, 150) for i in range(37)])
    names = [t.name for t in thumbs[:-1]] + ["036_clip.mov"]
    sheets = contact_sheets(thumbs, names)
    assert len(sheets) == 2
    import io
    im = Image.open(io.BytesIO(sheets[1]))
    assert im.size == (6 * 256, 2 * 144)  # 7 leftover tiles -> 2 rows


def test_order_plan_schema_round_trip():
    p = OrderPlan(order=[2, 1], rationale="x")
    assert p.climax_shots == [] and p.opening == ""
