# WorkBuddy 签到管理

本机 Windows 原生桌面管理面板。使用 Python 3.12 与 PySide6 / Qt，不依赖浏览器、不启动网页服务器。通过 `python -I app.py` 或打包后的 `WorkBuddyManager.exe` 打开；关闭窗口后，Windows 自动任务继续运行。界面为新粗野主义（Neubrutalism）风格：粗黑描边、无模糊的硬投影、直角、平涂色块，按钮按下时沉入投影。浅色用荧光柠檬 / 青 / 黄 / 橙 / 洋红，深色换成同色相的柔和低饱和色，底色、卡片、输入框分四档灰度。默认浅色，左下角可切换深色。

## 快速开始

环境：Windows 10/11，Python 3.12（按用户安装，默认位于 `%LOCALAPPDATA%\Programs\Python\Python312`），并已登录 WorkBuddy 桌面客户端。

```powershell
git clone https://github.com/wawavd13/workbuddy-checkin-manager.git
cd workbuddy-checkin-manager
python -I -m pip install -r requirements.txt
python -I app.py
```

首次运行会在项目目录下自动创建 `data/`（配置、状态与记录）与 `backups/`（计划任务备份），两者均已被 `.gitignore` 排除。`config.example.json` 是默认配置的参考，复制为 `data/config.json` 后按需修改；不复制则使用内置默认值。

不想安装 Python，可到 Releases 下载 `WorkBuddyManager.exe`，放到任意目录双击运行，数据生成在 exe 旁边。

## 页面

- **总览与签到日历**：今日签到、当日签到到账、连签天数、签到活动累计、历史日历、两个任务的最近与下次运行。
- **时间与签到策略**：多时间点、失败重试间隔与当日上限、当天确认成功后停止签到、漏跑补执行、电池模式。
- **成长中心与运行**：任务奖励、旅行、补登、兑换、抽奖及盲盒分项开关，立即运行和停止。
- **历史记录与日志**：日期/状态/类型筛选，易读摘要和原始记录，导出当前筛选结果。
- **积分消耗**：只读统计本机 WorkBuddy 会话记录（`~/.workbuddy/projects/*/*.jsonl`）里每次模型调用的计费，按模型、按日、按会话汇总。这是模型调用消耗的口径，与签到奖励、账号可用余额不同；服务端没有提供个人账号的逐笔消耗接口，所以只能来自本机记录。进入该页时后台解析一次，点「重新读取」刷新。

## 数字含义

**今日签到奖励** 是日志或服务端确认的当日签到奖励，不按执行次数相加。**签到活动累计** 是签到活动累计发放的积分，不是账号当前可用余额。

**账号可用积分** 使用个人账号 `POST /billing/meter/get-user-resource-summary`，合计 `Packages[].CycleRemainCapacity`，总额度和已用额度另列。点击“刷新积分”只读取，不签到、不消费；打开窗口仅读取本机缓存，不自动联网。签到确认（本次签到成功或已签）及手动成长检查结束后会自动更新余额和有效期缓存，窗口关闭时运行的定时任务也会更新；结果为“策略跳过”的巡检即使执行了成长时段也不查询积分。切回总览立即重读缓存。查询失败时保留上次成功的余额与有效期，并注明“上次成功于 …，最近一次查询失败（…）”；切换账号后不显示其他账号的缓存，从未成功过时显示未知。

**有效期** 从免费/付费资源包明细读取，按批次显示。每个资源包同时返回整包到期 `DeductionEndTime` 与本期结束 `CycleEndTime`：本期结束早于整包到期的是周期额度（每日免费包、体验版月度额度等），按本期结束显示并标记为周期；两者相同或缺一的是一次性包，按到期显示。判断只依据这两个时间，不依赖任何账号专属的包代码，也不能用订阅到期代替积分到期。明细分页读取、按 `ResourceId` 去重并与汇总余额核对；未对齐时明确提示信息可能不完整。有效期接口失败不影响已成功取得的余额。企业账号不套用个人接口。

**积分有效期** 为独立卡片，默认展开为时间线：每行一个日期，依次是日期、剩余天数、横条、积分数；同一天的多个批次合并并标注批数。横条长度只表示剩余积分数量，不表示时间。周期额度（如体验版每月额度）按本期结束日期排入同一时间线，用青色横条和“周期”标记区分，它在本期结束后刷新，资源包有效期另列且不等于本期积分有效期。点击日期行（或 Tab 选中后按空格 / 回车，上下方向键切换行）在该行下方展开每批准确时间，再次点击收起；切换主题及刷新缓存保留展开的日期。收起卡片时只显示最近到期与本期结束两行摘要。未知有效期单列，未知金额不推断为 0。旧版回退界面保留文字明细。

未返回的字段显示 `—`，没有记录的历史日期标为未知，不能直接判定为漏签。

显示的“记录时间”“状态查询时间”“任务运行时间”含义不同。首次查询发现已签，只能确认已经签到，无法据此推断确切成功时刻。

## 策略与资源

默认签到 00:05；成长检查 01:00、05:00、09:00、13:00、17:00、21:00。失败重试按设置的间隔触发（允许提前 2 分钟，避免整点触发比上次尝试早一秒而多等一整个间隔），并受当日次数上限限制；登录失效需要重新登录 WorkBuddy 后手动查询/签到恢复。成功缓存仅对当天和同一账号有效。

默认启用任务奖励及旅行，**补登、兑换、抽奖和开盲盒关闭**。旅行可能使用旅行能量；补登会用补登卡，抽奖会用次数，开 Buddy/盲盒可能用能量。保存开启前会提示，测试不会为了验证消费这些资源。

停止仅请求本管理器的当前运行停止，不关闭 WorkBuddy；已经完成的服务端操作无法撤销。关闭管理器并不暂停任务，要暂停请使用总览按钮。

## 自动任务与恢复

本管理器仅管理 `WorkBuddyAutoSignin` 和 `WorkBuddyGrowthPoll`，不会修改其他任务。首次应用前备份原任务，后续变更使用事务备份。时间策略页的“恢复原脚本任务”可恢复管理器接管前的动作及时间；恢复后原脚本可能执行整套成长中心，不再受分项开关限制。

电脑需要开机、联网、未休眠且 Windows 用户已登录。不会自动唤醒休眠电脑。错过时间的补运行不等于关机期间执行。任务使用真实 Python 完整路径、隔离模式 `-I` 和固定工作目录。

## 文件

- 上游脚本（可选）：`%USERPROFILE%\Tools\workbuddy-auto-signin\`，仅用于识别并接管旧版计划任务，不存在也不影响本程序运行。
- 管理器配置：`data/config.json`。
- 本机记录及状态：管理器目录内的 JSON / JSONL 文件。
- 原任务备份：`backups/original/`。

配置和日志不保存明文账号令牌、不上传云端。原实现取自 `88lin/workbuddy-auto-signin` 固定提交 `da2bc7f395d6a6d10334a7735f2f37d35766e003`，保留 MIT 许可；接口不是官方稳定公开 API，客户端升级或接口变化可能导致功能失效。

## 手动启动

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -I "$env:USERPROFILE\Tools\workbuddy-checkin-manager\app.py"
```

在界面“刷新签到状态”只查询、不领取、不执行成长任务。打开窗口仅读取本机记录及 Windows 任务状态，不自动联网查询账号。

## 界面依赖与回退

新版 Qt 依赖已安装在当前 Python 3.12。迁移到其他电脑时可运行 `python -I -m pip install PySide6`。使用 `qt_app.py` 与 `qt_theme.py`，后台无需 Qt。

旧界面已保留为 `legacy_tk_app.py`，临时回退可运行：

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -I "$env:USERPROFILE\Tools\workbuddy-checkin-manager\app.py" --legacy
```

## 独立运行（打包 exe）

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m PyInstaller --noconfirm --onefile --windowed --name WorkBuddyManager --add-data "vendor/auth_backend.py;vendor" --add-data "scheduler.ps1;." --hidden-import runner --hidden-import legacy_tk_app --hidden-import plistlib --hidden-import ntpath --hidden-import uuid --hidden-import base64 app.py
```

也可直接执行 `python -I -m PyInstaller --noconfirm WorkBuddyManager.spec`（与上面命令等价）。

产物为 `dist\WorkBuddyManager.exe`（约 51 MB），单文件、免安装 Python，双击即用；数据（`data\`）生成在 exe 旁边，删除 exe 即完全卸载。exe 兼任后台 worker：`WorkBuddyManager.exe --mode daily|poll`；在 exe 模式下“保存并应用”注册的计划任务直接指向该 exe。打开窗口只读本机记录，不自动联网；设置 `WB_DEBUG=1` 运行 worker 时会把异常类型追加到 `data\debug-traceback.log`（不含令牌与消息内容）。

## macOS

同一套代码在 macOS 11+ 以 launchd 运行同样的两个自动化任务，界面与策略页语义与 Windows 一致。

- **数据目录**：`~/Library/Application Support/WorkBuddyManager/`（`.app` 包保持只读，`data/`、`backups/` 都在这里）。
- **自动化**：「保存并应用」写入 `~/Library/LaunchAgents/com.wawavd13.workbuddy-manager.signin.plist` 与 `.poll.plist` 并 `launchctl bootstrap`；「暂停自动任务」对应 `bootout` + `disable`；「恢复原任务」移除这两个 plist，若发现上游安装的 `workbuddy-auto-signin.plist` 会重新加载它。所有写操作前先事务备份到 `backups/`。
- **显示差异**：launchd 不提供上次运行时间，面板该列显示 `—`；下次运行由触发时间表推算；任务是否被 launchd 加载、上次退出码来自 `launchctl print`。
- **从源码运行**：`python3 -I -m pip install PySide6` 后 `python3 -I app.py`。legacy tkinter 回退界面不随 macOS 包发布。
- **构建**：推送 `v*` 标签时 GitHub Actions 自动在 macOS runner 上跑全部测试并构建 `WorkBuddyManager.app`（未签名，首次打开需右键 → 打开，或在系统设置里允许）；本地构建执行 `python -m PyInstaller --noconfirm WorkBuddyManager-macos.spec`。


## 隐私与数据

- 所有数据只保存在本机项目目录（或 exe 旁）的 `data/` 下，不上传、不收集。
- 配置、状态与记录落盘前会剔除令牌、uid 等身份字段（见 `storage.nonsecret`）；日志中的令牌会被替换为 `[redacted]`。
- 仓库不包含任何账号数据、计划任务备份、截图或构建产物；提交前请确认 `git status` 中没有 `data/`、`backups/`、`dist/`。

## 许可与致谢

本项目以 MIT 许可发布（见 `LICENSE`）。`vendor/` 下的 `upstream_signin.py` 与 `auth_backend.py` 取自 [88lin/workbuddy-auto-signin](https://github.com/88lin/workbuddy-auto-signin)（MIT，版权归原作者，许可文本见 `vendor/LICENSE`），固定于提交 `da2bc7f395d6a6d10334a7735f2f37d35766e003`。
