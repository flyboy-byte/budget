from app.sparkline import build_sparkline_svg


def test_empty_points_returns_empty_string():
    assert build_sparkline_svg([]) == ""


def test_single_point_renders_a_dot():
    svg = build_sparkline_svg([("2026-07-01", 1000)])
    assert svg.startswith("<svg")
    assert "<circle" in svg
    assert "var(--good)" in svg


def test_negative_latest_value_uses_bad_color():
    svg = build_sparkline_svg([("2026-07-01", 1000), ("2026-07-02", -500)])
    assert "var(--bad)" in svg
    assert "var(--good)" not in svg


def test_positive_latest_value_uses_good_color():
    svg = build_sparkline_svg([("2026-07-01", -500), ("2026-07-02", 1000)])
    assert "var(--good)" in svg
    assert "var(--bad)" not in svg


def test_multi_point_includes_line_path_and_hover_titles():
    svg = build_sparkline_svg([("2026-07-01", 100), ("2026-07-02", 200), ("2026-07-03", 150)])
    assert svg.count("<title>") == 3
    assert "2026-07-01" in svg
    assert "$1.00" in svg  # 100 cents formatted


def test_flat_line_does_not_divide_by_zero():
    # all values identical -> span would be 0 without the `or 1` guard
    svg = build_sparkline_svg([("2026-07-01", 500), ("2026-07-02", 500), ("2026-07-03", 500)])
    assert svg.startswith("<svg")
    assert "nan" not in svg.lower()


def test_no_last_real_date_renders_fully_solid_no_hollow_marker():
    svg = build_sparkline_svg(
        [("2026-07-01", 100), ("2026-07-02", 200), ("2026-07-03", 150)], last_real_date=None
    )
    assert "stroke-dasharray" not in svg
    # only the solid end-dot's own stroke uses bg-elevated; no separate hollow marker
    assert svg.count("var(--bg-elevated)") == 1


def test_last_real_date_equal_to_latest_point_renders_fully_solid():
    points = [("2026-07-01", 100), ("2026-07-02", 200), ("2026-07-03", 150)]
    svg = build_sparkline_svg(points, last_real_date="2026-07-03")
    assert "stroke-dasharray" not in svg


def test_last_real_date_before_latest_splits_into_solid_and_dashed():
    points = [("2026-07-01", 100), ("2026-07-02", 200), ("2026-07-03", 150), ("2026-07-04", 150)]
    svg = build_sparkline_svg(points, last_real_date="2026-07-02")
    assert svg.count("stroke-dasharray") == 1
    # hollow marker at the last-real point (fill=bg-elevated) plus the solid end-dot's
    # own stroke=bg-elevated -- two occurrences total instead of one
    assert svg.count("var(--bg-elevated)") == 2


def test_last_real_date_before_all_points_renders_fully_solid():
    points = [("2026-07-01", 100), ("2026-07-02", 200)]
    svg = build_sparkline_svg(points, last_real_date="2026-06-01")
    assert "stroke-dasharray" not in svg
