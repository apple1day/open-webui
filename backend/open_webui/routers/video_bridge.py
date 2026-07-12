"""
Video Bridge Router
将前端请求代理到 video_large_model_project 后端 (port 8000)
提供视频QA、相似搜索、训练状态、系统监控等高级功能
"""
import os
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from open_webui.utils.auth import get_verified_user

router = APIRouter()

# 大模型项目后端地址
LARGE_MODEL_BACKEND = os.getenv("LARGE_MODEL_BACKEND_URL", "http://localhost:8000")

# 大模型项目的认证token（使用admin用户的长期token）
LARGE_MODEL_TOKEN = os.getenv("LARGE_MODEL_TOKEN", "")

# 大模型项目的JWT配置（与video_large_model_project/app/auth.py保持一致）
_LM_SECRET_KEY = "your-secret-key-here-change-in-production"
_LM_ALGORITHM = "HS256"


def _get_large_model_token() -> str:
    """获取大模型项目的认证token（直接生成JWT，不依赖video_large_model_project模块）"""
    global LARGE_MODEL_TOKEN
    if LARGE_MODEL_TOKEN:
        return LARGE_MODEL_TOKEN
    try:
        import jwt
        from datetime import datetime, timedelta, timezone
        expire = datetime.now(timezone.utc) + timedelta(days=30)
        payload = {"sub": "1", "exp": expire}
        token = jwt.encode(payload, _LM_SECRET_KEY, algorithm=_LM_ALGORITHM)
        LARGE_MODEL_TOKEN = token
        return token
    except Exception as e:
        print(f"[video_bridge] Failed to generate token: {e}")
        return ""


async def _proxy_request(
    method: str,
    path: str,
    user=Depends(get_verified_user),
    json_body: dict = None,
    params: dict = None,
) -> dict:
    """代理请求到大模型项目后端"""
    token = _get_large_model_token()
    url = f"{LARGE_MODEL_BACKEND}{path}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if method == "GET":
                resp = await client.get(url, headers=headers, params=params)
            elif method == "POST":
                resp = await client.post(url, headers=headers, json=json_body, params=params)
            elif method == "DELETE":
                resp = await client.delete(url, headers=headers, params=params)
            else:
                raise HTTPException(status_code=405, detail=f"Method {method} not supported")

            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Large model backend not available: {path}")
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Large model backend (port 8000) is not running")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Large model backend timeout")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# 视频QA问答
# ==========================================
class QARequest(BaseModel):
    video_id: int
    question: str


@router.post("/qa")
async def video_qa(request: QARequest, user=Depends(get_verified_user)):
    """视频问答 - 提交问答分析任务到大模型项目"""
    return await _proxy_request("POST", "/api/analysis/qa", user, json_body=request.model_dump())


@router.get("/analysis/list")
async def list_analyses(
    skip: int = 0,
    limit: int = 20,
    user=Depends(get_verified_user),
):
    """获取分析任务列表"""
    return await _proxy_request("GET", "/api/analysis/list", user, params={"skip": skip, "limit": limit})


@router.get("/analysis/{analysis_id}")
async def get_analysis(analysis_id: int, user=Depends(get_verified_user)):
    """获取分析任务详情"""
    return await _proxy_request("GET", f"/api/analysis/{analysis_id}", user)


# ==========================================
# 相似视频搜索
# ==========================================
class SimilarSearchRequest(BaseModel):
    video_id: int
    top_k: int = 5
    threshold: float = 0.5


@router.post("/search/similar")
async def search_similar(request: SimilarSearchRequest, user=Depends(get_verified_user)):
    """基于特征向量搜索相似视频"""
    return await _proxy_request("POST", "/api/search/similar", user, json_body=request.model_dump())


@router.get("/search/features/{video_id}")
async def get_video_features(video_id: int, user=Depends(get_verified_user)):
    """获取视频特征向量"""
    return await _proxy_request("GET", f"/api/search/features/{video_id}", user)


@router.post("/search/features/batch")
async def batch_extract_features(request: Request, user=Depends(get_verified_user)):
    """批量提取视频特征"""
    body = await request.json()
    return await _proxy_request("POST", "/api/search/features/batch", user, json_body=body)


# ==========================================
# 训练状态与数据
# ==========================================
@router.get("/training/status")
async def training_status(user=Depends(get_verified_user)):
    """获取训练状态"""
    return await _proxy_request("GET", "/api/training/status", user)


@router.get("/training/progress")
async def training_progress(
    output_name: Optional[str] = None,
    user=Depends(get_verified_user),
):
    """获取训练实时进度"""
    params = {}
    if output_name:
        params["output_name"] = output_name
    return await _proxy_request("GET", "/api/training/progress", user, params=params)


@router.get("/training/data/preview")
async def training_data_preview(user=Depends(get_verified_user)):
    """预览训练数据"""
    return await _proxy_request("GET", "/api/training/data/preview", user)


@router.get("/training/history")
async def training_history(
    skip: int = 0,
    limit: int = 10,
    user=Depends(get_verified_user),
):
    """获取训练历史"""
    return await _proxy_request("GET", "/api/training/history", user, params={"skip": skip, "limit": limit})


@router.post("/training/start")
async def start_training(request: Request, user=Depends(get_verified_user)):
    """手动触发训练"""
    body = await request.json()
    return await _proxy_request("POST", "/api/training/start", user, json_body=body)


@router.get("/training/weights")
async def list_lora_weights(user=Depends(get_verified_user)):
    """列出LoRA权重"""
    return await _proxy_request("GET", "/api/training/weights", user)


# ==========================================
# 系统监控
# ==========================================
@router.get("/monitor/health")
async def system_health(user=Depends(get_verified_user)):
    """系统健康检查"""
    return await _proxy_request("GET", "/api/monitor/health", user)


@router.get("/monitor/celery/status")
async def celery_status(user=Depends(get_verified_user)):
    """Celery集群状态"""
    return await _proxy_request("GET", "/api/monitor/celery/status", user)


@router.get("/monitor/stats/tasks")
async def task_stats(user=Depends(get_verified_user)):
    """任务统计"""
    return await _proxy_request("GET", "/api/monitor/stats/tasks", user)


# ==========================================
# 视频列表（从大模型项目获取已上传的视频）
# ==========================================
@router.get("/videos/list")
async def list_videos(
    skip: int = 0,
    limit: int = 20,
    user=Depends(get_verified_user),
):
    """获取大模型项目中的视频列表"""
    return await _proxy_request("GET", "/api/videos/", user, params={"skip": skip, "limit": limit})


# ==========================================
# L4: 数据集管理（闭环联动 - 分析结果入库）
# ==========================================
@router.get("/dataset/stats")
async def dataset_stats(user=Depends(get_verified_user)):
    """数据集统计信息"""
    return await _proxy_request("GET", "/api/dataset/stats", user)


@router.get("/dataset/items")
async def list_dataset_items(
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    video_id: Optional[int] = None,
    user=Depends(get_verified_user),
):
    """数据集条目列表"""
    params = {"skip": skip, "limit": limit}
    if status:
        params["status"] = status
    if video_id:
        params["video_id"] = video_id
    return await _proxy_request("GET", "/api/dataset/items", user, params=params)


@router.get("/dataset/items/{item_id}")
async def get_dataset_item(item_id: int, user=Depends(get_verified_user)):
    """获取数据集条目详情"""
    return await _proxy_request("GET", f"/api/dataset/items/{item_id}", user)


@router.put("/dataset/items/{item_id}")
async def update_dataset_item(item_id: int, request: Request, user=Depends(get_verified_user)):
    """更新数据集条目（修改描述、评分、状态等）"""
    body = await request.json()
    return await _proxy_request("PUT", f"/api/dataset/items/{item_id}", user, json_body=body)


@router.delete("/dataset/items/{item_id}")
async def delete_dataset_item(item_id: int, user=Depends(get_verified_user)):
    """删除数据集条目"""
    return await _proxy_request("DELETE", f"/api/dataset/items/{item_id}", user)


@router.post("/dataset/preprocess/{video_id}")
async def trigger_preprocess(video_id: int, user=Depends(get_verified_user)):
    """触发视频预处理（场景检测、片段切割、质量评分）→ 自动入库"""
    return await _proxy_request("POST", f"/api/dataset/preprocess/{video_id}", user)


@router.post("/dataset/items/batch-delete")
async def batch_delete_dataset_items(request: Request, user=Depends(get_verified_user)):
    """批量删除数据集条目"""
    body = await request.json()
    return await _proxy_request("POST", "/api/dataset/items/batch-delete", user, json_body=body)


# ==========================================
# L4: 生成任务管理（闭环联动 - 生成视频质量评估）
# ==========================================
@router.get("/generation/list")
async def list_generations(
    skip: int = 0,
    limit: int = 20,
    status: Optional[str] = None,
    user=Depends(get_verified_user),
):
    """获取生成任务列表（含质量评分）"""
    params = {"skip": skip, "limit": limit}
    if status:
        params["status"] = status
    return await _proxy_request("GET", "/api/generation/list", user, params=params)


@router.get("/generation/status/{generation_id}")
async def get_generation_status(generation_id: int, user=Depends(get_verified_user)):
    """获取生成进度（含quality_score）"""
    return await _proxy_request("GET", f"/api/generation/status/{generation_id}", user)


@router.get("/generation-templates")
async def list_templates(user=Depends(get_verified_user)):
    """获取提示词模板"""
    return await _proxy_request("GET", "/api/generation/templates", user)


@router.get("/generation/{generation_id}")
async def get_generation_detail(generation_id: int, user=Depends(get_verified_user)):
    """获取生成任务详情"""
    return await _proxy_request("GET", f"/api/generation/{generation_id}", user)


@router.post("/generation/create")
async def create_generation(request: Request, user=Depends(get_verified_user)):
    """创建视频生成任务"""
    body = await request.json()
    return await _proxy_request("POST", "/api/generation/create", user, json_body=body)


@router.post("/generation/enhance")
async def enhance_prompt(
    prompt: str,
    style: str = "cinematic",
    user=Depends(get_verified_user),
):
    """提示词增强"""
    return await _proxy_request("POST", "/api/generation/enhance", user, params={"prompt": prompt, "style": style})


@router.delete("/generation/{generation_id}")
async def delete_generation(generation_id: int, user=Depends(get_verified_user)):
    """删除生成任务"""
    return await _proxy_request("DELETE", f"/api/generation/{generation_id}", user)


# ==========================================
# L4: LoRA权重高级管理
# ==========================================
@router.get("/training/weights/latest")
async def get_latest_weight(user=Depends(get_verified_user)):
    """获取当前正在使用的最新LoRA权重信息"""
    return await _proxy_request("GET", "/api/training/weights/latest", user)


@router.delete("/training/weights/{weight_name}")
async def delete_lora_weight(weight_name: str, user=Depends(get_verified_user)):
    """删除LoRA权重"""
    return await _proxy_request("DELETE", f"/api/training/weights/{weight_name}", user)


@router.post("/training/weights/cleanup")
async def cleanup_old_weights(keep: int = 3, user=Depends(get_verified_user)):
    """清理旧LoRA权重，保留最近N个"""
    return await _proxy_request("POST", "/api/training/weights/cleanup", user, params={"keep": keep})


@router.get("/training/logs/{output_name}")
async def get_training_log(output_name: str, user=Depends(get_verified_user)):
    """获取指定训练的完整日志"""
    return await _proxy_request("GET", f"/api/training/logs/{output_name}", user)


# ==========================================
# L4: 自动训练配置
# ==========================================
@router.get("/training/auto-train/config")
async def get_auto_train_config(user=Depends(get_verified_user)):
    """获取自动训练配置（阈值、步数等）"""
    return await _proxy_request("GET", "/api/training/auto-train/config", user)


@router.post("/training/auto-train/config")
async def update_auto_train_config(
    request: Request,
    user=Depends(get_verified_user),
):
    """更新自动训练配置"""
    body = await request.json()
    params = {
        "enabled": body.get("enabled", True),
        "threshold": body.get("threshold", 20),
        "max_train_steps": body.get("max_train_steps", 100),
    }
    return await _proxy_request("POST", "/api/training/auto-train/config", user, params=params)


@router.get("/training/schedule")
async def get_schedule_config(user=Depends(get_verified_user)):
    """获取定时训练配置"""
    return await _proxy_request("GET", "/api/training/schedule", user)


@router.post("/training/schedule")
async def set_schedule_config(request: Request, user=Depends(get_verified_user)):
    """设置定时训练配置"""
    body = await request.json()
    return await _proxy_request("POST", "/api/training/schedule", user, json_body=body)


# ==========================================
# L4: 闭环流水线状态总览
# ==========================================
@router.get("/pipeline/status")
async def pipeline_status(user=Depends(get_verified_user)):
    """
    闭环流水线状态总览：
    汇总数据集统计、训练状态、生成质量、LoRA权重等信息
    形成完整的 分析→数据集→训练→生成→质量反馈 闭环视图
    """
    try:
        # 并行获取各环节数据
        dataset_stats = await _proxy_request("GET", "/api/dataset/stats", user)
        training_status = await _proxy_request("GET", "/api/training/status", user)
        auto_train_config = await _proxy_request("GET", "/api/training/auto-train/config", user)
        latest_weight = await _proxy_request("GET", "/api/training/weights/latest", user)
    except Exception as e:
        # 部分失败不影响整体
        dataset_stats = {}
        training_status = {}
        auto_train_config = {}
        latest_weight = {}

    # 获取最近生成任务（含质量评分）
    try:
        generations = await _proxy_request("GET", "/api/generation/list", user, params={"limit": 10})
        if isinstance(generations, list):
            completed_gens = [g for g in generations if g.get("status") == "completed"]
            avg_quality = 0
            if completed_gens:
                scores = [g.get("quality_score", 0) or 0 for g in completed_gens]
                avg_quality = sum(scores) / len(scores) if scores else 0
            gen_summary = {
                "total": len(generations),
                "completed": len(completed_gens),
                "avg_quality": round(avg_quality, 2),
            }
        else:
            gen_summary = {"total": 0, "completed": 0, "avg_quality": 0}
    except Exception:
        gen_summary = {"total": 0, "completed": 0, "avg_quality": 0}

    # 获取最近分析任务
    try:
        analyses = await _proxy_request("GET", "/api/analysis/list", user, params={"limit": 10})
        if isinstance(analyses, dict):
            analysis_items = analyses.get("items", [])
        else:
            analysis_items = analyses if isinstance(analyses, list) else []
        completed_analyses = [a for a in analysis_items if a.get("status") == "completed"]
        analysis_summary = {
            "total": len(analysis_items),
            "completed": len(completed_analyses),
        }
    except Exception:
        analysis_summary = {"total": 0, "completed": 0}

    return {
        "dataset": dataset_stats,
        "training": {
            "is_training": training_status.get("is_training", False),
            "lora_count": len(training_status.get("lora_weights", [])),
            "training_data_count": training_status.get("training_data_count", 0),
        },
        "auto_train": auto_train_config,
        "latest_weight": latest_weight,
        "generation": gen_summary,
        "analysis": analysis_summary,
        "pipeline_active": auto_train_config.get("enabled", False) if isinstance(auto_train_config, dict) else False,
    }