"""
AI 图像生成服务 - 集成 Stable Diffusion WebUI API
为视频生成提供高质量的场景图像

功能特性：
1. 智能提示词优化（基于风格）
2. 图像质量验证（颜色多样性、细节丰富度）
3. 缓存机制（减少重复生成）
4. 健康检查和自动重试
5. 训练数据自动收集（用于 LoRA 训练）
"""
import os
import json
import aiohttp
import asyncio
from typing import Optional, Dict, Any, Tuple
import logging
from pathlib import Path
import hashlib
import time
from PIL import Image
import numpy as np
import base64
from io import BytesIO

logger = logging.getLogger(__name__)

class SDImageGenerator:
    """Stable Diffusion 图像生成客户端"""
    
    def __init__(self, base_url: str = "http://localhost:7860"):
        self.base_url = base_url.rstrip("/")
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache_dir = Path("/tmp/sd_cache")
        self.cache_dir.mkdir(exist_ok=True)
        self.retry_count = 3
        self.retry_delay = 2
        self.timeout = aiohttp.ClientTimeout(total=300)  # 5分钟超时
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
            
    def _get_cache_key(self, prompt: str, width: int, height: int, **params) -> str:
        """生成缓存键"""
        key_data = f"{prompt}:{width}:{height}:{json.dumps(params, sort_keys=True)}"
        return hashlib.md5(key_data.encode()).hexdigest()
        
    async def generate_image(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 512,
        height: int = 512,
        steps: int = 20,
        cfg_scale: float = 7.0,
        sampler_name: str = "DPM++ 2M Karras",
        seed: int = -1,
        style: str = "cinematic",
        lora_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        调用 SD WebUI API 生成图像
        
        Args:
            prompt: 提示词
            negative_prompt: 负面提示词
            width: 宽度
            height: 高度
            steps: 采样步数
            cfg_scale: 提示词相关性
            sampler_name: 采样器
            seed: 随机种子，-1 表示随机
            style: 风格（用于优化提示词）
            lora_path: LoRA 权重路径
            
        Returns:
            Dict containing image_path, seed, metadata
        """
        if not self.session:
            self.session = aiohttp.ClientSession()
            
        # 根据风格优化提示词
        enhanced_prompt = self._enhance_prompt_for_style(prompt, style)
        
        # 构建请求参数
        payload = {
            "prompt": enhanced_prompt,
            "negative_prompt": negative_prompt or "low quality, blurry, distorted, ugly, deformed, bad anatomy",
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "sampler_name": sampler_name,
            "seed": seed,
            "batch_size": 1,
            "n_iter": 1,
            "enable_hr": False,
            "override_settings": {
                "sd_model_checkpoint": "v1-5-pruned-emaonly.safetensors",
            }
        }
        
        # 添加 LoRA 权重
        if lora_path and os.path.exists(lora_path):
            lora_name = os.path.basename(lora_path).replace(".safetensors", "")
            payload["prompt"] = f"<lora:{lora_name}:1.0>, {payload['prompt']}"
            
        # 检查缓存
        cache_key = self._get_cache_key(prompt, width, height, **{k: v for k, v in payload.items() if k != "seed"})
        cache_file = self.cache_dir / f"{cache_key}.json"
        
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text())
                if os.path.exists(cached["image_path"]):
                    logger.info(f"Using cached image for prompt: {prompt[:50]}...")
                    return cached
            except Exception as e:
                logger.warning(f"Cache read failed: {e}")
                
        # 重试机制
        for attempt in range(self.retry_count):
            try:
                # 调用 SD API
                async with self.session.post(
                    f"{self.base_url}/sdapi/v1/txt2img",
                    json=payload,
                    timeout=self.timeout
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        if attempt < self.retry_count - 1:
                            logger.warning(f"SD API attempt {attempt+1} failed: {response.status}, retrying...")
                            await asyncio.sleep(self.retry_delay * (attempt + 1))
                            continue
                        raise RuntimeError(f"SD API error {response.status}: {error_text[:200]}")
                        
                    result = await response.json()
                    
                    # 保存图像
                    image_data = result["images"][0]
                    image_bytes = base64.b64decode(image_data)
                    image = Image.open(BytesIO(image_bytes))
                    
                    # 生成文件名
                    timestamp = int(time.time())
                    filename = f"sd_gen_{timestamp}_{hashlib.md5(prompt.encode()).hexdigest()[:8]}.png"
                    output_dir = Path("/data/workspace/my-webui/backend/data/sd_generated")
                    output_dir.mkdir(parents=True, exist_ok=True)
                    image_path = output_dir / filename
                    
                    # 保存为 PNG（保留元数据）
                    image.save(image_path, "PNG")
                    
                    # 保存元数据
                    info = result.get("info", "{}")
                    try:
                        info_json = json.loads(info)
                    except:
                        info_json = {}
                        
                    metadata = {
                        "image_path": str(image_path),
                        "seed": result.get("seed", seed),
                        "prompt": prompt,
                        "enhanced_prompt": enhanced_prompt,
                        "negative_prompt": negative_prompt,
                        "width": width,
                        "height": height,
                        "steps": steps,
                        "cfg_scale": cfg_scale,
                        "sampler_name": sampler_name,
                        "style": style,
                        "generation_time": time.time(),
                        "sd_parameters": info_json,
                        "quality_score": 0,  # 将在验证后更新
                        "quality_reason": "pending validation",
                    }
                    
                    # 质量验证
                    is_valid, quality_score, quality_reason = await self.validate_image_quality(str(image_path))
                    metadata["quality_score"] = quality_score
                    metadata["quality_reason"] = quality_reason
                    metadata["is_valid"] = is_valid
                    
                    # 收集训练数据（如果质量合格）
                    if is_valid and quality_score >= 50:
                        await self._collect_training_sample(str(image_path), enhanced_prompt)
                    
                    # 写入缓存
                    cache_file.write_text(json.dumps(metadata, indent=2))
                    
                    return metadata
                    
            except asyncio.TimeoutError:
                if attempt < self.retry_count - 1:
                    logger.warning(f"SD API timeout attempt {attempt+1}, retrying...")
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
                    continue
                raise RuntimeError("SD API timeout after 300 seconds")
            except Exception as e:
                if attempt < self.retry_count - 1:
                    logger.warning(f"SD API error attempt {attempt+1}: {e}, retrying...")
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
                    continue
                logger.error(f"SD image generation failed after {self.retry_count} attempts: {e}")
                raise
            
    async def validate_image_quality(self, image_path: str) -> Tuple[bool, int, str]:
        """
        验证图像质量
        返回 (is_valid, quality_score, reason)
        """
        if not image_path or not os.path.exists(image_path):
            return False, 0, "image file not found"
            
        try:
            # 异步执行质量检查
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._validate_image_quality_sync, image_path)
            
        except Exception as e:
            return False, 0, f"validation error: {e}"
            
    def _validate_image_quality_sync(self, image_path: str) -> Tuple[bool, int, str]:
        """同步质量验证（在后台线程中执行）"""
        img = Image.open(image_path)
        arr = np.array(img)
        
        # 量化到 16 级每通道，计算独特颜色数
        arr_q = (arr // 16) * 16
        if arr_q.ndim == 3:
            unique_colors = len(np.unique(arr_q.reshape(-1, arr_q.shape[-1]), axis=0))
        else:
            unique_colors = len(np.unique(arr_q))
            
        # 计算像素标准差（越大表示细节越丰富）
        stddev = float(arr.std())
        
        # 综合评分：颜色多样性(70%) + 标准差(30%)
        color_score = min(70, int(unique_colors / 30))  # 2000色 -> 70分
        stddev_score = min(30, int(stddev / 3))  # 90 stddev -> 30分
        score = color_score + stddev_score
        
        # 质量阈值
        if unique_colors < 50:
            return False, score, f"low quality: only {unique_colors} unique colors (solid/gradient)"
        if stddev < 10:
            return False, score, f"low quality: stddev={stddev:.2f} (flat image)"
            
        return True, score, f"ok: {unique_colors} colors, stddev={stddev:.2f}"
        
    async def _collect_training_sample(self, image_path: str, prompt: str):
        """收集训练数据到数据集"""
        try:
            training_dir = Path("/data/workspace/my-webui/backend/data/training_samples")
            training_dir.mkdir(parents=True, exist_ok=True)
            
            # 复制图像
            import shutil
            import json as _json
            
            timestamp = int(time.time())
            dest_image = training_dir / f"sample_{timestamp}_{hashlib.md5(prompt.encode()).hexdigest()[:8]}.png"
            shutil.copy2(image_path, dest_image)
            
            # 追加到 metadata.jsonl
            metadata_path = training_dir / "metadata.jsonl"
            entry = {
                "image": str(dest_image),
                "prompt": prompt,
                "timestamp": timestamp,
                "quality_score": 0,  # 稍后更新
            }
            
            with open(metadata_path, "a") as f:
                f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
                
            # 统计数量
            count = 0
            if os.path.exists(metadata_path):
                with open(metadata_path, "r") as f:
                    count = sum(1 for line in f if line.strip())
                    
            logger.info(f"Training sample collected: {dest_image} (total: {count})")
            
            # 当积累到一定数量时提示
            if count >= 20 and count % 5 == 0:
                logger.info(f"Training data reached {count} samples, ready for LoRA training")
                
        except Exception as e:
            logger.warning(f"Training data collection failed: {e}")
            
    def _enhance_prompt_for_style(self, prompt: str, style: str) -> str:
        """根据风格优化提示词"""
        style_enhancements = {
            "cinematic": "movie still, cinematic, professional photography, depth of field, film grain, 35mm, anamorphic lens flare, masterpiece, best quality, detailed",
            "anime": "anime, masterpiece, best quality, detailed, vibrant colors, studio ghibli style, makoto shinkai, cel shading, anime art",
            "realistic": "photorealistic, hyperdetailed, 8k, ultra realistic, sharp focus, professional photo, masterpiece, best quality, detailed",
            "artistic": "digital art, concept art, illustration, artstation, trending on artstation, masterpiece, best quality, detailed, creative",
            "sci-fi": "science fiction, futuristic, cyberpunk, neon lights, holographic, tech, glowing, masterpiece, best quality, detailed",
            "fantasy": "fantasy art, magical, epic, mystical, ethereal, otherworldly, dreamlike, masterpiece, best quality, detailed",
        }
        
        enhancement = style_enhancements.get(style, "masterpiece, best quality, detailed")
        
        # 确保提示词包含必要的质量描述
        quality_tags = "masterpiece, best quality, detailed"
        if quality_tags not in prompt.lower():
            prompt = f"{quality_tags}, {prompt}"
            
        return f"{enhancement}, {prompt}"
        
    async def check_health(self) -> Tuple[bool, str]:
        """检查 SD WebUI 服务是否可用，返回 (is_healthy, message)"""
        try:
            if not self.session:
                self.session = aiohttp.ClientSession()
                
            async with self.session.get(
                f"{self.base_url}/sdapi/v1/sd-models",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    model_count = len(data) if isinstance(data, list) else 0
                    return True, f"SD WebUI healthy with {model_count} models"
                else:
                    return False, f"SD WebUI returned status {response.status}"
        except aiohttp.ClientError as e:
            return False, f"SD WebUI connection error: {str(e)}"
        except Exception as e:
            return False, f"SD WebUI health check error: {str(e)}"
            
    async def get_available_models(self) -> list:
        """获取可用的 SD 模型列表"""
        try:
            if not self.session:
                self.session = aiohttp.ClientSession()
                
            async with self.session.get(
                f"{self.base_url}/sdapi/v1/sd-models",
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    models = await response.json()
                    return [model.get("title", model.get("model_name", "Unknown")) for model in models]
        except:
            pass
        return []


# 全局实例
_sd_generator: Optional[SDImageGenerator] = None

async def get_sd_generator() -> SDImageGenerator:
    """获取或创建 SD 生成器实例"""
    global _sd_generator
    if _sd_generator is None:
        _sd_generator = SDImageGenerator()
    return _sd_generator

async def generate_scene_image(
    prompt: str,
    style: str = "cinematic",
    width: int = 512,
    height: int = 512,
    lora_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    生成场景图像（主要接口）
    
    Args:
        prompt: 提示词
        style: 风格
        width: 宽度
        height: 高度
        lora_path: LoRA 权重路径
        
    Returns:
        图像元数据
    """
    generator = await get_sd_generator()
    
    # 根据风格选择负面提示词
    negative_prompts = {
        "cinematic": "bad photography, blurry, out of focus, amateur, flat lighting, bad composition",
        "anime": "bad anatomy, deformed, disfigured, poorly drawn face, mutation, extra limbs",
        "realistic": "unrealistic, cartoon, anime, painting, drawing, digital art, 3d render",
        "artistic": "photographic, realistic, photo, mundane, boring, plain",
        "sci-fi": "historical, vintage, old-fashioned, traditional, mundane, realistic",
    }
    
    negative_prompt = negative_prompts.get(style, "low quality, blurry, ugly, deformed, bad anatomy")
    
    return await generator.generate_image(
        prompt=prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        style=style,
        lora_path=lora_path,
    )

async def validate_image_quality(image_path: str) -> Tuple[bool, int, str]:
    """
    验证图像质量（外部接口）
    返回 (is_valid, quality_score, reason)
    """
    generator = await get_sd_generator()
    return await generator.validate_image_quality(image_path)

async def check_sd_health() -> Tuple[bool, str]:
    """检查 SD 服务健康状态"""
    generator = await get_sd_generator()
    return await generator.check_health()

async def get_sd_models() -> list:
    """获取 SD 模型列表"""
    generator = await get_sd_generator()
    return await generator.get_available_models()