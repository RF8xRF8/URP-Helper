# URP 抢课助手

面向 URP 类教务系统的 Web 版选课工具。

## 使用前必看

当前版本不是“零配置即用”。
启动前必须先在 [app.py](app.py) 中填写目标系统地址，否则程序会访问占位域名并失败。

需要修改的配置项如下：

- URP_CONFIG.base_url：教务系统主地址
- URP_CONFIG.webvpn_auth：WebVPN 认证地址（含 /authserver）
- URP_CONFIG.webvpn_base：WebVPN 下教务地址
- URP_CONFIG.cas_service：CAS 回调服务地址

示例位置： [app.py](app.py)

## 快速启动

### 方式 1：双击启动（推荐）

1. 确认已按上节填写 [app.py](app.py) 的 URP_CONFIG。
2. 双击 [start.bat](start.bat)。
3. 首次运行会自动安装依赖并启动服务。
4. 打开 http://127.0.0.1:5000。

### 方式 2：命令行启动

1. Python 版本要求：3.14+。
2. 在项目目录执行：

```powershell
uv sync
uv run .\app.py
```

如果没有 uv：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\app.py
```

## 页面填写说明

1. 输入学号和密码。
2. 选择登录方式：
- 校园网内：校园网直连
- 校园网外：WebVPN
3. 添加课程（每门至少填一个条件）：
- kch：课程号（推荐）
- kxh：课序号（推荐）
- kcm：课程名
- skjs：教师名
4. 点击“开始抢课”。

建议：优先填写 kch + kxh，可减少歧义与手动选择。

## 运行机制

- 系统未开放时会持续轮询。
- 搜索出现多条匹配时会弹窗让你选择。
- 失败课程会自动按策略重试。
- 点击“停止”会中断任务并关闭当前会话。

## 常见问题

### 1. 启动后立刻失败或无法访问

优先检查 [app.py](app.py) 中 URP_CONFIG 是否填写为真实地址。

### 2. uv run 返回 Exit Code 1

如果是手动 Ctrl + C 停止，这是正常退出。

### 3. 登录失败

- 先核对学号和密码。
- 校外网络请切换为 WebVPN。
- WebVPN 失败时重点检查 URP_CONFIG.webvpn_auth 与 cas_service。

### 4. 课程无结果

- 核对 kch/kxh 是否正确。
- 确认是否处于可选课时段。

### 5. 终端中文乱码

[start.bat](start.bat) 已自动设置 UTF-8。
手动终端可先执行：

```powershell
chcp 65001
```

## 安全建议

- 不要在代码、截图、日志中暴露真实凭据。
- 不要将含账号信息的配置上传公开仓库。

## 项目结构

- [app.py](app.py)：后端主程序与路由
- [templates/index.html](templates/index.html)：前端页面
- [start.bat](start.bat)：一键启动（uv 优先）
- [start_venv.bat](start_venv.bat)：纯 Python/.venv 启动
- [requirements.txt](requirements.txt)：pip 依赖

## 免责声明

本项目仅用于学习与技术研究，请遵守所在学校与相关法律法规。


