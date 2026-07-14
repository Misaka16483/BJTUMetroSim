# 倍率按钮无响应修复计划

## 问题
用户反馈前端倍率按钮（1×/10×/60× 和 1秒/10秒/1分钟）点击后无反应。

## 根因分析

### 1. 按钮在 PAUSED 状态不可用
`SimulationLifecycleControls.tsx` 只允许 `RUNNING` 状态：
```typescript
const enabled = engineClockState === 'RUNNING' && !operation;
```
当仿真暂停（PAUSED）时，用户无法调整倍率后再恢复。

`ControlPanel.tsx` 同样：
```typescript
const canChangeSpeed = isBackend ? backendState === 'RUNNING' : isRunning;
```

### 2. setSpeed 在网络请求失败时静默忽略
```typescript
void simSetSpeedMultiplier(multiplier).catch(() => {
  // 静默忽略
});
```
如果网络请求失败，本地状态虽已更新，但用户感知不到变化。

### 3. 视觉反馈不直观
所有按钮样式变化非常微弱（灰色 #6e7681 → 蓝色 #58a6ff），用户难以感知点击成功。

## 修复方案

### 修复1: `SimulationLifecycleControls.tsx` - 放宽按钮可用条件
- 将 `enabled` 从 `engineClockState === 'RUNNING'` 改为 `engineClockState === 'RUNNING' || engineClockState === 'PAUSED'`
- 暂停状态下允许用户调整倍率

### 修复2: `ControlPanel.tsx` - 放宽按钮可用条件
- 将 `canChangeSpeed` 从 `backendState === 'RUNNING'` 改为 `backendState === 'RUNNING' || backendState === 'PAUSED'`
- 暂停状态下允许用户调整倍率

### 修复3: 增强视觉反馈
- 添加点击涟漪动画反馈
- 增加倍率按钮在 active 状态下的高亮强度
- 添加过渡动画让状态切换更明显

### 修复4: 添加点击通知
- 在 `setSpeed` 中，当网络请求失败时给出控制台警告

## 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `bj-metro-sim/src/components/SimulationLifecycleControls.tsx` | 修改 | 放宽条件 + 增强视觉反馈 |
| `bj-metro-sim/src/components/ControlPanel.tsx` | 修改 | 放宽条件 |
