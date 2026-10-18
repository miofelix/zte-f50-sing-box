# 测试指南

## 快速检查

```sh
make check
make test-unit
# 单元测试命令：
# PYTHONPATH=src python3 -m unittest discover -s tests -v
```

`make check` 验证 Python/Shell 语法、profile 加载和 CLI 入口。`make test-unit` 验证主/备用入口注册、NUL 损坏拒绝、备份与同步失败恢复、旧状态识别、元数据完整性、开关持久化、网络等待、健康检查、重复触发及会话并发保护。

单元测试使用临时文件与桩，不连接设备。

## 事务故障注入

```sh
make test
# 单独运行事务故障注入：
make test-safety
# 或 python3 test_safety.py
```

`test_safety.py` 使用临时 `ip`/`iptables` 模型，包含三项测试：逐个注入 attach 失败并检查回滚、清理失败后的重试、watchdog 的确认与到期竞态。测试不修改本机网络，完整故障注入通常需要数分钟。

## 设备验证

使用 root SSH，以短租期启动代理，验证以下行为：

- 原生配置检查与 TCP/UDP 监听。
- 热点客户端国内直连、境外代理、DNS 和 IPv4/IPv6。
- UDP 转发及所需应用的网络连接。
- 核心退出和试运行到期后的规则清理。
- 真实重启后的开机触发、健康检查和自动确认。
- 开关切换、状态持久化及正在运行会话的连续性。

开机验证检查 `autostart status`、`run/boot.result`、`log/boot.log` 和客户端分流结果。手动执行 `boot-start.sh` 用于检查任务逻辑，真实重启用于验证设备启动链。验证记录应注明设备、固件、核心版本、profile 和结果。
