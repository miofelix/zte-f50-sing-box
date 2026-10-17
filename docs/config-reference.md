# 配置参考

## 入站

默认 `gateway` 是 `[::]:7893` 的 TPROXY TCP/UDP 入站，`local-test` 是 `127.0.0.1:7890` 的 mixed HTTP/SOCKS 入站。profile 渲染器根据设备参数设置这两个 tag 的端口。

## DNS

热点客户端 DNS 通过 `hijack-dns` 交给 sing-box。`geosite-cn` 和 `.cn`/`.中国` 域名使用 `bootstrap-dns` 直连 AliDNS；其余查询通过 `remote-dns` 经 `proxy` 访问 Cloudflare DoH。

节点服务器使用 IP 或 bootstrap resolver 解析，避免 DNS 与代理连接之间的循环依赖。

## 路由

路由按顺序匹配：DNS hijack、私网/Tailscale 直连、sniff、国内域名直连、域名解析、`geoip-cn` 中国 IP 直连。未匹配的流量使用 `route.final: proxy`。

`geosite-cn.srs` 与 `geoip-cn.srs` 使用固定提交和 SHA256，运行时从设备本地加载。规则来源与缓存校验由 `rules.py` 管理。

## 原生配置

配置使用 sing-box 原生 JSON。节点导入器替换 `proxy` 出站，profile 设置入站端口；其他 outbound、DNS、route 和日志选项可按 sing-box 官方文档配置。

配置安装执行原生 `sing-box check`。gateway tag、端口和规则集路径属于控制器契约，编辑时需要与 profile 一致。
