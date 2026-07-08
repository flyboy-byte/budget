from app.repositories import push_subscriptions as repo


def _other_user(db):
    cur = db.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)", ("bob", "hash")
    )
    db.commit()
    return cur.lastrowid


def test_create_and_list_for_user(db, user_id):
    repo.create(db, user_id, "https://push.example/1", "p256dh-key", "auth-key")
    db.commit()
    rows = repo.list_for_user(db, user_id)
    assert len(rows) == 1
    assert rows[0]["endpoint"] == "https://push.example/1"
    assert rows[0]["p256dh"] == "p256dh-key"


def test_list_for_user_scoped_per_user(db, user_id):
    other_id = _other_user(db)
    repo.create(db, user_id, "https://push.example/mine", "p", "a")
    repo.create(db, other_id, "https://push.example/theirs", "p", "a")
    db.commit()
    assert [r["endpoint"] for r in repo.list_for_user(db, user_id)] == ["https://push.example/mine"]


def test_create_upserts_on_duplicate_endpoint(db, user_id):
    repo.create(db, user_id, "https://push.example/1", "old-p256dh", "old-auth")
    db.commit()
    repo.create(db, user_id, "https://push.example/1", "new-p256dh", "new-auth")
    db.commit()
    rows = repo.list_for_user(db, user_id)
    assert len(rows) == 1
    assert rows[0]["p256dh"] == "new-p256dh"


def test_delete_is_ownership_scoped(db, user_id):
    other_id = _other_user(db)
    sub_id = repo.create(db, other_id, "https://push.example/theirs", "p", "a")
    db.commit()
    assert repo.delete(db, user_id, sub_id) is False
    assert len(repo.list_for_user(db, other_id)) == 1


def test_delete_removes_own_subscription(db, user_id):
    sub_id = repo.create(db, user_id, "https://push.example/1", "p", "a")
    db.commit()
    assert repo.delete(db, user_id, sub_id) is True
    assert repo.list_for_user(db, user_id) == []


def test_delete_by_endpoint(db, user_id):
    repo.create(db, user_id, "https://push.example/1", "p", "a")
    db.commit()
    assert repo.delete_by_endpoint(db, "https://push.example/1") is True
    assert repo.list_for_user(db, user_id) == []
