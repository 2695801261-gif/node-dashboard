# 节点小站（GitHub Pages）

一个极简中文静态网页：汇总公开发布的 Clash/Mihomo 订阅入口，并提供一键复制与导入说明。

## 3 步免费上线

1. 登录 GitHub，创建一个 **Public** 仓库，例如 `node-dashboard`。
2. 将本压缩包内的所有文件上传到仓库根目录（包括 `.github` 文件夹），默认分支使用 `main`。
3. 打开仓库 **Settings → Pages → Build and deployment → Source**，选择 **GitHub Actions**。等待 Actions 里的部署任务成功后，访问 `https://你的用户名.github.io/node-dashboard/`。

之后每次更新网页文件，GitHub Actions 会自动重新部署。网页中的订阅由各上游项目自行更新，不需要你手动改网页。

## 当前公开订阅入口

- 美国侧测试筛选（Mihomo）：https://github.com/learnLi/free-nodes/releases/latest/download/list.meta.us.yml
- Free Nodes Radar（Clash）：https://radarhub.top/clash.yaml
- Thordata（Clash HTTP/SOCKS5）：https://thordata.github.io/awesome-free-proxy-list/data/clash/all.yaml

## 重要说明

- “美国侧测试”不等于出口 IP 一定在美国；如需严格美国出口，请在客户端按国家/地区再次筛选。
- 公共节点无法保证持续在线、速度、隐私或 IP 信誉，也不能保证不会触发任何第三方平台的风控。
- 不要通过免费节点处理网银、邮箱或其他敏感信息。
- 本项目只是公开订阅入口目录，不运行代理服务器、不托管节点内容。
