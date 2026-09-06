import pytest
import socket
import httpx
from unittest.mock import patch, AsyncMock
from app.services.ai.tools.system_tools import validate_url, system_http_request

# --- URL Validation Tests ---

def test_validate_url_safe():
    """测试合法的外部 URL"""
    with patch("socket.gethostbyname", return_value="8.8.8.8"):
        assert validate_url("https://www.google.com") is True

def test_validate_url_internal_ip():
    """测试拦截内网 IP"""
    with patch("socket.gethostbyname", return_value="192.168.1.1"):
        with pytest.raises(ValueError, match="restricted"):
            validate_url("http://internal-service/api")

def test_validate_url_loopback():
    """测试拦截回环地址"""
    with patch("socket.gethostbyname", return_value="127.0.0.1"):
        with pytest.raises(ValueError, match="restricted"):
            validate_url("http://localhost:8000")

def test_validate_url_invalid():
    """测试不合法的 URL"""
    with pytest.raises(Exception):
        validate_url("not-a-url")

# --- System HTTP Request Tests ---

@pytest.mark.asyncio
async def test_system_http_request_success():
    """测试正常的 HTTP 请求"""
    with patch("app.services.ai.tools.system_tools.validate_url", return_value=True), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        
        mock_get.return_value = httpx.Response(200, json={"status": "ok"})
        
        result = await system_http_request.ainvoke({
            "method": "GET",
            "url": "https://api.github.com/zen"
        })
        
        assert "ok" in result
        mock_get.assert_called_once()

@pytest.mark.asyncio
async def test_system_http_request_ssrf_blocked():
    """测试被 SSRF 拦截的情况"""
    # 模拟 validate_url 抛出异常
    with patch("app.services.ai.tools.system_tools.validate_url", side_effect=ValueError("Access restricted")):
        result = await system_http_request.ainvoke({
            "method": "GET",
            "url": "http://127.0.0.1/admin"
        })
        
        assert "Error executing request" in result
        assert "Access restricted" in result

@pytest.mark.asyncio
async def test_system_http_request_post():
    """测试 POST 请求"""
    with patch("app.services.ai.tools.system_tools.validate_url", return_value=True), \
         patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
        
        mock_request.return_value = httpx.Response(201, content="Created")
        
        result = await system_http_request.ainvoke({
            "method": "POST",
            "url": "https://api.test.com/v1",
            "body": {"key": "val"}
        })
        
        assert "Created" in result
        args, kwargs = mock_request.call_args
        assert kwargs["json"] == {"key": "val"}
