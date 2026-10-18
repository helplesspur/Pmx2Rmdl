# PMX2RMDL / pmxconv

PMXConv 是一个 Windows 工具链：读取 MMD PMX 网格、权重、骨骼和部分物理数据，生成 Apex/R5F 的 S21 客户端与 S3 服务端 RMDL 模组，并可生成无头第一人称手臂/身体。它不使用“PMX 转 SMD”骨骼绑定插件；SMD 只作为 Source 编译器的交换格式。

本仓库发布源码、可复现文档和已经编译的 Windows 程序。没有包含任何角色 PMX/贴图、游戏 RPak/StarPak 或第三方模型素材。

## 目录

- `src/`：Python 源码和转换资源。
- `dist/pmxconv/pmxconv.exe`：图形界面程序，便携式目录运行。
- `dist/pmxconv/cli/pmxconv.exe`：命令行程序。
- `docs/`：转换和发布说明。
- `tools/`：从源码构建发布包的辅助脚本。

## 运行编译版

将 `dist/pmxconv/` 整个目录保留在一起，运行 `pmxconv.exe`，在界面中选择 PMX、R5F、PMX2RMDL 工具目录、Blender 4.2 和 SourceFilmmaker 的 `game` 目录。命令行示例：

```powershell
.\dist\pmxconv\cli\pmxconv.exe convert .\model.pmx --name demo `
  --tools C:\path\to\pmx2rmdl --blender C:\path\to\blender.exe `
  --sfm C:\path\to\SourceFilmmaker\game --game C:\path\to\R5F `
  --output .\converted\demo --first-person
```

`--outline-width`、`--fp-outline-width` 和 `--skin-emission` 默认为 0。`--animation-style loba` 是默认第三人称动作树。安装前请关闭游戏，并使用 `--install` 或界面中的安装按钮；安装器会创建备份并支持 `rollback`。

## 依赖

- Blender 4.2，安装 MMD Tools。
- SourceFilmmaker 的 `studiomdl.exe`。
- PMX2RMDL 工具目录中的 RePak、RSX、rmdlconv 和 mdlshit。
- R5F 游戏安装，用于读取参考骨架、动画和材质模板。

这些工具不随本仓库再分发。源码默认从命令行参数读取路径，也支持环境变量 `PMXCONV_TOOLS`、`PMXCONV_BLENDER`、`PMXCONV_SFM`、`PMXCONV_GAME`。

## 物理和兼容性边界

PMX 的 Bullet 刚体、关节、IK、表情和材质形变不能等价映射到 Apex。本项目将部分附属骨转换成受限的 Apex 摆动骨；缎带、袖子、衣摆等容易乱飞的链默认关闭物理，尾巴和衣服不做 Bullet 碰撞。报告中的归档 JSON 只记录输入数据，不表示游戏完整复现了原始物理。

## 从源码构建

源码构建需要 Python 3.11/3.12、PyInstaller、Pillow 和 NumPy。先安装依赖，再运行：

```powershell
python -m pip install pyinstaller pillow numpy
python -m PyInstaller --noconfirm --clean --onedir --name pmxconv `
  --paths src\resources --add-data "src\resources;resources" `
  --hidden-import integration --hidden-import rigs --hidden-import runtime_audit `
  --hidden-import texture_rules --hidden-import PIL.Image --hidden-import numpy `
  src\pmxconv.py
```

完整的项目构建约定见 `docs/build.md`。发布包使用 GPL v3；MMD Tools 的版权和许可证见 `src/resources/LICENSE-mmd-tools.txt`。第三方转换器及模型素材仍由各自作者负责。
