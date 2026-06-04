# 菜鸟国际物流批量查询工具 - macOS 版

## 系统要求

- macOS 10.15 (Catalina) 或更新版本
- 已安装 [Google Chrome](https://www.google.com/chrome/) 浏览器

## 安装

1. 双击下载的 `CainiaoTracker.dmg` 文件
2. 将 `CainiaoTracker.app` 拖到 `Applications` 文件夹
3. 关闭 DMG 窗口

## 首次运行

由于应用没有 Apple 开发者签名，首次打开需要：

1. 打开 `应用程序` 文件夹，找到 `CainiaoTracker`
2. **右键点击** → 选择 **"打开"**
3. 弹出安全提示时，点击 **"打开"** 确认

> 后续运行可以直接双击打开，不需要再右键操作。

## 首次登录 WPS

首次运行时需要扫码登录 WPS 网页版：

1. 双击运行应用，终端窗口会打开
2. Chrome 浏览器会自动弹出 WPS 登录页面
3. 用手机 WPS 扫码登录
4. 登录成功后，Cookie 自动保存，后续不需要再次登录

## 使用说明

双击 `CainiaoTracker.app` 即可运行，程序会：

1. 自动打开 Chrome 访问 WPS 多维表格
2. 读取所有物流单号
3. 批量查询菜鸟国际物流状态
4. 将结果写回表格

### 运行模式

默认为**增量模式**（只查询状态为空或异常的记录）。

如需其他模式，请在终端中直接运行：

```bash
# 增量更新（默认，跳过已签收的）
/Applications/CainiaoTracker.app/Contents/Resources/cainiao_tracker

# 强制查询所有记录
/Applications/CainiaoTracker.app/Contents/Resources/cainiao_tracker --all

# 仅读取，不查询不写入（测试用）
/Applications/CainiaoTracker.app/Contents/Resources/cainiao_tracker --dry-run

# 重置进度，开始新一轮
/Applications/CainiaoTracker.app/Contents/Resources/cainiao_tracker --reset
```

## 数据存储位置

所有运行数据存储在：

```
~/Library/Application Support/CainiaoTracker/
├── browser_data/          # WPS 登录 Cookie（勿删除）
├── cainiao_browser_data/  # 菜鸟查询浏览器数据
├── query_progress.json    # 查询进度记录
└── cainiao_tracker.log    # 运行日志
```

## 常见问题

### Q: 提示"Chrome 未安装"

请到 https://www.google.com/chrome/ 下载并安装 Chrome 浏览器。

### Q: 提示"无法验证开发者"无法打开

右键点击应用 → 选择"打开" → 确认打开。或在 `系统偏好设置` → `安全性与隐私` → `通用` 中点击"仍要打开"。

### Q: WPS 登录过期了

删除浏览器数据重新登录：

```bash
rm -rf ~/Library/Application\ Support/CainiaoTracker/browser_data
```

然后重新运行应用，扫码登录即可。

### Q: 想重新开始查询

```bash
/Applications/CainiaoTracker.app/Contents/Resources/cainiao_tracker --reset
```

## 卸载

1. 将 `CainiaoTracker.app` 从应用程序文件夹删除
2. 删除数据目录（可选）：
   ```bash
   rm -rf ~/Library/Application\ Support/CainiaoTracker
   ```
