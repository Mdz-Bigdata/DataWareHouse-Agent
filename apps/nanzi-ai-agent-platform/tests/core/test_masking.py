import json
import pytest
from app.utils.masking import mask_sensitive_data

def test_mask_simple_dict():
    data = {"username": "admin", "password": "secret_password"}
    masked = mask_sensitive_data(data)
    assert masked["username"] == "admin"
    assert masked["password"] == "******"

def test_mask_nested_dict():
    data = {
        "user": {
            "name": "john",
            "api_key": "key-123-456"
        },
        "config": {
            "token": "abc.def.ghi"
        }
    }
    masked = mask_sensitive_data(data)
    assert masked["user"]["name"] == "john"
    assert masked["user"]["api_key"] == "******"
    assert masked["config"]["token"] == "******"

def test_mask_list():
    data = [
        {"id": 1, "secret": "s1"},
        {"id": 2, "secret": "s2"}
    ]
    masked = mask_sensitive_data(data)
    assert masked[0]["secret"] == "******"
    assert masked[1]["secret"] == "******"

def test_mask_json_string():
    data_str = json.dumps({"password": "123", "normal": "abc"})
    masked_str = mask_sensitive_data(data_str)
    masked = json.loads(masked_str)
    assert masked["password"] == "******"
    assert masked["normal"] == "abc"

def test_mask_query_string():
    qs = "name=admin&password=123&token=abc"
    masked = mask_sensitive_data(qs)
    assert "name=admin" in masked
    assert "password=******" in masked
    assert "token=******" in masked

def test_mask_malformed_json():
    # Should fallback to regex
    malformed = '{"password": "123", "oops": '
    masked = mask_sensitive_data(malformed)
    assert '"password": "******"' in masked

def test_mask_case_insensitive():
    data = {"Password": "123", "API_KEY": "key"}
    masked = mask_sensitive_data(data)
    assert masked["Password"] == "******"
    assert masked["API_KEY"] == "******"
