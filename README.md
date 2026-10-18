# 中兴 F50 sing-box 网关

为中兴 F50 随身 Wi-Fi 提供基于官方 sing-box 的透明代理。通过 SSH 完成部署、节点配置和运行管理，支持 IPv4/IPv6、TCP/UDP、规则分流和开机自启动。

默认分流策略为国内域名、中国 IP 直连，其余公网流量走代理。开机自启动默认开启，可通过命令切换。代理功能使用 sing-box 原生 JSON 配置，设备参数由 profile 定义。

## 快速开始

准备 Python 3.10+、OpenSSH 和设备的 root SSH 访问权限。默认 profile 使用 SSH 别名 `f50`，部署目录为 `/data/local/f50-sing-box`。F50 的开机自启动需要 UFI/Samba 提供的 root 启动入口，配置要求见 [设备适配](docs/device-profiles.md)。

在仓库根目录执行：

```sh
python3 deploy.py fetch
python3 deploy.py fetch-rules
python3 deploy.py deploy

# 隐藏输入 VLESS 节点 URL
python3 deploy.py import-vless
python3 deploy.py install-config

# 试运行并检查热点客户端的网络连接
ssh f50 '/data/local/f50-sing-box/f50-proxy check'
ssh f50 '/data/local/f50-sing-box/f50-proxy start 180'
ssh f50 '/data/local/f50-sing-box/f50-proxy status'

# 验证分流、DNS 和 IPv4/IPv6 后确认运行
ssh f50 '/data/local/f50-sing-box/f50-proxy confirm'
```

试运行默认持续 180 秒，超时未确认会自动停止并撤回代理规则。停止服务使用 `ssh f50 '/data/local/f50-sing-box/f50-proxy stop'`。完整步骤见 [用户指南](docs/user-guide.md)。

## 开机自启动

`deploy` 注册启动入口，开机自启动默认开启。使用以下命令管理：

```sh
python3 deploy.py autostart on
python3 deploy.py autostart status
python3 deploy.py autostart off
```

开关状态持久保存。设备内可执行 `./f50-proxy autostart on|off|status`；停止正在运行的代理使用 `./f50-proxy stop`。

开机任务等待系统、热点和上行网络就绪，启动试运行并执行联网检查，通过后自动确认；失败则清理该任务创建的会话。F50 使用 UFI/Samba 以 root 执行的 `/sdcard/ufi_tools_boot.sh` 触发任务，并通过 `/sdcard/ufi_tools_schedule.sh` 补上本次开机尚未触发的启动。部署程序注册两个入口，具体适配方式见 [设备适配](docs/device-profiles.md)。

## 设备与配置

提供的 `zte-f50` profile 面向 Android arm64 的中兴 F50，使用热点桥 `br0`、IPv4/IPv6 TPROXY 和策略路由。profile 定义远端路径、端口、网卡、mark、路由表及启动入口。

- [用户指南](docs/user-guide.md)：部署、节点导入、运行管理和故障排查。
- [配置参考](docs/config-reference.md)：入站、DNS、规则分流和原生配置。
- [设备适配](docs/device-profiles.md)：profile 字段、能力要求和开机入口。

## 开发

Python 模块位于 `src/zte_f50_singbox/`，根目录 `deploy.py` 是命令行入口。

| 模块 | 职责 |
| --- | --- |
| `release.py` / `rules.py` | 官方核心、规则集下载及摘要校验 |
| `profiles.py` | 设备参数校验与模板渲染 |
| `vless.py` / `config_ops.py` | 节点解析与原生配置安装 |
| `installer.py` / `transport.py` | SSH 传输与部署 |
| `autostart.py` / `boot-start.sh` | 启动入口注册、开关与开机检查 |
| `cli.py` | 命令行参数与操作编排 |

```sh
make check       # 语法、profile 和 CLI 检查
make test-unit   # 启动入口与开机流程的离线测试
make test        # 单元测试及事务故障注入
```

模块边界、设备生命周期和测试方法见 [开发者指南](docs/developer-guide.md)、[安全模型](docs/safety.md) 和 [测试指南](docs/testing.md)。

## 许可证

MIT License，版权归 **Felix**。详见 [LICENSE](LICENSE)。
