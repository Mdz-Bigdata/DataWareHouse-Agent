import hashlib
import secrets
import httpx
from typing import Optional, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from app.core.redis import get_redis
from app.core.orm import AsyncSessionLocal
from app.core.config import get_settings
from app.models.user import User
from app.utils.encryption import get_api_key_manager
from passlib.context import CryptContext

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- Bcrypt Monkeypatch for Passlib Compatibility ---
import bcrypt
if not hasattr(bcrypt, "__about__"):
    class BcryptAbout:
        __version__ = getattr(bcrypt, "__version__", "4.0.1")
    bcrypt.__about__ = BcryptAbout()
# ---------------------------------------------------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class AuthService:
    @staticmethod
    async def _get_session(db: Optional[AsyncSession] = None):
        """Helper to get a session if not provided"""
        if db:
            return db, False
        session = AsyncSessionLocal()
        return session, True

    @staticmethod
    async def generate_api_key(
        user_name: str,
        role: str = "user",
        real_name: str = None,
        remark: str = None,
        dept_code: str = None,
        org_path: str = None,
        extra_data: str = None,
        user_id: Optional[int] = None,
        db: Optional[AsyncSession] = None
    ) -> str:
        """
        生成 API 密钥 (ORM Version)
        """
        session, is_local = await AuthService._get_session(db)
        try:
            manager = get_api_key_manager()
            api_key, encrypted_key, hashed_key = manager.generate_api_key()
            
            new_user = User(
                user_name=user_name,
                real_name=real_name,
                api_key_encrypted=encrypted_key,
                api_key_hash=hashed_key,
                role=role,
                dept_code=dept_code,
                org_path=org_path,
                extra_data=extra_data,
                remark=remark,
                status=1
            )
            if user_id is not None:
                new_user.id = user_id
            session.add(new_user)
            await session.commit()
            return api_key
        except Exception:
            await session.rollback()
            raise
        finally:
            if is_local:
                await session.close()

    @staticmethod
    async def verify_api_key(api_key: str, db: Optional[AsyncSession] = None) -> Optional[Dict]:
        """
        校验 API Key (Redis -> ORM)
        """
        manager = get_api_key_manager()
        hashed_key = manager.hash_api_key(api_key)
        cache_key = f"auth:api_key:{hashed_key}"
        
        # 1. Redis Cache
        redis = await get_redis()
        if redis:
            cached_user = await redis.hgetall(cache_key)
            if cached_user:
                 # 增强校验：检查 status 字段
                 # 如果 status 缺失（旧缓存）或者 status不是 "1"，视为无效/需要重新验证
                 if cached_user.get("status") != "1":
                     pass # Fall through to DB
                 else:
                     return cached_user

        # 2. DB Query
        session, is_local = await AuthService._get_session(db)
        try:
            result = await session.execute(
                select(User).where(User.api_key_hash == hashed_key)
            )
            user = result.scalar_one_or_none()
            
            user_data = None
            if user and user.status == 1:
                user_data = {
                    "user_id": str(user.id),
                    "user_name": user.user_name,
                    "real_name": user.real_name or user.user_name,
                    "role": user.role,
                    "dept_code": user.dept_code or "",
                    "org_path": user.org_path or "",
                    "extra_data": user.extra_data or "",
                    "created_at": user.created_at.strftime("%Y-%m-%d %H:%M:%S") if user.created_at else None,
                    "remark": user.remark or "",
                    "status": str(user.status) # Add status to cache
                }
            
            # 3. Cache to Redis
            if user_data and redis:
                await redis.hset(cache_key, mapping=user_data)
                await redis.expire(cache_key, 3600)

            return user_data
        finally:
            if is_local:
                await session.close()

    @staticmethod
    async def resolve_user_by_username(username: str, db: AsyncSession) -> Optional[Dict]:
        """
        按登录名解析启用用户，返回与 verify_api_key 一致的业务字段（不含 api_key）。
        供无需 API Key 的受控接口（如 ChatBI sql/checkauth）使用。
        """
        name = (username or "").strip()
        if not name:
            return None
        result = await db.execute(select(User).where(User.user_name == name))
        user = result.scalar_one_or_none()
        if not user or user.status != 1:
            return None
        return {
            "user_id": str(user.id),
            "user_name": user.user_name,
            "real_name": user.real_name or user.user_name,
            "role": user.role,
            "dept_code": user.dept_code or "",
            "org_path": user.org_path or "",
            "extra_data": user.extra_data or "",
            "created_at": user.created_at.strftime("%Y-%m-%d %H:%M:%S") if user.created_at else None,
            "remark": user.remark or "",
        }

    @staticmethod
    async def reset_api_key(user_id: int, db: Optional[AsyncSession] = None) -> Optional[str]:
        """
        重置 API Key
        """
        session, is_local = await AuthService._get_session(db)
        try:
            manager = get_api_key_manager()
            api_key, encrypted_key, hashed_key = manager.generate_api_key()
            
            # Fetch user first to get old hash for cache clearing
            user = await session.get(User, user_id)
            if not user:
                return None
            
            old_hash = user.api_key_hash
            
            # Update
            user.api_key_encrypted = encrypted_key
            user.api_key_hash = hashed_key
            await session.commit()
            
            # Clear Cache
            if old_hash:
                cache_key = f"auth:api_key:{old_hash}"
                redis = await get_redis()
                if redis:
                    await redis.delete(cache_key)
            
            return api_key
        except Exception:
            await session.rollback()
            raise
        finally:
            if is_local:
                await session.close()
    
    @staticmethod
    async def get_decrypted_api_key(user_id: int, db: Optional[AsyncSession] = None) -> Optional[str]:
        """
        获取解密 API Key
        """
        session, is_local = await AuthService._get_session(db)
        try:
            user = await session.get(User, user_id)
            if not user or not user.api_key_encrypted:
                return None
            
            manager = get_api_key_manager()
            return manager.decrypt_api_key(user.api_key_encrypted)
        finally:
            if is_local:
                await session.close()

    @staticmethod
    async def register_online_state(api_key: str, user_data: Dict):
        """
        主动注册在线状态到 Redis
        """
        manager = get_api_key_manager()
        hashed_key = manager.hash_api_key(api_key)
        cache_key = f"auth:api_key:{hashed_key}"
        
        redis = await get_redis()
        if redis:
            # 确保 status 字段存在
            if "status" not in user_data:
                user_data["status"] = "1"
            
            await redis.hset(cache_key, mapping=user_data)
            await redis.expire(cache_key, 3600)
    
    @staticmethod
    async def verify_admin_login(api_key: str, db: Optional[AsyncSession] = None) -> Optional[Dict]:
        """校验管理员登录"""
        user = await AuthService.verify_api_key(api_key, db)
        if not user or user.get("role") != "admin":
            return None
        return user

    @staticmethod
    async def expire_api_key(api_key: str):
        """退出登录 (仅清缓存)"""
        manager = get_api_key_manager()
        hashed_key = manager.hash_api_key(api_key)
        cache_key = f"auth:api_key:{hashed_key}"
        redis = await get_redis()
        if redis:
            await redis.delete(cache_key)

    @staticmethod
    def verify_password_hash(plain_password: str, hashed_password: str) -> bool:
        if len(plain_password.encode('utf-8')) > 72:
            plain_password = plain_password.encode('utf-8')[:72].decode('utf-8', errors='ignore')
        return pwd_context.verify(plain_password, hashed_password)

    @staticmethod
    def get_password_hash(password: str) -> str:
        if len(password.encode('utf-8')) > 72:
            password = password.encode('utf-8')[:72].decode('utf-8', errors='ignore')
        return pwd_context.hash(password)

    @staticmethod
    async def verify_user_password(username: str, password: str, db: Optional[AsyncSession] = None) -> dict:
        """校验用户名密码"""
        session, is_local = await AuthService._get_session(db)
        try:
            result = await session.execute(select(User).where(User.user_name == username))
            user = result.scalar_one_or_none()
            
            if not user:
                return {"status": "fail", "message": "用户名或密码错误"}
            
            if user.status != 1:
                 return {"status": "fail", "message": "账户已被禁用"}

            if not user.password_hash:
                return {"status": "error_no_password", "message": "尚未设置密码，请先使用 API Key 登录并设置密码"}
            
            if AuthService.verify_password_hash(password, user.password_hash):
                return {
                    "status": "success",
                    "user": {
                         "user_id": str(user.id),
                         "user_name": user.user_name,
                         "real_name": user.real_name or user.user_name,
                         "role": user.role,
                         "dept_code": user.dept_code or "",
                         "org_path": user.org_path or "",
                         "extra_data": user.extra_data or "",
                         "created_at": user.created_at.strftime("%Y-%m-%d %H:%M:%S") if user.created_at else None,
                         "remark": user.remark or ""
                    }
                }
            else:
                return {"status": "fail", "message": "用户名或密码错误"}
        finally:
            if is_local:
                await session.close()

    @staticmethod
    async def set_user_password(user_id: int, password: str, db: Optional[AsyncSession] = None) -> bool:
        """设置用户密码"""
        session, is_local = await AuthService._get_session(db)
        try:
            hashed = AuthService.get_password_hash(password)
            stmt = update(User).where(User.id == user_id).values(password_hash=hashed)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0
        except Exception:
            await session.rollback()
            raise
        finally:
            if is_local:
                await session.close()

    @staticmethod
    async def authenticate_sso_user(username: str, password: str, db: Optional[AsyncSession] = None) -> dict:
        """
        SSO 认证逻辑
        1. 调用远程 SSO 接口
        2. 认证通过后，查询本地数据库映射权限
        """
        # 1. 调用 SSO 接口
        api_request = {
            'requestSystem': settings.SSO_REQUEST_SYSTEM,
            'requestBusiness': settings.SSO_REQUEST_BUSINESS,
            'operationType': 'LOGIN',
            'userName': username,
            'password': password
        }
        
        headers = {
            'YOVOLE-LAPLACE-API-ACCESS-TOKEN': settings.SSO_ACCESS_TOKEN,
            'Content-Type': 'application/json;charset=UTF-8'
        }

        try:
            async with httpx.AsyncClient(timeout=settings.SSO_TIMEOUT, verify=False) as client:
                response = await client.post(settings.SSO_API_URL, headers=headers, json=api_request)
                if response.status_code != 200:
                    return {"status": "fail", "message": f"SSO 服务响应异常: {response.status_code}"}
                
                resp_data = response.json()
                if not resp_data.get('data'):
                    return {"status": "fail", "message": "SSO 认证失败: 用户名或密码错误"}
        except httpx.RequestError as e:
            return {"status": "fail", "message": f"连接 SSO 服务失败: {str(e)}"}
        except Exception as e:
            return {"status": "fail", "message": f"SSO 认证过程发生错误: {str(e)}"}

        # 2. 认证通过后，查询本地数据库映射权限
        session, is_local = await AuthService._get_session(db)
        try:
            result = await session.execute(select(User).where(User.user_name == username))
            user = result.scalar_one_or_none()
            
            if not user:
                return {"status": "error_not_found", "message": "请联系管理员开通系统权限"}
            
            if user.status != 1:
                 return {"status": "error_disabled", "message": "账户已被禁用"}

            return {
                "status": "success",
                "user": {
                     "user_id": str(user.id),
                     "user_name": user.user_name,
                     "real_name": user.real_name or user.user_name,
                     "role": user.role,
                     "dept_code": user.dept_code or "",
                     "org_path": user.org_path or "",
                     "extra_data": user.extra_data or "",
                     "created_at": user.created_at.strftime("%Y-%m-%d %H:%M:%S") if user.created_at else None,
                     "remark": user.remark or ""
                }
            }
        finally:
            if is_local:
                await session.close()

    