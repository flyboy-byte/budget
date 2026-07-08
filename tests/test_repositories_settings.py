from app.repositories import settings as repo


def test_get_all_empty_by_default(db, user_id):
    assert repo.get_all(db, user_id) == {}


def test_upsert_creates_setting(db, user_id):
    repo.upsert(db, user_id, "timezone", "America/Denver")
    db.commit()
    assert repo.get_all(db, user_id) == {"timezone": "America/Denver"}


def test_upsert_same_key_overwrites(db, user_id):
    repo.upsert(db, user_id, "timezone", "America/Denver")
    repo.upsert(db, user_id, "timezone", "UTC")
    db.commit()
    assert repo.get_all(db, user_id) == {"timezone": "UTC"}


def test_settings_scoped_to_owner(db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    repo.upsert(db, other_id, "timezone", "America/New_York")
    db.commit()

    assert repo.get_all(db, user_id) == {}
    assert repo.get_all(db, other_id) == {"timezone": "America/New_York"}
