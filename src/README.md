# 源码入口

业务代码统一位于 [`lre_workbench/`](lre_workbench/)。`src` 是 Python 源码搜索根，不是导入名称；使用 `lre_workbench.*`，不要使用 `src.*`。

| 目录 | 职责 |
| --- | --- |
| `components/` | 泵、涡轮、燃烧室、喷管、喷注器、预燃室、冷却的模型与几何 |
| `systems/` | 全流量补燃循环、压力网络、再生冷却耦合及整机几何编排 |
| `solvers/` | 非线性迭代、初始化、状态和收敛判据 |
| `domain/` | 输入、结果、任务与几何数据契约、共享参数元数据 |
| `properties/`、`materials/` | 流体热物性、化学比较、推进剂及固体材料 |
| `operations/` | 供给网络、预冷、点火、瞬态和关机研究模型 |
| `analysis/` | 工程评估、扫描与热结构分析 |
| `integrations/` | CFD、CFturbo 与燃气数据交接 |
| `application/`、`runtime/` | 方案预设、恢复、模型调度、任务取消和运行环境 |
| `desktop/` | Qt 桌面窗口、绘图与桌面专用资源 |
| `legacy/` | 历史模型兼容实现，不接入当前 Web FFSC 设计入口 |

Web 页面、HTTP 路由和任务队列位于项目根目录的 `webapp/`；测试、安装和开发工具分别位于 `tests/`、`web_tests/`、`scripts/`。

保持现有启动方式：项目根目录 `run_app.py` 或 `python -m webapp.run --personal-workspace`。完整说明见 [源码架构与维护规则](../docs/SOURCE_ARCHITECTURE.md)。
