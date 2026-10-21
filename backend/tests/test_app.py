import pytest
from unittest.mock import patch, MagicMock
from app import app, time_str_to_ms

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c

def test_time_str_to_ms():
    assert time_str_to_ms("01:02.3") == 62300
    assert time_str_to_ms("00:50.658") == 50658
    assert time_str_to_ms("50.6") == 50600
    assert time_str_to_ms("1:00") == 60000
    assert time_str_to_ms("-") == 0
    assert time_str_to_ms("DNF") == 0
    assert time_str_to_ms("") == 0
    assert time_str_to_ms("invalid") == 0

def test_save_stage_no_token(client):
    resp = client.post('/api/save-stage', json={})
    assert resp.status_code == 401

def test_save_stage_non_admin_token(client):
    with patch('utils.auth.get_user_from_token') as mock_get_user:
        mock_user = MagicMock()
        mock_user.app_metadata = {"role": "user"}  # non admin
        mock_get_user.return_value = mock_user
        resp = client.post('/api/save-stage',
                           json={"stage_name": "SS1", "leg_id": 1},
                           headers={"Authorization": "Bearer fake-token"})
        assert resp.status_code == 403

def test_save_stage_admin_creates_stage(client):
    with patch('utils.auth.get_user_from_token') as mock_get_user, \
         patch('app.get_supabase') as mock_sb:
        mock_user = MagicMock()
        mock_user.app_metadata = {"role": "admin"}
        mock_get_user.return_value = mock_user

        # Mock Supabase: insert restituisce un id
        sb = MagicMock()
        sb.table.return_value.insert.return_value.execute.return_value.data = [{"id": 42}]
        mock_sb.return_value = sb

        resp = client.post('/api/save-stage',
                           json={"stage_name": "SS1", "leg_id": 1, "results": []},
                           headers={"Authorization": "Bearer fake-token"})
        assert resp.status_code == 200
        assert resp.json["success"] is True
        assert resp.json["stage_id"] == 42