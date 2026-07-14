# 占压路径线程提示词

你负责实现跨 PathPlan 的连续车体路径和道岔路径感知占压。先阅读 `docs/member-c-parallel/README.md`。

严格限制：不要修改 `app/core/engine.py`，不要实现终点折返选择器。

任务：

1. 审查 `PathTrackQuery`、`SectionOccupationService._segments_covered_by_train()`和 `TrainState.path_track`。
2. 设计并实现 `TrainTrackTrace`或等价组件，保存列车真实经过的有序 Seg 走廊。
3. 轨迹必须能跨越“上一程 PathPlan 末尾 + 当前平台 + 下一程 PathPlan 开头”，不能在切换 PathPlan 时丢掉仍被车尾占压的旧 Seg。
4. 道岔处只能沿列车实际获批路径回溯车尾，不得从全局拓扑任意选择分支。
5. 保留至少足以覆盖列车长度的历史；明确处理方向、偏移和重复 Seg。若终点换向需要主引擎提供活动车头转换，定义接口并记录，不要在本线程修改主引擎。
6. 为直线跨路径、道岔分支、车尾逐段出清和多车隔离增加独立测试。

建议优先新增独立模块，减少对 `section_occupation.py`的侵入。组件接口应允许主线程把它转换成 `PathTrackQuery`或直接作为兼容查询对象传给 `TrainState.path_track`。

验收重点：只给“当前新 PathPlan”构造 `path_track`是不合格的，因为车尾可能仍在旧 PathPlan。测试必须覆盖这一情况。

完成后运行聚焦测试并提交，不推送。报告提交哈希、接口、测试结果，以及主集成线程需要完成的接线工作。

