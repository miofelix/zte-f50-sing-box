# 开发者指南

## 目录和模块

```text
deploy.py                   命令行入口
src/zte_f50_singbox/         Python 包
  cli.py                    参数解析、操作编排和错误出口
  profiles.py               profile 校验与模板端口渲染
  release.py                官方 sing-box 发布物
  rules.py                  固定规则集与校验
  vless.py                  VLESS URL 解析
  config_ops.py             原生配置导入和安装
  installer.py              设备部署
  autostart.py              启动入口注册与开关管理
  transport.py              SSH 传输
f50-proxy                   设备端控制器
boot-start.sh               网络就绪和开机健康检查
profiles/                   设备 profile 与参数
config.example.json         sing-box 原生配置模板
test_safety.py              防火墙事务故障注入
tests/                      启动注册与开机流程单元测试
```

Python 包无第三方运行时依赖。在仓库根目录运行 `python3 deploy.py`，入口会将 `src/` 加入模块搜索路径。模块可通过 `PYTHONPATH=src` 导入。

## 设备适配

profile JSON 定义设备身份、SSH、部署目录、平台、入站端口和网络资源。`profile.conf` 使用经过字段与值校验的标量赋值；部署程序将其同步到设备，由控制器读取。

适配设备的步骤：

1. 定义 JSON 与 shell profile，确保端口、mark、路由表及链前缀一致。
2. 核对发布物平台、系统命令和内核能力。
3. 验证非法路径、端口及 shell 字段会被拒绝。
4. 运行故障注入，验证规则所有权和回滚顺序。
5. 完成真实客户端的 IPv4/IPv6、DNS、UDP 及开机验证。

字段与启动入口要求见 [设备适配](device-profiles.md)。

## 生命周期

启动事务按以下顺序执行：原生配置检查、核心就绪、创建 session 与 watchdog、准备私有链和路由、安装 filter，最后发布 PREROUTING hook。停止时先撤回拦截，再清理自有资源。

所有规则写操作都必须具有对应的清理路径。诊断快照用于审计；回滚通过精确删除完成，不将快照恢复到整套系统防火墙。

## 开机启动

`autostart.py` 向 profile 指定的 root 启动脚本注册标记块。F50 使用 UFI/Samba 执行的 `/sdcard/ufi_tools_boot.sh`，该启动链是设备环境要求。

注册操作检查入口类型、大小、shell 语法和标记完整性。项目只维护自己的标记块，保留其他脚本内容、inode、属主和权限，并在 `backups/` 保存 `0600` 备份。重复执行注册应得到相同内容；畸形、重复或不完整的标记必须拒绝。

自启动开关通过 `autostart.disabled` 表示：文件不存在时开启，存在时关闭。电脑端 `autostart on` 注册入口并移除该标记；设备端命令管理开关。部署操作不改变禁用标记。

`boot-start.sh` 脱离启动脚本后台运行，每个 boot ID 只尝试一次。它等待系统、LAN IPv4、上行路由和至少 30 秒 uptime，网络等待上限为 180 秒；通过 `_boot-start 120` 创建试运行会话，再经 mixed 入站执行 HTTP 204 检查。

会话创建、确认和清理受控制器锁保护。自动确认要求 session 匹配且开关开启；手动会话和其他 session 不得被确认或停止。失败只清理该任务创建的会话，watchdog 提供超时和核心退出回滚。

## 配置与节点

`config.example.json` 使用 sing-box 原生格式。profile 渲染器设置 `gateway` 和 `local-test` 端口；节点导入器只替换 tag 为 `proxy` 的 outbound。其他 DNS、route、inbound 和日志字段按原生语义处理。

修改 gateway tag、端口、DNS hijack 或规则集路径时，同步检查 profile 与设备控制器契约。配置安装先写入临时文件，执行原生检查，再移动到 `config.json`。

## 凭据与错误

用户可见错误应使用受控、脱敏的说明。节点 URL、UUID、Reality 参数和完整私有配置不得写入错误输出。私有配置、临时文件和备份使用 `0600` 权限。
