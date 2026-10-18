# 用户指南

## 环境准备

电脑需要 Python 3.10+ 和 OpenSSH，设备需要 root SSH、TPROXY 和策略路由能力。默认设备配置为 `zte-f50`：SSH 别名 `f50`，部署目录 `/data/local/f50-sing-box`。

```sh
ssh f50 'id'
find profiles -name '*.json' -print
```

确认 SSH 返回 root 身份。开机自启动要求固件通过 UFI/Samba 以 root 执行 `/sdcard/ufi_tools_boot.sh`；其他启动环境的配置方式见 [设备适配](device-profiles.md)。

## 下载和部署

在仓库根目录执行：

```sh
python3 deploy.py fetch --profile zte-f50
python3 deploy.py fetch-rules
python3 deploy.py deploy --profile zte-f50
```

核心和规则集通过 HTTPS 下载，并校验来源与摘要。部署程序使用设备端临时目录和传输校验，安装文件并注册开机入口；代理由启动命令或开机任务运行。执行 `deploy` 时，网关必须处于停止状态。

## 节点与配置

导入器以隐藏提示读取 VLESS 节点 URL，也接受标准输入：

```sh
python3 deploy.py import-vless
# macOS 也可从剪贴板读取：
# pbpaste | python3 deploy.py import-vless
python3 deploy.py install-config
```

私有配置保存在 `.local/config.json`，权限为 `0600`。导入器支持 VLESS TCP、TLS/Reality、Vision、uTLS、short ID 和 ALPN。其他 sing-box 协议可直接编辑原生 JSON，并用 `install-config --config <文件路径>` 安装。配置安装会执行设备上的原生 `sing-box check`，检查通过后替换 `config.json`。

## 试运行与确认

```sh
ssh f50 '/data/local/f50-sing-box/f50-proxy check'
ssh f50 '/data/local/f50-sing-box/f50-proxy start 180'
ssh f50 '/data/local/f50-sing-box/f50-proxy status'
```

连接 F50 热点，检查国内直连、境外代理、DNS、IPv4/IPv6 和所需应用，再确认运行：

```sh
ssh f50 '/data/local/f50-sing-box/f50-proxy confirm'
```

`start N` 支持 30–3600 秒试运行，默认 180 秒。确认会取消超时，watchdog 持续监测核心。核心退出或试运行到期时，工具撤回代理规则并停止该会话。

## 停止、重启和日志

```sh
ssh f50 '/data/local/f50-sing-box/f50-proxy stop'
ssh f50 '/data/local/f50-sing-box/f50-proxy restart 180'
ssh f50 '/data/local/f50-sing-box/f50-proxy logs 100'
```

`stop` 停止代理，`restart` 重新进入试运行。热点地址或蜂窝接口变化后，使用 `restart` 刷新地址快照，并验证后执行 `confirm`。

## 开机自启动

开机自启动默认开启。电脑端命令负责注册启动入口并管理开关：

```sh
python3 deploy.py autostart on --profile zte-f50
python3 deploy.py autostart status --profile zte-f50
python3 deploy.py autostart off --profile zte-f50
```

在设备的部署目录中也可管理开关：

```sh
cd /data/local/f50-sing-box
./f50-proxy autostart on
./f50-proxy autostart status
./f50-proxy autostart off
```

开关状态持久保存，控制开机任务是否执行。关闭自启动不会中断正在运行的代理；立即停止使用 `./f50-proxy stop`。

F50 同时注册 `/sdcard/ufi_tools_boot.sh` 和 `/sdcard/ufi_tools_schedule.sh`。若主入口损坏或错过 UFI 的开机窗口，后续调度会补上本次开机尚未尝试的启动。两个入口共用 boot ID 和锁，不会反复启动失败节点，也不会重新启动本次开机已经手动停止的已处理会话。

开机任务等待系统启动完成、热点 IPv4 和上行路由就绪，并至少等待到设备运行 30 秒，网络等待上限为 180 秒。随后启动 120 秒试运行，通过本机 mixed 入站完成 HTTP 204 联网检查后自动确认。每个内核 boot ID 只尝试一次；失败则清理任务创建的会话，恢复系统转发路径。

查看开机任务结果与日志：

```sh
ssh f50 'cat /data/local/f50-sing-box/run/boot.result'
ssh f50 'tail -n 100 /data/local/f50-sing-box/log/boot.log'
```

## 故障排查

| 现象 | 检查方法 |
| --- | --- |
| `Native config check failed` | 检查原生 JSON、节点参数和 `rule-set/` 文件 |
| `Reserved chain ... in use` | 查看 `status` 和 profile 的保留资源，确认链、路由表及优先级冲突 |
| `TPROXY unavailable` | 核对内核能力与设备 profile 要求 |
| SSH 连接中断 | 重新连接后用 `status` 查看状态；watchdog 在设备上独立运行 |
| 启动入口缺失或损坏 | 检查固件的 root 启动链，然后执行电脑端 `autostart on` 注册项目入口 |
| 开机联网检查失败 | 查看 `log/boot.log`、`run/boot.result`，检查蜂窝网络、节点及配置后手动短租期启动 |
| `boot hook health: corrupt-NUL-bytes` | 启动脚本已损坏；保留现场并从验证过的备份恢复，保留其他插件的新入口，再执行 `autostart on` |
| `last boot: not attempted in this boot` | 本次开机尚未触发启动任务；检查 UFI/Samba 调度链及两个入口的 health |
| `lease: stale` | 旧状态文件对应的核心或 watchdog 已不存在，检查后用 `restart` 清理并重新试运行 |

处理规则冲突时只操作项目拥有的资源，不清空系统防火墙表。启动尝试标记用于限制自动重试，故障排查使用手动 `start`。

修改启动脚本前保留完整备份；保存后执行 `sync`，用 `autostart status` 核对两个入口的健康状态，再正常重启。`sh -n` 会接受部分含空字节的文件，不能单独作为完整性检查。项目注册入口时会检查空字节、备份并同步落盘；UFI 或第三方插件自行修改脚本时，也应保留这些检查。
