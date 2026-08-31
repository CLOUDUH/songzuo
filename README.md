# 松坐（songzuo）

一个面向局域网 MJPEG 摄像头的轻量久坐监测服务：只分析“座位区域内是否有人”，记录坐下/离座时间与时长，达到阈值后通过 Bark 提醒，并提供日、周、月统计网页。

## 为什么选 Docker

建议直接部署到现有的 Docker 虚拟机，不再新建 PVE 虚拟机：摄像头走局域网，不需要 USB 直通；单容器只保留一个 Python 进程，网页在构建阶段编译成静态文件，由同一进程提供。这样省掉一套 Guest OS 和常驻 Node 服务，备份、升级也更简单。

运行时数据流：

```text
MJPEG 摄像头 → 字节流读取（只保留内存最新帧） → 低频人物检测 → 状态防抖
                                                        ├→ SQLite 会话/统计
                                                        ├→ Bark / Webhook
                                                        └→ Web 仪表盘
```

## 已实现

- MJPEG 持续读取，支持无认证、Basic、Digest 和登录 Cookie。
- Web 内置摄像头设置入口；新连接必须先收到有效 MJPEG 帧才会保存和切换，失败时保留原连接。
- `320×320`、每 2–5 秒一次的低频检测；OpenCV 线程数可限制。
- 镜像内置 OpenCV 官方 YuNet 轻量 DNN，在座位 ROI 内按真实置信度、近景人脸尺寸和头部位置联合判断；不再使用容易把椅背网格认成侧脸的 Haar 作为默认检测。
- 仅当人物框中心位于可配置的座位 ROI 内才算“在座位上”。
- 坐下/离座独立确认；可设置连续会话合并窗口，短暂离座仍归入同一次会话，但离座分钟数从坐姿与久坐时长中扣除。
- SQLite 仅保存会话、设置和推送日志；摄像头 JPEG 只在内存保留最新一帧，不落盘、不保存视频。
- 记录坐下时间、站起时间、持续时长、是否久坐、提醒时间和平均置信度。
- 今日首页、横向会话时间轴、同期周/月平均对比，以及独立的周/月汇总页面。
- 独立“按日查询”页面，可选择具体日期查看时间轴、逐次时间戳、累计/平均时长、久坐次数、离座时长和当天分析。
- 摄像头、推送和监测设置合并为一个完整的“系统设置”页面，不依赖侧滑弹层。
- Bark Server、设备 Key、Webhook、提醒文案和汇总时间均可在 Web 设置；支持即时提醒、每日小结和每周报告。
- 入口 HTML 禁止缓存、带哈希资源长期缓存，避免 Chrome 在容器更新后保留旧交互代码。
- 一次性历史修复会先将原会话与离座记录写入 `repair_backups`，再重建用户确认的 2026-08-28 至 2026-08-30 数据，重复启动不会再次执行。
- 摄像头断线自动重连；断线时不会误判成离座，并支持延迟离线提醒及恢复上线通知。

## 快速部署

在现有 Docker VM 中执行：

```bash
cd songzuo
cp .env.example .env
```

编辑 `.env`：

1. 可以先保留默认摄像头配置，启动后在 Web 左侧点击“摄像头”，填写地址和认证信息并选择“测试并保存摄像头”。当前初始地址为 `http://192.168.1.80:2345/`，但有些设备首页和流地址不同。
2. 也可以在首次启动前填写 `CAMERA_USERNAME` / `CAMERA_PASSWORD`。通过 Web 保存后，配置会写入 `data/camera-settings.json` 并优先于 `.env`；密码和 Cookie 不会通过 API 回传。
3. Digest 认证把 `CAMERA_AUTH_TYPE` 改成 `digest`。若只能网页登录，可将浏览器登录后的 Cookie 临时放在 `CAMERA_COOKIE`。
4. Bark 无需写入 `.env`：启动后进入 Web 的“消息推送”，粘贴设备 Key 或 Bark App 的完整测试链接，再选择“保存并测试”。

然后启动：

```bash
docker compose up -d --build
docker compose logs -f --tail=100
```

容器以内置 UID/GID `1000:1000` 运行。若日志提示 SQLite 无法写入，在 Docker VM 中执行一次 `sudo chown -R 1000:1000 data` 后重启即可。

打开 `http://Docker虚拟机IP:8787/`。健康检查在 `/api/health`，接口文档在 `/api/docs`。

`.env`、运行时摄像头配置和整个 `data` 内容都不会进入 Docker 构建上下文。不要把账号、密码、Cookie 或 Bark key 写进 `docker-compose.yml`。

### 使用 Portainer

Docker Standalone 环境可在 Portainer 中选择 `Stacks → Add stack → Git repository`，仓库指向 `https://github.com/CLOUDUH/songzuo.git`，Repository reference 填 `refs/heads/main`，Compose path 填 `portainer-stack.yml`。该文件通过 Compose 的 `${变量名}` 直接接收 Portainer 页面中填写的环境变量，无需在 Git 仓库中保存 `stack.env`，并用命名卷 `songzuo-data`、`songzuo-models` 保留数据。

Portainer 只需保留 `TZ=Asia/Shanghai`、检测器与资源限制等运行参数。摄像头、Bark 和 Webhook 均在部署完成后的 Web 页面设置，不需要把秘密放进 Stack 环境变量。

Portainer 对 Git Stack 内 `build:` 的支持取决于版本和环境连接方式。如果部署日志显示构建步骤失败，应先在 CI 或 Docker 主机上构建并推送镜像，再把 `portainer-stack.yml` 中的 `image` 改为实际镜像地址并删除 `build` 段。

如果 Portainer 无法稳定连接 GitHub Git 服务，仓库内的 `.github/workflows/container-image.yml` 会在每次推送 `main` 后将镜像发布到 `ghcr.io/clouduh/songzuo:latest`。等待 GitHub Actions 构建成功并将 GHCR Package 设为 Public 后，可在 Portainer 选择 `Stacks → Add stack → Web editor`，粘贴 `portainer-image-stack.yml` 的内容部署。该方式只拉取镜像，不需要 Portainer 克隆 Git 仓库或执行构建。

## 人物模型

默认 `auto` 模式使用镜像内置的 OpenCV Zoo YuNet 模型，不需要另外下载。该模型只有约 227 KB，针对当前固定工位只检查校准后的座位 ROI，并通过真实置信度、最小人脸宽度和头部位置过滤椅背网格、显示器边缘与远处人员。

Web 的“监测设置”中可以调节人脸置信度和近景人脸最小宽度；当前机位推荐 `60% / 9%`。若仍出现静态误报，可依次提高到 `65% / 11%`，无需重新构建镜像。

YOLOv8n 仅作为可选诊断模式。需要时可准备 ONNX：

在装有 Docker 且能联网的机器上：

```bash
./scripts/export-yolo.sh
```

脚本会在一次性容器中导出 `models/yolov8n.onnx`；只有显式设置 `DETECTOR_MODE=yolo` 时才会使用它。

## N95 推荐参数

先采用：

```dotenv
DETECTOR_MODE=auto
DETECTOR_INPUT_SIZE=320
OPENCV_THREADS=2
```

Web 设置中的采样间隔先选 3 秒。如果仍需省资源，改为 5 秒；若漏检明显再改为 2 秒。容器默认限制为 1.5 CPU、512 MB 内存，可按实测调整。不要把采样频率提高到视频帧率——久坐判断是分钟级问题，高帧率没有收益。

座位 ROI 已按当前摄像头画面中的红框校准为 `x=0.13, y=0.54, w=0.37, h=0.43`。旧版默认 ROI 会在首次启动新版本时自动迁移；这些数值均为画面宽高的 0–1 比例。

## 统计口径

- 坐下：连续两次采样在座位 ROI 内检测到人物，从第一次检测时刻起算。
- 站起：持续无人超过“离座确认时间”后立即显示离座；结束时间仍记为第一次无人时刻。
- 短暂离座：在“连续会话合并窗口”内返回时仍属于同一次会话，离座区间保存在 `session_breaks` 并从坐姿时长扣除。
- 久坐：单次会话扣除全部离座时间后达到阈值才标记，提醒每个会话只尝试一次。
- 日/周/月：按 `TZ` 配置的本地时区切分；跨午夜会话按实际重叠时长计入各天。
- 当前未结束会话也实时计入统计。

SQLite 文件位于 `data/songzuo.db`，WAL 模式并为常用时间查询建了索引。摄像头与推送秘密分别位于权限为 `0600` 的 `data/camera-settings.json`、`data/notification-settings.json`；备份整个 `data` 目录时应像保护密码一样保护备份。图片与视频无需清理，因为从不写盘。

## Bark 与其他推送

Bark 很适合这套 Apple 设备组合：iPhone 收到的系统通知可按 Watch 的通知设置同步到 Apple Watch，也无需为项目申请 APNs 证书。服务使用 Bark V2 JSON `POST /push` 接口，支持官方服务或自建 Bark Server。

如果以后已有 Home Assistant / MQTT，可在 Web 中配置通用 Webhook，再由它分发到多个平台；在当前单人、Apple 设备为主的场景中，Bark 是更精简的选择。

## 本地开发与测试

前端：

```bash
pnpm install
pnpm dev
pnpm test
pnpm build
```

后端：

```bash
python -m pip install -r backend/requirements-dev.txt
python -m pytest backend/tests
```

前端开发服务器会把 `/api` 代理到 `127.0.0.1:8000`。直接打开前端但没有启动 API 时，会显示明确标注的演示数据。

## 主要 API

- `GET /api/overview?period=day|week|month`：仪表盘数据
- `GET /api/report?period=week|month`：独立周期报告
- `GET /api/day?date=YYYY-MM-DD`：指定日期详情和当天分析
- `GET /api/stats`：今日、本周、本月汇总
- `GET /api/sessions?period=day|week|month`：会话明细
- `GET/PUT /api/settings`：监测、提醒和 ROI 参数
- `GET/PUT /api/camera/settings`：读取脱敏配置、验证并安全切换摄像头
- `GET/PUT /api/notifications/settings`：读取脱敏状态、保存 Bark/Webhook 配置
- `POST /api/notifications/test`：测试推送
- `GET /api/health`：容器、摄像头和检测器状态

## 安全边界

默认假设 Web 页面只暴露在可信局域网。若要通过公网访问，请放到带 HTTPS 和认证的反向代理后，不要直接映射 8787 端口。摄像头密码、Cookie 和 Bark key 不通过 API 返回；摄像头秘密与分析数据库分开保存。
