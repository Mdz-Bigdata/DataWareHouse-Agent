import requests
import json
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

class LaplacePortalApiClient(object):
    @staticmethod
    def get_all_users():
        try:
            headers = {
                'YOVOLE-LAPLACE-API-ACCESS-TOKEN': settings.SSO_ACCESS_TOKEN,
                'Content-Type': 'application/json;charset=UTF-8'
            }
            api_url = 'https://yovole.net/api/v1/user/list'            
            
            # 先查询所有的用户
            api_request_all = {
                'requestSystem': settings.SSO_REQUEST_SYSTEM,
                'requestBusiness': 'USER-ADMIN-SYNC-AD-USER',
                'operationType': 'READ',
                'userStatus': -1,
                "userInfo": True
            }
            
            all_users = []
            
            # 查询 userStatus=0
            resp_0 = requests.post(url=api_url, headers=headers, data=json.dumps(api_request_all), timeout=30000,
                                  verify=False)
            if resp_0.status_code == 200:
                response_0 = json.loads(resp_0.content)
                for sso_user in response_0.get('data', []):
                    name = sso_user.get('displayName', '')
                    code = sso_user.get('loginName', '').lower()
                    email = sso_user.get('userEmail', '')
                    mobile = sso_user.get('userMobile', '')
                    department = sso_user.get('departmentName', '')
                    position = sso_user.get('positionName', '')
                    status = sso_user.get('userStatus', 0),
                    userinfo = sso_user.get('userInfo', '')
                    if code:
                        all_users.append({
                            'code': code,
                            'name': name,
                            'email': email,
                            'status': status == 0,
                            'mobile': mobile,
                            'department': department,
                            'position': position,
                            'userinfo': userinfo
                        })
            
            return all_users
        except Exception as e:
            logger.error("auth_user error：%s" % e)
        return []