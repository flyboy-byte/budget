from app.repositories import snapshots as repo

VALUES = {
    "cash_on_hand_cents": 10000,
    "total_debt_cents": 2000,
    "net_position_cents": 8000,
    "reserved_cash_cents": 500,
    "safe_to_spend_cents": 9500,
    "forecast_position_cents": 9000,
    "protected_floor_cents": 0,
    "window_days": 14,
}


def test_upsert_creates_snapshot(db, user_id):
    repo.upsert_snapshot(db, user_id, "2026-01-01", VALUES)
    db.commit()
    rows = repo.list_snapshots(db, user_id)
    assert len(rows) == 1
    assert rows[0]["safe_to_spend_cents"] == 9500


def test_upsert_same_date_overwrites_not_duplicates(db, user_id):
    repo.upsert_snapshot(db, user_id, "2026-01-01", VALUES)
    db.commit()
    updated_values = {**VALUES, "safe_to_spend_cents": 12345}
    repo.upsert_snapshot(db, user_id, "2026-01-01", updated_values)
    db.commit()

    rows = repo.list_snapshots(db, user_id)
    assert len(rows) == 1
    assert rows[0]["safe_to_spend_cents"] == 12345


def test_list_orders_newest_first(db, user_id):
    repo.upsert_snapshot(db, user_id, "2026-01-01", VALUES)
    repo.upsert_snapshot(db, user_id, "2026-02-01", VALUES)
    db.commit()
    rows = repo.list_snapshots(db, user_id)
    assert [r["snapshot_date"] for r in rows] == ["2026-02-01", "2026-01-01"]


def test_delete_snapshot(db, user_id):
    repo.upsert_snapshot(db, user_id, "2026-01-01", VALUES)
    db.commit()
    snapshot_id = repo.list_snapshots(db, user_id)[0]["id"]
    assert repo.delete_snapshot(db, user_id, snapshot_id) is True
    assert repo.get_snapshot(db, user_id, snapshot_id) is None


def test_snapshot_scoped_to_owner(db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    repo.upsert_snapshot(db, other_id, "2026-01-01", VALUES)
    db.commit()
    snapshot_id = repo.list_snapshots(db, other_id)[0]["id"]
    assert repo.get_snapshot(db, user_id, snapshot_id) is None
    assert repo.delete_snapshot(db, user_id, snapshot_id) is False
