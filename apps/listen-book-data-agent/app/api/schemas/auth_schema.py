from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserInfo(BaseModel):
    id: str
    username: str
    role: str
    must_change_password: bool


class LoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user: UserInfo


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)
