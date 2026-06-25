I'm thinking through which files in this open-webui fork can safely be moved to a subdirectory without breaking the build system and package managers. Since this is a fork with dependencies on npm, Python, and CI tooling, I need to be conservative about what gets relocated—files like package.json and pyproject.toml need to stay in the root since build tools expect them there.

Looking at what could be moved, I see documentation files like CHANGELOG.md, CODE_OF_CONDUCT.md, and various license-related files that aren't referenced by the build system. The docker helper scripts are a bit trickier since they might be referenced elsewhere, but the standalone contribution_stats.py script could definitely move. I should also check whether those PNG images are embedded in the README before moving them, since that would break the links. I should search for references to these candidate files across the codebase to determine which ones are truly safe to move—checking pyproject.toml for any build/packaging references, grepping for banner.png and demo.png in docs, and verifying that docker-compose files and video-tasks.json aren't imported elsewhere. Once I confirm what's actually referenced, I can move the genuinely non-essential files like CODE_OF_CONDUCT, CHANGELOG, and TROUBLESHOOTING into an organized folder while keeping all build tooling and the LICENSE itself in the root.
先别急着动手——根目录很多文件是构建/打包工具硬性依赖的，移动会导致构建失败。我先排查哪些文件被引用、哪些是纯文档/辅助文件可安全归档。
I'm noticing that some files that look like documentation are actually being pulled into the build and read at runtime — CHANGELOG.md is being force-included in the wheel distribution and then loaded by the environment module when the app starts.

LICENSE and README.md are also locked in place by the build configuration, and since README references banner.png and demo.png, those images need to stay at the root too. The hatch_build.py file is critical to the build process itself, so it absolutely can't move. Files like CODE_OF_CONDUCT.md and TROUBLESHOOTING.md are just GitHub conventions without direct code references, so they're safe to reorganize if needed.

Now checking the remaining files: CONTRIBUTOR_LICENSE_AGREEMENT and LICENSE_NOTICE don't appear to be referenced anywhere in the codebase, so they're functionally safe to move, though keeping them visible is good practice for legal clarity. The contribution_stats.py script is standalone with no dependencies, so it's movable. Docker files are trickier—docker-compose.yaml is expected at the root by default, and moving it would break the docker-compose launcher and Makefile references, so I should probably leave those alone unless the user isn't using Docker.

My approach: create a dedicated folder for optional/convenience files and move the safe items there, while being cautious about docker-related files since moving them could break relative paths and default behavior. docker/ folder would require updating references in the Makefile and launcher script, so I need to check those files first to see if the move is feasible without breaking the workflow. Let me scan through those to understand the cross-dependencies.
关键发现：不少"看起来像文档"的文件其实被**构建或运行时**引用，**绝不能移动**：

- `CHANGELOG.md` → 被 `pyproject.toml:208` 打包进 wheel，且 `env.py:173` 运行时读取（"What's New" 弹窗）
- `hatch_build.py` → `pyproject.toml:193` 构建钩子，hatchling 在根目录查找
- `LICENSE` / `README.md` → `pyproject.toml` 的 `license`/`readme` 字段
- `banner.png` / `demo.png` / `LICENSE_HISTORY` → 被 `README.md` 引用（GitHub 首页图）

再确认下 docker 那批文件的相互引用，判断能否成组归档：
Moving the Docker files to a separate folder creates a dependency problem: the Makefile expects `docker-compose.yaml` in the root directory for `docker compose up`, while the launcher script uses relative paths that would only work if both the script and YAML files move together. Updating either approach would require coordinating changes across multiple entry points.

I'll move the five clearly safe meta files (CODE_OF_CONDUCT.md, CONTRIBUTOR_LICENSE_AGREEMENT, LICENSE_NOTICE, TROUBLESHOOTING.md, and contribution_stats.py) into a `meta/` folder now, but for the Docker files—which represent the bulk of the clutter—I should explain the tradeoffs and offer to reorganize them rather than silently breaking existing workflows. I'll verify the Makefile references aren't a blocker, then execute the moves for those five safe files into `project-meta/`. Since the moved files have no inbound references, I won't need to update anything. For the Docker files, I'll hold off on moving them automatically since users expect `docker compose up` to work from the root — I'll present that as a separate option with a clear refactoring plan instead.
已查清。结论：

- **docker 系列文件不能随意移动**——`Makefile` 的 `docker compose up` 默认读根目录 `docker-compose.yaml`，`docker-compose-launcher.sh` 也按裸文件名引用这些 yaml。移动会破坏 docker 工作流（需同步改 Makefile + launcher）。
- **真正可安全归档**（无任何构建/运行时/README 引用）的只有这几个社区/法务/独立脚本文件。

我先把这批安全文件归到一个 `project-meta/` 文件夹：
已完成安全归档。当前根目录情况说明：

## 已移动到 `project-meta/`（5 个，确认无引用，零风险）
- `CODE_OF_CONDUCT.md`
- `CONTRIBUTOR_LICENSE_AGREEMENT`
- `LICENSE_NOTICE`
- `TROUBLESHOOTING.md`
- `contribution_stats.py`

## 必须留在根目录的（移动会报错/构建失败）

| 文件 | 原因 |
|---|---|
| `package.json` `package-lock.json` | npm，且 `pyproject` 从 package.json 读版本号 |
| `pyproject.toml` `uv.lock` `hatch_build.py` | Python 打包；`hatch_build.py` 是构建钩子 |
| `vite.config.ts` `svelte.config.js` `tailwind.config.js` `postcss.config.js` `tsconfig.json` `i18next-parser.config.ts` | 前端构建配置，工具只认根目录 |
| `CHANGELOG.md` | 被打包进 wheel + 运行时读取（"What's New"） |
| `README.md` `LICENSE` | `pyproject` 的 `readme`/`license` 字段 |
| `banner.png` `demo.png` `LICENSE_HISTORY` | 被 README 引用 |
| `Makefile` `Dockerfile` | 构建/容器入口 |
| `start-video-analysis.sh` `video-tasks.json` | 你的视频分析入口与任务配置 |

## docker 系列（10 个，是真正的"大头"，可选归档）

`docker-compose*.yaml`(6) + `docker-*.sh`(4)。**可以**整体移进 `docker/` 文件夹，但需要我同步改两处引用：
- `Makefile`：`docker compose` 加 `-f docker/docker-compose.yaml`
- `docker-compose-launcher.sh`：内部的 yaml 路径

代价是以后不能在根目录直接 `docker compose up`，得用 `make` 或指定 `-f`。你平时用 venv 脚本启动、基本不碰 docker——

要我把这 10 个 docker 文件也归到 `docker/` 并改好引用吗？如果要，我就一并处理。
The user confirmed moving the docker files into a `docker/` folder, so I need to carefully reorganize them. I'm starting by examining the docker-compose-launcher.sh script to see how it references the compose files, then checking all the docker-*.sh scripts for any cross-references and verifying how the Dockerfile and docker-compose files interact with each other.
开始执行。先完整读 docker 相关脚本，搞清内部路径引用，避免移动后断链。
The issue is that the launcher script references compose files by their bare names relative to the current working directory, but if both get moved into a `docker/` subdirectory, the paths will break since the user would still be running the script from the repo root. I need to make the launcher resolve file paths relative to its own location instead of the CWD—either by having it change to its own directory or by prefixing the compose file paths with the script's directory.

But there's a deeper problem: if the compose files use relative build contexts like `.`, those paths are resolved relative to the compose file's location, not the repo root. Moving the compose files to `docker/` would break the build context references. I need to check what the compose files actually reference for build contexts, volume mounts, env files, and any other relative paths to see if moving them is even feasible.
launcher 按裸名引用 yaml。移动前必须确认 compose 文件里是否有 `build context`/相对路径（这决定移动后会不会断）。读一下主 compose 和其它几个：
The compose file specifies a build context relative to where the compose file sits, so moving it to a `docker/` subdirectory would break the paths — the context would become `docker/` instead of the repo root, and it would look for the Dockerfile in the wrong place. I need to adjust the paths to account for this.

I'm realizing the grep search didn't catch anything, which is strange since the build context is clearly in the main compose file. The filetype filter might not be working correctly. Rather than troubleshooting that, I should check all the other compose files directly — there are several of them (gpu, api, data, playwright, otel, amdgpu, a1111-test) that might also have build contexts or relative paths I need to account for when reorganizing the docker setup.

This refactoring is more complex than initially apparent. I need to systematically go through all 13 docker-related files (8 compose yamls and 5 shell scripts), identify every build context and relative path reference, then update them all consistently when moving everything into the docker/ directory. The Makefile will need updates too, and the launcher script likely has hardcoded paths that depend on the current structure.
主 compose 有 `build: context: .`，移动后需改为 `context: ..`。还有 7 个其它 compose 文件和 4 个 docker 脚本要一并检查。重新精确搜索所有 compose 的构建上下文/相对路径，并读取 docker 脚本：
I need to refine my grep search to target only Docker Compose files and look specifically for `context:` and `dockerfile:` fields rather than matching too broadly across the codebase. Since all the compose files are in the root, I'll search with a more targeted pattern to isolate just the build configuration sections.
grep 命中太杂。直接读其余 7 个 compose 文件（都很小）确认是否含 `build/context`：
I'm reviewing the compose files and identifying which build context paths need updating when they're moved into the docker directory—the main ones reference the current directory and will need to point up one level instead, and there's also an environment variable path in the data compose file that needs adjustment.

The key insight is that setting `--project-directory` to the repo root when invoking docker compose would make all relative paths resolve correctly regardless of where the yaml files live, which is the cleanest way to avoid breakage across build contexts and volume mounts.

But I'm realizing this approach has real downsides — `context: .` resolution is murky and version-dependent, and moving compose files introduces genuine coupling risks for what amounts to a cosmetic cleanup. The safer path is to just update the relative paths in the compose files themselves (`context: ..` in a couple places, `../test/...` for the test volume) and adjust the launcher to handle the new location, rather than trying to be clever with project-directory flags. It's more explicit and less fragile, even if it requires a few edits. The alternative of moving only helper scripts doesn't really solve the problem since the launcher still needs to find the compose files.

Let me look at what those docker helper scripts actually reference to see if there's any real coupling I'm missing.
其余 compose 基本只用环境变量。需要处理的相对路径：
- `docker-compose.yaml` 和 `docker-compose.otel.yaml`：`build: context: .` → 移动后改 `context: ..`
- `docker-compose.a1111-test.yaml`：`./test/...` → `../test/...`

再读 4 个 docker 辅助脚本，确认内部引用：
Looking at the Docker helper scripts, the build script uses the current working directory as its context, so it works correctly whether run from the root or from a subdirectory—the `.` always refers to wherever the command is executed from. The Ollama script has no path references, so it's safe either way.

The cleanup script runs `docker compose down -v` against whatever compose file exists in the current directory, which would fail if the compose files move to a `docker/` subdirectory and the script is run from the root. The update-models script is also safe with no path dependencies.

The real coupling issues are that the launcher script, Makefile, and compose files all assume the compose YAML files are in the current working directory, and the build context and test paths are relative to the repo root. The plan is to move all 13 Docker files into a `docker/` subdirectory and use `--project-directory` to keep relative paths resolving correctly—this tells Docker Compose where the project root is so that volume mounts and build contexts still reference the right locations.

However, there's a subtlety with build context: the Compose spec says `context: .` is resolved relative to the compose file's parent folder, not the project directory. So if the compose file moves to `docker/`, a bare `context: .` would point to `docker/` instead of the repo root. The safest approach is to update the relative paths in the compose files themselves—change `context: .` to `context: ..` so it points to the parent directory (repo root), and similarly update the a1111 test path from `./test/...` to `../test/...` and the ollama data path to `../ollama-data` for consistency. Then the launcher script needs to be adjusted to reference the compose files in their new location.

I'm setting up the launcher to compute its own directory using bash parameter expansion, then use absolute paths for the `-f` flag when invoking docker compose. This way the build context `..` resolves correctly relative to the compose file's location in `docker/`, and volumes like `../test/...` point to the right places regardless of where the script is run from. I'll apply the same pattern to the cleanup script and Makefile, adding the explicit compose file path to all docker compose invocations.

For docker-run.sh, I need to make sure it builds from the repo root by computing SCRIPT_DIR and passing `$SCRIPT_DIR/..` as the build context, so it works whether the user runs it from root or from inside the docker directory.

Now I'm ready to execute: move all 13 docker files into the docker/ directory using git mv, then update the compose files to fix the context and volume paths, update the launcher and cleanup script with SCRIPT_DIR logic, update docker-run.sh to build from the correct context, and add the `-f docker/docker-compose.yaml` flag to the Makefile.
辅助脚本基本基于 CWD 运行。方案明确，开始执行。先移动 13 个 docker 文件到 `docker/`：
全部移动完成。现在修正相对路径引用。先改两个含 `build context` 的 compose：
改 a1111 测试 overlay 和 data 的相对宿主路径（让其仍指向仓库根）：
改 launcher：用脚本自身目录的绝对路径引用 compose（与 CWD 无关）：
I should also fix the `--drop` branch to include the base compose file when running `docker compose down --remove-orphans`, so it targets the correct project.
launcher 的 `--drop` 分支也用裸 `docker compose down`，补上 `-f`：
改 `docker-cleanup.sh` 和 `docker-run.sh`，让它们用脚本自身目录定位 compose / 构建上下文：
最后改 `Makefile`，让 `docker compose` 指向新路径：
校验所有改动的脚本语法，并用 `docker compose config` 验证移动后的 compose 能正确解析（含 build context）：
The shell scripts all pass syntax validation and the Docker Compose configuration parses correctly with the build context resolving to the repo root, so I have good confidence the setup is solid.
全部通过，且 `docker compose config` 成功解析（含 build context）。再确认 build context 实际指向仓库根：
完成验证。`docker compose config` 解析出的 **build context 正确指向仓库根** `/Users/even/code/code_deal/open-webui`，`Dockerfile` 也对（仍在根目录）。根目录除了必须保留的 `Dockerfile` 外，docker 相关文件已全部归档。

## 本次整理总结

**根目录现在清爽很多**，移动了 18 个文件到两个文件夹：

### `project-meta/`（5 个，零风险，无引用）
`CODE_OF_CONDUCT.md` `CONTRIBUTOR_LICENSE_AGREEMENT` `LICENSE_NOTICE` `TROUBLESHOOTING.md` `contribution_stats.py`

### `docker/`（13 个，已同步修复所有路径引用）
8 个 `docker-compose*.yaml` + 5 个 `docker-*.sh`，并修正了：
- `docker-compose.yaml` / `docker-compose.otel.yaml`：`context: .` → `context: ..`
- `docker-compose.a1111-test.yaml`：`./test/...` → `../test/...`
- `docker-compose.data.yaml`：默认数据目录 → `../ollama-data`
- `docker-compose-launcher.sh` / `docker-cleanup.sh`：用脚本自身目录绝对路径引用 compose
- `docker-run.sh`：构建上下文指向仓库根
- `Makefile`：`docker compose` 追加 `-f docker/docker-compose.yaml`

### 用法变化（仅 docker 相关）
- `make install` / `make start` 等：**不变**，照常在根目录运行
- 手动起容器：`docker compose -f docker/docker-compose.yaml up -d`
- 交互式启动器：`bash docker/docker-compose-launcher.sh`（任意目录可运行）

校验结果：5 个 shell 脚本 `bash -n` 全通过，`docker compose config` 解析成功且路径正确。你的视频分析入口（`start-video-analysis.sh`、`video-tasks.json`）和所有前后端构建配置均保持在根目录、未受影响。

刚才那条命令被取消了，需要我接着做别的吗？