# 日报批量创建工具

## 运行

双击 `run_daily_report_gui.bat`，或在当前目录执行：

```powershell
python .\daily_report_gui.py
```

## 使用流程

1. 打开工具后会自动加载需求和负责人。
2. 第一步选择需求，确认详情无误后点击 `下一步`。
3. 第二步选择负责人、状态、日期、工时和是否设置负责人/时间/状态/工时。
4. 第三步填写日报内容，每行一条，右侧会自动生成预览。
5. 点击 `确认执行`，二次确认后才会真实请求接口写入平台。

## 配置和历史

- 顶部 `全局配置` 可以修改 `Base URL`、`Project ID`、`Tenant ID`、`Authorization`，也可以获取当前登录人信息。
- 直接用 Python 脚本运行时，配置保存到脚本旁边的 `daily_report_data/config.json`；打包成 exe 后，配置保存到当前 Windows 用户的 `%APPDATA%\DailyReportTool\daily_report_data\config.json`，关闭再打开不会丢失。
- 代码里不再写死默认 token。`Base URL`、`Project ID`、`Tenant ID` 保留默认值；首次使用需要填写并保存 `Authorization`。之后打开软件会自动读取本地配置，不需要重复填写。
- `Authorization` 支持填写完整的 `Bearer xxx`，也支持只填写 token 内容，程序会自动补 `Bearer`。
- 当前登录人信息包含用户 ID、昵称、部门、岗位；负责人列表加载后会优先默认选中当前登录人。
- 顶部 `全部日报` 会按当前登录人 ID 查询平台上的日报/任务数据，支持关键词和分页。
- 每条通过脚本创建的日报会保存到 `daily_report_data/history.jsonl`。
- 点击右上角 `历史记录` 可以按日期或关键词查询历史日报，里面会保留任务 ID、父需求、日报内容、负责人、状态和创建结果。

## GitHub 检查更新

顶部 `检查更新` 会读取 `Fiz2Z/daily-report-tool` 的 GitHub 最新 Release，并从 Release 附件里下载 `report-tool.exe`。用户不需要配置 GitHub 仓库。

自动发新版：

1. 把项目推到 GitHub 仓库。
2. 确认仓库里包含 `.github/workflows/release.yml`。
3. 创建并推送版本 tag，例如：

```powershell
git tag v1.0.1
git push origin v1.0.1
```

GitHub Actions 会自动在 Windows 环境打包应用，创建 Release，并上传 `report-tool.exe` 和 sha256 文件。打包时会自动把程序内的 `APP_VERSION` 设置成 tag 版本号。

也可以在 GitHub 页面进入 `Actions`，手动运行 `Build Windows Release`，填写版本号后自动打包发布。

用户点击 `检查更新` 后，程序会比较本地 `APP_VERSION` 和最新 Release Tag。

如果 Release 附件带有 GitHub `digest` 字段，或 Release 描述里写了 `sha256: 文件hash`，程序会自动校验下载文件。exe 运行模式下，下载完成后可自动关闭、替换当前 exe 并重启；脚本运行模式下只下载文件，不覆盖源码。

## 日报条数

日报配置页只保留手动条数，下拉可选：

- 5 条：时间到 20:30。
- 4 条：时间到 18:30。

日期默认是明天，也可以手动修改。

## 时间规则

5 条：

- 09:00:00 - 11:00:00
- 11:00:00 - 14:30:00
- 14:30:00 - 16:30:00
- 16:30:00 - 18:30:00
- 18:30:00 - 20:30:00

4 条使用前 4 个时间段。

## 注意

- 我没有帮你执行接口测试，避免产生真实数据。
- 日报内容需要至少达到要创建的条数。比如当天自动生成 5 条，就至少填写 5 行。
- 如果某条任务创建成功但后续设置失败，日志会显示已创建的任务 ID，方便你在平台里处理。
- 默认失败后会停止继续创建；如果想跳过失败继续后面的任务，可勾选 `失败后继续`。
