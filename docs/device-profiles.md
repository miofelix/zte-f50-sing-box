# 设备适配

profile 描述设备运行环境。默认配置文件为 `profiles/zte-f50.json`，配套参数文件为 `profiles/zte-f50/profile.conf`。

## 配置字段

| 字段 | 作用 |
| --- | --- |
| `schema_version` | profile 格式版本：`1` |
| `name` / `display_name` | 稳定标识和显示名称 |
| `ssh_host` / `remote_root` | SSH 别名和部署根目录 |
| `platform` | 核心发布物平台：`android-arm64` |
| `autostart.backend` / `autostart.hook_path` | `external-script` 适配器与 root 启动脚本路径，可选 |
| `ports.tproxy` / `ports.local_test` | TPROXY 与本机测试端口 |
| `network.lan_interface` | 热点桥接口，例如 `br0` |
| `network.mark` | 策略 mark/mask |
| `network.route_table` / `rule_priority` | 路由表与策略优先级 |
| `network.chain_prefix` | 项目自有链的前缀 |
| `shell_config` | 与 JSON 字段核对的 shell 参数文件 |

路径、SSH host、接口、端口、mark 和 shell 字段必须通过校验。平台应与下载器支持的发布物一致。

## F50 运行要求

`zte-f50` profile 需要 root SSH、热点桥 `br0`、IPv4/IPv6 TPROXY、策略路由，以及 `iptables`、`ip6tables`、`ss`、`flock` 和 `setsid`。每种固件都应完成能力检查和客户端验证。

## 开机入口

F50 的启动适配配置为：

```json
"autostart": {
  "backend": "external-script",
  "hook_path": "/sdcard/ufi_tools_boot.sh"
}
```

固件需要通过 UFI/Samba 启动链以 root 执行此用户脚本。项目部署程序在脚本中注册后台任务，启动加载器由设备环境提供。入口缺失时，注册操作停止并报告错误。

其他启动环境可将 `hook_path` 指向 `/data` 或 `/sdcard` 下、开机以 root 执行的普通脚本。路径不接受符号链接；需要验证触发时机、后台任务权限和真实重启行为。启动入口必须在开机阶段持久可用，不能依赖开机后才出现的临时挂载层。

省略 `autostart` 的 profile 使用手动启动方式，`autostart on` 会报告缺少启动适配器。注册程序只维护项目标记块，保留脚本其他内容及元数据。实现约定见 [开发者指南](developer-guide.md)。
