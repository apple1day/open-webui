好的，让我帮你查看如何配置自动化分析目录下的所有视频。首先让我查看一下当前的配置文件和代码，了解目录批量分析的功能：
好的，我来帮你查看如何配置自动化分析目录下所有视频。根据代码和文档，系统已经支持这个功能了。让我给你展示完整的配置方法：

## 📁 配置自动化分析目录下的所有视频

### 方法1：使用 `video_dir` 配置（推荐）

在 `video-tasks.json` 中，你可以使用 `video_dir` 字段来指定要分析的目录：

```json
{
  "ollama_url": "http://localhost:11434",
  "default_model": "minicpm-v:latest",
  "default_summary_model": "qwen2.5:14b",
  "language": "zh",
  "include_audio": false,
  "whisper_model": "base",
  
  "concurrency": 3,
  "cache_enabled": true,
  "dedup_enabled": true,
  
  "tasks": [
    {
      "video_dir": "/data/workspace/data",
      "recursive": false,
      "model": "minicpm-v:latest",
      "summary_model": "qwen2.5:14b",
      "language": "zh",
      "sample_mode": "auto",
      "max_frames": 20,
      "min_frames": 20,
      "include_audio": true,
      "whisper_model": "small",
      "save_report": true,
      "concurrency": 3,
      "cache_enabled": true,
      "dedup_enabled": true
    }
  ]
}
```

### 方法2：使用命令行参数

你也可以使用 `--video-dir` 命令行参数：

```bash
cd /data/workspace/my-webui

# 分析单个目录（不递归子目录）
./venv/bin/python scripts/video_tasks.py --video-dir /data/workspace/data

# 递归分析目录（包括子目录）
./venv/bin/python scripts/video_tasks.py --video-dir /data/workspace/data --recursive

# 跳过已分析过的视频
./venv/bin/python scripts/video_tasks.py --video-dir /data/workspace/data --skip-existing
```

### 配置参数说明

| 参数 | 说明 | 示例 |
|------|------|------|
| `video_dir` | 要分析的目录路径 | `/data/workspace/data` |
| `recursive` | 是否递归分析子目录 | `true` / `false` |
| `skip_existing` | 跳过已生成报告的视频 | `true` / `false` |

### 完整配置示例

让我帮你创建一个完整的配置示例：
完美！我已经为你创建了完整的目录批量分析配置。现在让我详细说明如何使用：

## 📋 配置说明

### ✅ 已配置的功能

1. **目录批量分析**：`video_dir: "/data/workspace/data"`
   - 自动分析该目录下的所有视频文件
   - `recursive: false` → 不递归子目录（如需递归改为 `true`）

2. **第三阶段新功能**：
   - ✅ **并发处理**：`concurrency: 3`（同时分析3个帧）
   - ✅ **缓存优化**：`cache_enabled: true`（避免重复分析）
   - ✅ **内容去重**：`dedup_enabled: true`（去除相似帧）
   - ✅ **GPU加速**：`gpu_enabled: true`（自动检测并启用）

3. **增量分析**：`skip_existing: true`
   - 跳过已生成分析报告的视频
   - 非常适合定期运行，只分析新视频

## 🚀 使用方法

### 方法1：使用配置文件（已配置好）

```bash
cd /data/workspace/my-webui
./venv/bin/python scripts/video_tasks.py
```

### 方法2：使用命令行参数

```bash
# 分析指定目录
./venv/bin/python scripts/video_tasks.py --video-dir /data/workspace/data

# 递归分析（包括子目录）
./venv/bin/python scripts/video_tasks.py --video-dir /data/workspace/data --recursive

# 跳过已分析的视频
./venv/bin/python scripts/video_tasks.py --video-dir /data/workspace/data --skip-existing

# 指定配置文件
./venv/bin/python scripts/video_tasks.py --config video-tasks.json
```

## 📊 支持的视频格式

系统会自动识别以下格式：
- `.mp4`, `.mov`, `.mkv`, `.avi`
- `.webm`, `.flv`, `.m4v`
- `.mpg`, `.mpeg`, `.wmv`, `.ts`

## 🔍 运行后的效果

1. **扫描目录**：自动查找所有支持的视频文件
2. **逐个分析**：按顺序分析每个视频
3. **生成报告**：在每个视频同目录生成 `.analysis.md` 文件
4. **增量运行**：再次运行时，跳过已有报告的视频

## 💡 实用技巧

### 定时自动分析（使用cron）

```bash
# 每天凌晨2点自动分析新视频
0 2 * * * cd /data/workspace/my-webui && ./venv/bin/python scripts/video_tasks.py
```

### 查看分析进度

```bash
# 实时查看日志
tail -f logs/backend.log

# 查看已生成的报告
ls -lh /data/workspace/data/*.analysis.md
```

---

现在你可以运行 `./venv/bin/python scripts/video_tasks.py` 来测试目录批量分析了！

需要我帮你：
1. 立即运行测试？
2. 查看某个具体视频的分析报告？
3. 调整配置参数？