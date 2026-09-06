import os
import sys
import requests
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env explicitly from project root
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
print(f"Loading env from: {dotenv_path}")
load_dotenv(dotenv_path)

from app.services.ai.tools.jira_tools import JiraSearchTool, JiraCreateIssueInput, JiraCreateIssueTool, get_jira_client

def test_jira_connectivity():
    print("\n" + "="*50)
    print(">>> 🔍 Jira Connectivity Debug Tool")
    print("="*50)
    
    url = os.getenv("JIRA_URL")
    user = os.getenv("JIRA_USERNAME")
    pwd = os.getenv("JIRA_PASSWORD")
    
    if not url or not user or not pwd:
        print("❌ ERROR: Missing Jira configuration (URL/User/Pass) in .env")
        return

    print(f"Target URL: {url}")
    print(f"Username:   {user}")
    print(f"Password:   {'********' if pwd else 'MISSING'}")

    # 1. Low-level HTTP Test
    print("\n[Step 1] Basic Network & Auth Check (Raw HTTP)")
    try:
        # Try to access the 'myself' endpoint which is standard in Jira API
        test_api = f"{url.rstrip('/')}/rest/api/2/myself"
        print(f"Calling: {test_api}")
        
        response = requests.get(
            test_api, 
            auth=(user, pwd), 
            verify=False, 
            timeout=10
        )
        
        print(f"HTTP Status: {response.status_code}")
        print(f"Content-Type: {response.headers.get('Content-Type')}")
        
        if response.status_code == 200:
            print("✅ HTTP Connection Successful!")
            try:
                data = response.json()
                print(f"✅ JSON Parse Successful! Hello, {data.get('displayName')}")
            except Exception:
                print("❌ HTTP 200 but NOT JSON. Server returned:")
                print("-" * 30)
                print(response.text[:1000]) # Print first 1000 chars
                print("-" * 30)
        else:
            print(f"❌ HTTP Error {response.status_code}")
            print("Response Preview:")
            print("-" * 30)
            print(response.text[:1000])
            print("-" * 30)
            
    except Exception as e:
        print(f"❌ Network Level Failure: {e}")

    # 2. Test via Jira Library (The one used in the app)
    print("\n[Step 2] Testing via atlassian-python-api (Library used in app)")
    jira = get_jira_client()
    if not jira:
        print("❌ Failed to initialize Jira client object.")
        return

    try:
        user_jql = 'project = "CS:Service Desk" AND createdDate >= startOfMonth()'
        print(f"Running JQL: {user_jql}")
        
        # The .jql() method is where it usually fails with JSON error
        raw_res = jira.jql(user_jql, limit=1)
        
        issues = raw_res.get("issues", [])
        print(f"✅ Library call success! Found {len(issues)} issues (limit 1).")
        if issues:
            print(f"Latest Issue: {issues[0].get('key')}")
            
    except Exception as e:
        print(f"❌ Library Call Failed: {e}")
        # If it's the JSON error, the library might not show the body. 
        # But our Step 1 should have caught it.

    print("\n" + "="*50)
    print(">>> Debug Finished.")
    print("="*50)

if __name__ == "__main__":
    # Disable urllib3 warnings for self-signed certs
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    test_jira_connectivity()