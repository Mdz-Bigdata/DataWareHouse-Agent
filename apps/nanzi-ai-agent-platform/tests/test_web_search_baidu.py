import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from app.services.ai.tools.advanced_auxiliary_tools import web_search_baidu

@pytest.mark.asyncio
async def test_web_search_baidu_success():
    # 模拟 HTML 网页返回，包含百度搜索特有的 class result 或 c-container
    mock_html = """
    <html>
      <body>
        <div id="content_left">
          <div class="result c-container">
            <h3 class="t">
              <a href="http://example.com/link1">测试有孚网络标题一</a>
            </h3>
            <div class="c-span-last">
              这是有孚网络的第一条测试搜索结果摘要。
            </div>
          </div>
          <div class="result c-container">
            <h3 class="t">
              <a href="http://example.com/link2">测试有孚网络标题二</a>
            </h3>
            <span class="abstract">
              这是有孚网络的第二条测试搜索结果摘要。
            </span>
          </div>
        </div>
      </body>
    </html>
    """
    
    # 构造 Playwright 的 Mock 对象结构
    mock_page = AsyncMock()
    mock_page.content = AsyncMock(return_value=mock_html)
    mock_page.goto = AsyncMock()
    mock_page.wait_for_selector = AsyncMock()
    
    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    
    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()
    
    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)
    
    mock_playwright_instance = MagicMock()
    mock_playwright_instance.chromium = mock_chromium
    
    # 模拟 async_playwright() 返回一个异步上下文管理器
    mock_playwright_cm = MagicMock()
    mock_playwright_cm.__aenter__ = AsyncMock(return_value=mock_playwright_instance)
    mock_playwright_cm.__aexit__ = AsyncMock(return_value=None)
    
    with patch("app.services.ai.tools.advanced_auxiliary_tools.async_playwright", return_value=mock_playwright_cm):
        result = await web_search_baidu.ainvoke({"query": "有孚网络", "max_results": 2})
        
    assert "百度搜索结果 (关于: '有孚网络')" in result
    assert "测试有孚网络标题一" in result
    assert "测试有孚网络标题二" in result
    assert "这是有孚网络的第一条测试搜索结果摘要" in result
    assert "这是有孚网络的第二条测试搜索结果摘要" in result
    assert "http://example.com/link1" in result
    assert "http://example.com/link2" in result

@pytest.mark.asyncio
async def test_web_search_baidu_no_results():
    # 模拟未找到 content_left 的 HTML 网页返回
    mock_html = "<html><body><div>没有结果</div></body></html>"
    
    mock_page = AsyncMock()
    mock_page.content = AsyncMock(return_value=mock_html)
    mock_page.goto = AsyncMock()
    mock_page.wait_for_selector = AsyncMock()
    
    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    
    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()
    
    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)
    
    mock_playwright_instance = MagicMock()
    mock_playwright_instance.chromium = mock_chromium
    
    mock_playwright_cm = MagicMock()
    mock_playwright_cm.__aenter__ = AsyncMock(return_value=mock_playwright_instance)
    mock_playwright_cm.__aexit__ = AsyncMock(return_value=None)
    
    with patch("app.services.ai.tools.advanced_auxiliary_tools.async_playwright", return_value=mock_playwright_cm):
        result = await web_search_baidu.ainvoke({"query": "不存在的词", "max_results": 2})
        
    assert "未能检索到任何相关结果" in result
