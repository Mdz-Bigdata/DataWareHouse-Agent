import sys
import os

# Add project root to sys.path
sys.path.append(os.getcwd())

try:
    print("Checking app.services.ai.tools.generic_api...")
    import app.services.ai.tools.generic_api
    print("✅ app.services.ai.tools.generic_api imported successfully")

    print("Checking app.services.ai.tools.registry...")
    import app.services.ai.tools.registry
    print("✅ app.services.ai.tools.registry imported successfully")

    print("Checking app.api.portal.endpoints.tools...")
    import app.api.portal.endpoints.tools
    print("✅ app.api.portal.endpoints.tools imported successfully")

    print("Checking app.models.tool...")
    import app.models.tool
    print("✅ app.models.tool imported successfully")
    
    print("Checking app.schemas.tool...")
    import app.schemas.tool
    print("✅ app.schemas.tool imported successfully")

except Exception as e:
    print(f"❌ Compilation/Import Error: {e}")
    sys.exit(1)
