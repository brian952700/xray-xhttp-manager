# xy：VLESS + xHTTP + TLS 管理脚本

保留原脚本的安装/更新、添加用户、删除用户、查看用户及链接、彻底卸载、退出六项菜单。首次运行且不存在配置时自动进入安装流程。

主要适配目标是 Debian 13（trixie）、systemd、普通 Linux VPS。也保留 apt、dnf/yum 分支，但其他发行版未实机验证。不能在 Windows、未运行 systemd 的容器或 OpenWrt 上直接安装。

## 使用

将 `xy.sh` 上传到服务器，进入文件所在目录后执行：

```bash
bash -n xy.sh
sudo install -o root -g root -m 0755 xy.sh /usr/local/bin/xy
sudo /usr/local/bin/xy
```

如果已经是 root，可省略 `sudo`。以后使用 `sudo xy` 打开菜单。文件是 UTF-8、LF 换行；不要用 `sh xy.sh`，也不要把内容通过管道交给 shell。

首次安装填写域名、监听端口和 xHTTP 路径，默认端口 443，默认路径 `/`。初始用户仍为 `admin`，UUID 从 Linux 内核随机 UUID 接口获取。安装后将输出的 `vless://` 链接导入支持 VLESS、xHTTP、TLS 的客户端。

域名 A/AAAA 记录需正确指向服务器；有 AAAA 记录时，IPv6 也必须可达。安全组和防火墙需允许公网 TCP 80（证书申请及续期）以及选定的节点 TCP 端口。脚本不会自动修改防火墙。80 端口不能用作节点监听端口，因为 standalone 证书验证需要它。服务器还需能够访问 Debian 软件源、GitHub 和证书签发服务。

## 旧脚本升级

覆盖 `/usr/local/bin/xy` 后打开菜单，选择 1。已有的域名、端口、路径、用户和 UUID 都保留；选择 1 不再重置全部用户。

兼容原来的 `/usr/local/etc/xray/config.json`、`/etc/xray-cert/server.crt`、`server.key` 和 `domain.txt`。如果 root 的 `/root/.acme.sh` 中已有当前域名的证书，会复用它并迁移该域名的证书安装路径与重载回调。其他结构的自定义、多入口或 DynamicUser 服务会停止处理，避免错误覆盖。

首次安装使用独立 ACME 目录 `/etc/xray-cert/acme`，通过 `/etc/cron.d/xy-acme` 每日检查续期。旧安装复用其原有 ACME 定时任务。证书尚未到期时不会强制重新签发。保留 acme.sh 自动升级行为。

## 修复内容

- 使用 `xray run -test -config` 校验配置。
- 使用 `jq --arg` 处理用户名、UUID、路径，避免把输入拼成 jq 程序或 sed 替换表达式。
- 配置先生成候选文件，检查通过后替换；服务启动失败时恢复原配置/证书并尝试恢复先前服务状态。
- 续期先写暂存证书；验证有效期、域名和公私钥匹配，并用实际 Xray 服务身份检查配置读取权限。
- 证书目录由 root 持有；读取组按 systemd 实际服务身份确定，不再假定 `nobody:nogroup`。
- 用私有临时文件和操作锁，防止两次管理操作同时覆盖配置。
- 不吞掉配置检查错误；只有检查和服务状态检查均成功才报告部署完成。
- 保留至少一个用户；连接链接的路径、SNI、用户名备注进行 URL 编码。
- 卸载删除 Xray 配置和本脚本的专用证书环境，保留共享的 `/root/.acme.sh`。对复用的旧 ACME 环境只停止当前域名续期，证书存档保留在共享目录中。

## 日志与验证

安装后可运行：

```bash
sudo xray run -test -config /usr/local/etc/xray/config.json
sudo systemctl status xray --no-pager
sudo journalctl -u xray -n 50 --no-pager
```

续期回调日志在 `/var/log/xray_cron_error.log`；专用 ACME 任务日志在 `/var/log/xy-acme.log`。首次安装或升级选择 1 后，可添加一个临时用户，用客户端验证实际连接，再删除该用户验证管理流程。

本交付已进行源代码审查，并核对官方命令、安装器、ACME 回调与 Debian 软件包信息。当前执行环境是 Windows，没有可用的 Debian 13 测试实例；尝试调用本地 Bash 进行语法检查时，运行环境报 `couldn't create signal pipe, Win32 error 5`，因此**不能声称 Bash 语法测试、安装、续期、回滚或端到端代理连接已经实测通过**。请先在 Debian 13 测试 VPS 上完成上述检查。

回滚范围是本脚本部署的配置和证书，不包括 apt/dnf 软件包、Xray 核心二进制或 acme.sh 升级。服务重启会短暂中断连接；检查通过不等于零中断。多文件替换无法保证突然断电或 SIGKILL 时完整回滚；若自动恢复失败，脚本会显示并保留备份目录。健康检查验证启动后服务仍在运行，不替代客户端端到端验证。

脚本继续使用上游最新版安装器和 acme.sh，因此后续上游变化需要重新验证。

## 核对依据

- Xray 的测试参数属于 run 子命令：[Xray run.go](https://github.com/XTLS/Xray-core/blob/main/main/run.go)。
- Xray 官方服务用户、安装/卸载方式：[Xray-install](https://github.com/XTLS/Xray-install)。
- acme.sh 支持自定义 home、独立部署证书、续期后执行 reloadcmd：[安装文档](https://github.com/acmesh-official/acme.sh/wiki/How-to-install)、[项目说明](https://github.com/acmesh-official/acme.sh)。
- Debian 13 软件包：[cron](https://packages.debian.org/trixie/cron)、[util-linux](https://packages.debian.org/trixie/util-linux)。
