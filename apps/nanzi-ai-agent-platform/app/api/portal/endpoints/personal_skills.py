"""个人技能管理 API — agent_workspaces/{user_key}/skills/{skill_id}/

权限：仅需已登录（require_api_key），用户只能操作自己的技能，无需 skills_management 菜单权限。
路由挂载于 /api/portal/skills/personal（在 api.py 中注册为 personal_skills router）。
"""

import os
import shutil
import logging
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form

from app.core.dependencies import require_api_key
from app.services.ai.skill_resolver import get_user_personal_skills_dir

# 复用平台技能中的辅助函数
from app.api.portal.endpoints.skills import (
    SkillCreateRequest,
    FileEditRequest,
    SkillAssetCreateRequest,
    EDITABLE_TEXT_EXTENSIONS,
    parse_skill_metadata,
    get_file_tree,
    get_skill_modified_at,
    safe_extract_archive,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# 内部辅助：推导并校验个人技能目录
# ---------------------------------------------------------------------------

def _get_personal_skills_root(user_info: dict) -> str:
    """获取当前用户的个人技能根目录，不存在则自动创建。"""
    skills_dir = get_user_personal_skills_dir(user_info)
    if not skills_dir:
        raise HTTPException(status_code=400, detail="无法解析用户工作区，请确认账号信息完整")
    os.makedirs(skills_dir, exist_ok=True)
    return skills_dir


def _validate_personal_skill_path(
    user_info: dict,
    skill_id: str,
    relative_path: str = "",
) -> str:
    """校验 skill_id 和可选的 relative_path，防御路径穿越攻击，返回绝对路径。"""
    if not skill_id or "/" in skill_id or "\\" in skill_id or ".." in skill_id:
        raise HTTPException(status_code=400, detail="非法个人技能 ID 格式")
    if not re.match(r"^[a-zA-Z0-9_-]+$", skill_id):
        raise HTTPException(status_code=400, detail="技能 ID 只能包含字母、数字、下划线和连字符")

    skills_root = _get_personal_skills_root(user_info)
    skill_base = os.path.abspath(os.path.join(skills_root, skill_id))

    if os.path.commonpath([skills_root, skill_base]) != skills_root:
        raise HTTPException(status_code=403, detail="安全拦截：个人技能路径越界")

    if not relative_path:
        return skill_base

    target = os.path.abspath(os.path.join(skill_base, relative_path.lstrip("/")))
    if os.path.commonpath([skill_base, target]) != skill_base:
        raise HTTPException(status_code=403, detail="安全拦截：文件子路径越界")
    return target


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------

@router.get("", summary="列出当前用户的个人技能")
async def list_personal_skills(user: dict = Depends(require_api_key)):
    """扫描用户个人技能目录，解析 SKILL.md 返回列表。"""
    skills_root = _get_personal_skills_root(user)
    skills_list = []
    try:
        for item in sorted(os.listdir(skills_root)):
            item_path = os.path.join(skills_root, item)
            if os.path.isdir(item_path) and not item.startswith("."):
                skill_md = os.path.join(item_path, "SKILL.md")
                meta = parse_skill_metadata(item, skill_md)
                meta["scope"] = "personal"
                meta["modified_at"] = get_skill_modified_at(item_path, skill_md)
                skills_list.append(meta)
    except Exception as e:
        logger.error("[PersonalSkills] Failed to list skills: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success", "data": skills_list}


# ---------------------------------------------------------------------------
# 新建
# ---------------------------------------------------------------------------

@router.post("", summary="新建个人技能")
async def create_personal_skill(
    req: SkillCreateRequest,
    user: dict = Depends(require_api_key),
):
    """在用户个人目录下新建技能文件夹并初始化默认 SKILL.md。"""
    try:
        skill_dir = _validate_personal_skill_path(user, req.id)
        if os.path.exists(skill_dir):
            raise HTTPException(status_code=400, detail="该个人技能 ID 已存在")

        os.makedirs(skill_dir, exist_ok=True)
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        default_content = (
            f"---\n"
            f"name: {req.name}\n"
            f"description: {req.description}\n"
            f"---\n\n"
            f"# {req.name}\n\n"
            f"在此处编写该技能的具体战术规范、操作流程与 <HARD-GATE> 红线守则。\n"
        )
        with open(skill_md_path, "w", encoding="utf-8") as f:
            f.write(default_content)
        return {"status": "success", "message": f"个人技能 {req.id} 创建成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to create skill %s: %s", req.id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 详情
# ---------------------------------------------------------------------------

@router.get("/{skill_id}", summary="获取个人技能详情")
async def get_personal_skill_detail(
    skill_id: str,
    user: dict = Depends(require_api_key),
):
    """获取个人技能的 SKILL.md 内容和文件目录树。"""
    try:
        skill_dir = _validate_personal_skill_path(user, skill_id)
        if not os.path.exists(skill_dir) or not os.path.isdir(skill_dir):
            raise HTTPException(status_code=404, detail="个人技能未找到")

        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        skill_md_content = ""
        if os.path.exists(skill_md_path):
            with open(skill_md_path, "r", encoding="utf-8") as f:
                skill_md_content = f.read()

        meta = parse_skill_metadata(skill_id, skill_md_path)
        file_tree = get_file_tree(skill_dir, skill_dir)
        return {
            "status": "success",
            "data": {
                "id": skill_id,
                "name": meta.get("name", skill_id),
                "description": meta.get("description", ""),
                "scope": "personal",
                "skill_md_content": skill_md_content,
                "file_tree": file_tree,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to fetch detail for %s: %s", skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 预览（供聊天页技能抽屉使用）
# ---------------------------------------------------------------------------

@router.get("/{skill_id}/preview", summary="预览个人技能 SKILL.md")
async def preview_personal_skill(
    skill_id: str,
    user: dict = Depends(require_api_key),
):
    try:
        skill_dir = _validate_personal_skill_path(user, skill_id)
        if not os.path.exists(skill_dir) or not os.path.isdir(skill_dir):
            raise HTTPException(status_code=404, detail="个人技能未找到")

        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        skill_md_content = ""
        if os.path.exists(skill_md_path):
            with open(skill_md_path, "r", encoding="utf-8") as f:
                skill_md_content = f.read()

        meta = parse_skill_metadata(skill_id, skill_md_path)
        return {
            "status": "success",
            "data": {
                "id": skill_id,
                "name": meta.get("name", skill_id),
                "description": meta.get("description", ""),
                "scope": "personal",
                "skill_md_content": skill_md_content,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to preview %s: %s", skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 读取文件
# ---------------------------------------------------------------------------

@router.get("/{skill_id}/files", summary="读取个人技能文件内容")
async def get_personal_skill_file(
    skill_id: str,
    path: str,
    user: dict = Depends(require_api_key),
):
    try:
        file_path = _validate_personal_skill_path(user, skill_id, path)
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="文件不存在")

        _, ext = os.path.splitext(file_path.lower())
        if ext not in EDITABLE_TEXT_EXTENSIONS:
            raise HTTPException(status_code=400, detail="只允许在线读取文本类文件")

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"status": "success", "content": content}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to read file %s in %s: %s", path, skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 编辑文件
# ---------------------------------------------------------------------------

@router.put("/{skill_id}/files", summary="编辑个人技能文件")
async def edit_personal_skill_file(
    skill_id: str,
    req: FileEditRequest,
    user: dict = Depends(require_api_key),
):
    try:
        file_path = _validate_personal_skill_path(user, skill_id, req.path)
        _, ext = os.path.splitext(file_path.lower())
        if ext not in EDITABLE_TEXT_EXTENSIONS:
            raise HTTPException(status_code=400, detail="只允许在线编辑文本类文件")
        if len(req.content.encode("utf-8")) > 2 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="文件大小超出 2MB 在线编辑限制")

        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {"status": "success", "message": "保存成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to edit file %s in %s: %s", req.path, skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 新建资产（文件/文件夹）
# ---------------------------------------------------------------------------

@router.post("/{skill_id}/files", summary="在个人技能目录内新建文件或文件夹")
async def create_personal_skill_asset(
    skill_id: str,
    req: SkillAssetCreateRequest,
    user: dict = Depends(require_api_key),
):
    try:
        if not req.path or not req.path.strip():
            raise HTTPException(status_code=400, detail="资产名称不能为空")
        if os.path.isabs(req.path) or req.path.startswith("/"):
            raise HTTPException(status_code=400, detail="资产路径必须是相对路径")

        target_path = _validate_personal_skill_path(user, skill_id, req.path)
        if req.type == "file":
            _, ext = os.path.splitext(target_path.lower())
            if ext not in EDITABLE_TEXT_EXTENSIONS:
                raise HTTPException(status_code=400, detail="只允许新建可在线编辑的文本类文件")

        parent_path = os.path.dirname(target_path)
        if not os.path.isdir(parent_path):
            raise HTTPException(status_code=400, detail="目标父文件夹不存在")
        if os.path.exists(target_path):
            raise HTTPException(status_code=409, detail="同名文件或文件夹已存在")

        if req.type == "folder":
            os.mkdir(target_path)
        else:
            with open(target_path, "x", encoding="utf-8"):
                pass
        return {"status": "success", "message": "创建成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to create asset %s in %s: %s", req.path, skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 上传文件
# ---------------------------------------------------------------------------

@router.post("/{skill_id}/upload", summary="上传文件到个人技能目录")
async def upload_personal_skill_file(
    skill_id: str,
    folder: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user: dict = Depends(require_api_key),
):
    try:
        sub_dir = folder if folder else ""
        file_name = file.filename if file.filename else "uploaded_file"
        target_path = _validate_personal_skill_path(
            user, skill_id, os.path.join(sub_dir, file_name)
        )
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="上传文件大小超出 10MB 限制")

        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "wb") as f:
            f.write(content)
        return {"status": "success", "message": f"文件 {file_name} 上传成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to upload file in %s: %s", skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{skill_id}/upload-archive", summary="上传压缩包并解压到个人技能目录")
async def upload_personal_skill_archive(
    skill_id: str,
    folder: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user: dict = Depends(require_api_key),
):
    try:
        sub_dir = folder if folder else ""
        target_dir = _validate_personal_skill_path(user, skill_id, sub_dir)
        content = await file.read()
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="上传的压缩文件大小不能超出 20MB 限制")

        file_name = file.filename if file.filename else "archive.zip"
        safe_extract_archive(content, file_name, target_dir)
        return {"status": "success", "message": f"技能压缩包 {file_name} 上传解压成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to upload archive in %s: %s", skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 删除文件
# ---------------------------------------------------------------------------

@router.delete("/{skill_id}/files", summary="删除个人技能目录下的文件或子文件夹")
async def delete_personal_skill_file(
    skill_id: str,
    path: str,
    user: dict = Depends(require_api_key),
):
    try:
        target_path = _validate_personal_skill_path(user, skill_id, path)
        if not os.path.exists(target_path):
            raise HTTPException(status_code=404, detail="目标文件不存在")
        if path.strip().lower() == "skill.md":
            raise HTTPException(status_code=400, detail="禁止直接删除核心规范文件 SKILL.md")

        if os.path.isdir(target_path):
            shutil.rmtree(target_path)
        else:
            os.remove(target_path)
        return {"status": "success", "message": "删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to delete %s in %s: %s", path, skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 删除整个技能
# ---------------------------------------------------------------------------

@router.delete("/{skill_id}", summary="彻底删除个人技能")
async def delete_personal_skill(
    skill_id: str,
    user: dict = Depends(require_api_key),
):
    try:
        skill_dir = _validate_personal_skill_path(user, skill_id)
        if not os.path.exists(skill_dir):
            raise HTTPException(status_code=404, detail="个人技能目录不存在")
        shutil.rmtree(skill_dir)
        return {"status": "success", "message": f"个人技能 {skill_id} 已物理删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to delete skill %s: %s", skill_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 导入压缩包
# ---------------------------------------------------------------------------

@router.post("/import", summary="导入压缩包为个人技能")
async def import_personal_skill_package(
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
    user: dict = Depends(require_api_key),
):
    """上传 zip/tar 压缩包，解压到个人技能目录。解包后必须包含 SKILL.md。"""
    try:
        filename = file.filename if file.filename else "imported_skill.zip"
        base_name, _ = os.path.splitext(filename)
        if base_name.lower().endswith(".tar"):
            base_name, _ = os.path.splitext(base_name)

        skill_id = re.sub(r"[^a-zA-Z0-9_\-]", "", base_name).strip()
        if not skill_id:
            raise HTTPException(status_code=400, detail="无法从文件名中提取合法的技能 ID")

        skill_dir = _validate_personal_skill_path(user, skill_id)
        if os.path.exists(skill_dir) and not overwrite:
            raise HTTPException(
                status_code=400,
                detail=f"个人技能 {skill_id} 已存在，请开启 overwrite 以覆盖导入",
            )

        content = await file.read()
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="导入文件大小超出 20MB 限制")

        if os.path.exists(skill_dir):
            shutil.rmtree(skill_dir)
        os.makedirs(skill_dir, exist_ok=True)

        try:
            safe_extract_archive(content, filename, skill_dir)

            # 平铺外层文件夹
            top_items = os.listdir(skill_dir)
            visible_items = [i for i in top_items if not i.startswith(".")]
            if len(visible_items) == 1:
                single_sub = os.path.join(skill_dir, visible_items[0])
                if os.path.isdir(single_sub):
                    has_inner_skill_md = any(
                        i.upper() == "SKILL.MD" and os.path.isfile(os.path.join(single_sub, i))
                        for i in os.listdir(single_sub)
                    )
                    if has_inner_skill_md:
                        for item in os.listdir(single_sub):
                            shutil.move(os.path.join(single_sub, item), os.path.join(skill_dir, item))
                        os.rmdir(single_sub)

            has_skill_md = any(
                i.upper() == "SKILL.MD" and os.path.isfile(os.path.join(skill_dir, i))
                for i in os.listdir(skill_dir)
            )
            if not has_skill_md:
                shutil.rmtree(skill_dir)
                raise HTTPException(
                    status_code=400,
                    detail="导入失败：压缩包根目录下必须包含核心规范文件 SKILL.md",
                )
        except Exception as e:
            if os.path.exists(skill_dir):
                shutil.rmtree(skill_dir)
            raise e

        return {"status": "success", "message": f"个人技能 {skill_id} 导入成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PersonalSkills] Failed to import skill package: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{skill_id}/toggle", summary="启用或禁用个人技能")
async def toggle_personal_skill(
    skill_id: str,
    enabled: bool,
    user: dict = Depends(require_api_key),
):
    try:
        skill_dir = _validate_personal_skill_path(user, skill_id)
        if not os.path.exists(skill_dir):
            raise HTTPException(status_code=404, detail="个人技能不存在")
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        
        from app.utils.skill_metadata import toggle_skill_enabled_in_file
        success = toggle_skill_enabled_in_file(skill_md_path, enabled)
        if not success:
            raise HTTPException(status_code=500, detail="切换状态失败，请确认技能文件格式正常")
        return {"status": "success", "message": f"技能已{'启用' if enabled else '禁用'}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PersonalSkills] Failed to toggle skill {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
