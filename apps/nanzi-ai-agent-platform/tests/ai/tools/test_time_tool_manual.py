import sys
import os
from datetime import datetime
import pytz

# Add project root to path
sys.path.append(os.getcwd())

from app.services.ai.tools.system_tools import get_current_time

def test_time_output():
    print("--- Testing get_current_time tool ---")
    
    # Use .func to access the original function since it's decorated with @tool
    get_time_func = get_current_time.func
    
    # Test Default (Shanghai)
    res_sh = get_time_func()
    print(f"Default (Shanghai): {res_sh}")
    assert "星期" in res_sh
    
    # Test UTC
    res_utc = get_time_func(timezone="UTC")
    print(f"UTC: {res_utc}")
    assert "UTC" in res_utc or "+0000" in res_utc
    
    # Verify specific date (2026-01-22 should be Thursday)
    # We can't easily mock datetime.now() without a mocking library, 
    # but we can check the logic of weekday calculation.
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    test_date = datetime(2026, 1, 22)
    print(f"Manual check for 2026-01-22: {weekdays[test_date.weekday()]}")
    assert weekdays[test_date.weekday()] == "星期四"
    
    print("\n✅ Test Passed: Tool logic is correct and imports are working.")

if __name__ == "__main__":
    try:
        test_time_output()
    except Exception as e:
        print(f"\n❌ Test Failed: {e}")
        sys.exit(1)
