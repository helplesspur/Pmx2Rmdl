# 构建说明

在仓库根目录创建虚拟环境并安装 `pyinstaller`、`pillow`、`numpy`。源码入口是 `src/pmxconv.py`，资源目录是 `src/resources`；GUI 入口是 `src/pmxconv_gui.py`。

构建时把资源目录作为 PyInstaller data，并添加 `integration`、`rigs`、`runtime_audit`、`texture_rules`、`menu_models` 和 `rpak_index` hidden imports。构建产物需要保留 `_internal` 或对应的 PyInstaller 运行时目录，不能只复制 exe。

转换器需要运行时提供 Blender、SourceFilmmaker、RePak、RSX、rmdlconv、mdlshit 和 R5F 路径。发布仓库不写入任何本机绝对路径；通过 CLI 参数或 `PMXCONV_*` 环境变量配置。
