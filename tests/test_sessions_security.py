from app import security


def test_list_sessions_returns_only_active_ones(db, user_id):
    id1, _ = security.create_session(db, user_id, "ua-1")
    db.commit()
    id2, _ = security.create_session(db, user_id, "ua-2")
    db.commit()
    db.execute(
        "UPDATE sessions SET expires_at = '2000-01-01T00:00:00.000000Z' WHERE id = ?", (id1,)
    )
    db.commit()

    active = security.list_sessions(db, user_id)
    assert [s["id"] for s in active] == [id2]


def test_list_sessions_ordered_most_recent_first(db, user_id):
    id1, _ = security.create_session(db, user_id)
    db.commit()
    id2, _ = security.create_session(db, user_id)
    db.commit()
    db.execute("UPDATE sessions SET last_seen_at = '2020-01-01T00:00:00.000000Z' WHERE id = ?", (id1,))
    db.execute("UPDATE sessions SET last_seen_at = '2030-01-01T00:00:00.000000Z' WHERE id = ?", (id2,))
    db.commit()

    active = security.list_sessions(db, user_id)
    assert [s["id"] for s in active] == [id2, id1]


def test_delete_session_for_user_scoped_to_owner(db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    session_id, _ = security.create_session(db, other_id)
    db.commit()

    deleted = security.delete_session_for_user(db, user_id, session_id)
    db.commit()
    assert deleted is False
    assert security.get_valid_session(db, session_id) is not None


def test_delete_session_for_user_deletes_own_session(db, user_id):
    session_id, _ = security.create_session(db, user_id)
    db.commit()
    deleted = security.delete_session_for_user(db, user_id, session_id)
    db.commit()
    assert deleted is True
    assert security.get_valid_session(db, session_id) is None


def test_delete_other_sessions_keeps_current(db, user_id):
    keep_id, _ = security.create_session(db, user_id)
    db.commit()
    other1, _ = security.create_session(db, user_id)
    other2, _ = security.create_session(db, user_id)
    db.commit()

    revoked_count = security.delete_other_sessions(db, user_id, keep_id)
    db.commit()

    assert revoked_count == 2
    assert security.get_valid_session(db, keep_id) is not None
    assert security.get_valid_session(db, other1) is None
    assert security.get_valid_session(db, other2) is None


def test_delete_other_sessions_never_touches_another_user(db, user_id):
    other_cur = db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'hash')")
    db.commit()
    other_id = other_cur.lastrowid
    my_session, _ = security.create_session(db, user_id)
    bobs_session, _ = security.create_session(db, other_id)
    db.commit()

    security.delete_other_sessions(db, user_id, my_session)
    db.commit()

    assert security.get_valid_session(db, bobs_session) is not None
