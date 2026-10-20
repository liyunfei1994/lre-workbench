# Python 3.14 迁移

本维护版统一使用标准版 CPython 3.14、Windows x64。`.python-version`、
Windows 启动器、安装器与 GitHub Actions 使用同一版本策略。
3.11 / 3.12 环境会先保留为 `.venv.backup-*`，再在本机重建 `.venv`。
安装脚本不支持 free-threaded 构建；其他项目的解释器和系统 PATH 不受影响。

## 热力学依赖核对

项目使用两个不同用途的库，二者均支持 Python 3.14，并非要求只能使用 3.14：

- **Cantera 3.2.0**：平衡化学与燃气热力学。官方安装文档列出 Python 3.10–3.14；
  PyPI 提供 `cantera-3.2.0-cp314-cp314-win_amd64.whl`。
  来源：[Cantera 安装说明](https://cantera.org/3.2/install/pip.html)、
  [Cantera 3.2.0](https://pypi.org/project/cantera/3.2.0/)。
- **CoolProp 7.2.0**：真实流体物性。7.2.0 更新记录明确增加 Python 3.14 支持；
  PyPI 提供对应 Windows x64 安装包。
  来源：[CoolProp 更新记录](https://coolprop.org/coolprop/changelog.html)、
  [CoolProp 7.2.0](https://pypi.org/project/CoolProp/7.2.0/)。

原锁定的 Cantera 3.1.0、CoolProp 7.1.0 不能原样作为本项目的 3.14 安装方案，
因此需要同时升级依赖。采用 CoolProp 7.2.0，避免无必要地跨越到 8.x 的接口变动。

| 直接依赖 | 迁移前 | Python 3.14 环境 |
| --- | --- | --- |
| PySide6 | 6.8.3 | 6.10.1 |
| NumPy | 2.2.6 | 2.3.5 |
| Matplotlib | 3.10.3 | 3.10.8 |
| Cantera | 3.1.0 | 3.2.0 |
| CoolProp | 7.1.0 | 7.2.0 |
| scikit-fem | 11.0.0 | 11.0.0 |
| SciPy | 1.15.3 | 1.16.3 |

完整传递依赖固定在 `constraints.txt`，避免开发者各自安装后得到不同环境。
环境指纹记录实际 Python 补丁版本、依赖版本及 Cantera 机制摘要。
不同环境得到的计算结果应按其指纹区分；回归测试不能替代物理模型验证。

## 使用和回退

保存并关闭正在运行的工作台后，双击 `setup_windows.bat` 完成环境迁移。
PyCharm 继续选择当前项目的 `.venv\Scripts\python.exe`；迁移后该路径对应 3.14。
使用 `.venv\Scripts\python.exe --version` 可查看实际版本。

如需恢复旧版，在另一个目录取得旧标签 `v7.1.0-team.1` 的源码，
按该版本的安装说明重建环境。不要将旧虚拟环境与新依赖文件混用。
保留的 `.venv.backup-*` 仅用于恢复参考；虚拟环境改名后不保证其中所有绝对路径仍有效。
