# StickerSaver

一个用于小范围内测的跨端“表情搜索与保存到相册”原型：

- `server/`：轻量 Python 服务端，接入 GIPHY 官方 API 并隐藏 API Key。
- `app/`：Android 原生 App。
- `ios/`：iOS SwiftUI App。

## 服务端

```powershell
$env:GIPHY_API_KEY="your-giphy-api-key"
python server/sticker_server.py
```

默认地址是 `http://127.0.0.1:8080`，搜索接口：

```text
GET /api/stickers/search?q=哈哈&page=1
```

健康检查：

```text
GET /health
```

## Android

Android App 默认服务端地址是 `http://10.0.2.2:8080`，适合 Android 模拟器访问本机服务端。真机测试时，把 `MainActivity.SERVER_BASE_URL` 改成电脑局域网 IP 或线上服务地址。

功能：

- 关键词搜索 GIPHY GIF/Sticker
- 预览动图
- 保存到系统相册 `Pictures/StickerSaver/`
- 接收其他 App 分享来的图片/GIF 并保存

## iOS

iOS App 位于 `ios/StickerSaver.xcodeproj`，使用 SwiftUI，无第三方依赖。

默认服务端地址是 `http://127.0.0.1:8080`，适合 iOS Simulator 访问本机服务端。真机测试时，把 `StickerAPI.baseURL` 改成电脑局域网 IP 或线上 HTTPS 地址。

功能：

- 关键词搜索 GIPHY GIF/Sticker
- 使用 `WKWebView` 预览 GIF 动图
- 请求照片写入权限
- 保存 GIF 到 iPhone 照片 App
- 记录最近保存

## 测试

```powershell
python -m unittest discover -s server -p "test_*.py"
```

本项目没有 JavaScript 文件，因此不需要运行 `npm test`。

## 发布

Android APK 由 GitHub Actions 构建：

- 推送到 `main` 会生成可下载的 workflow artifact。
- 推送 `v*` 标签会创建 GitHub Release，并附带 `StickerSaver-android-debug.apk`。

示例：

```powershell
git tag v0.1.0
git push origin v0.1.0
```

iPhone 版本不能像 APK 一样从 GitHub 直接下载安装，需要在 macOS 上用 Xcode 签名安装，或通过 TestFlight 分发。
