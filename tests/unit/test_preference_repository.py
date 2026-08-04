from app.models.base import Channel
from app.repositories.preference_repository import PreferenceRepository


def test_defaults_to_all_channels_enabled_when_no_preferences_set(db_session):
    repo = PreferenceRepository(db_session)
    enabled = repo.get_enabled_channels("new-user", requested=None)
    assert set(enabled) == set(Channel)


def test_explicit_opt_out_is_respected(db_session):
    repo = PreferenceRepository(db_session)
    repo.upsert("user-1", Channel.SMS, enabled=False)
    db_session.commit()

    enabled = repo.get_enabled_channels("user-1", requested=None)
    assert Channel.SMS not in enabled
    assert Channel.EMAIL in enabled
    assert Channel.PUSH in enabled


def test_requested_channels_filtered_by_opt_out(db_session):
    repo = PreferenceRepository(db_session)
    repo.upsert("user-1", Channel.PUSH, enabled=False)
    db_session.commit()

    enabled = repo.get_enabled_channels("user-1", requested=[Channel.EMAIL, Channel.PUSH])
    assert enabled == [Channel.EMAIL]


def test_upsert_updates_existing_preference(db_session):
    repo = PreferenceRepository(db_session)
    repo.upsert("user-1", Channel.EMAIL, enabled=False)
    db_session.commit()
    repo.upsert("user-1", Channel.EMAIL, enabled=True)
    db_session.commit()

    rows = repo.get_all_for_user("user-1")
    email_rows = [r for r in rows if r.channel == Channel.EMAIL]
    assert len(email_rows) == 1
    assert email_rows[0].enabled is True
