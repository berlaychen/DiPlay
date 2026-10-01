# DiPlay Linux：原生版與 Linux 後端＋Web 前端

本目錄新增兩個入口，共用上游協定核心，不改 Android 模組。

| 入口 | 實作 | 主要用途 |
|---|---|---|
| `--mode native` | GTK 3＋GStreamer，沒有 Electron／Android 模擬器 | T100、接螢幕的 Pi |
| `--mode web` | Linux 處理 CarPlay；瀏覽器 WebCodecs＋AudioWorklet | 同機或另一台裝置顯示 |

目前是 **無線優先的工程預覽版**。Linux USB 有線、Windows、BYD HUD、完整圖形設定頁尚未移植。編譯與合成影音測試不代表真實 iPhone 已連通；開發環境沒有 T100／Pi／iPhone 實機，不能宣稱硬解、Siri、長途及供電已通過驗證。

## 先測原生／Web 畫面

在 Debian 13／Raspberry Pi OS Trixie 64-bit／Ubuntu 24.04 上安裝依賴，再使用此 fork 的 Actions 預覽套件，或照英文 README 編譯核心：

```sh
./desktop/install-deps.sh
./desktop/diplay --demo --mode native
# 或：
./desktop/diplay --demo --mode web
cat ~/.local/state/diplay/web-token
```

瀏覽器開 `http://127.0.0.1:8765`，輸入 token，按 Enable sound。Demo 是合成測試圖／聲音，不是 CarPlay。Native 需要 X11／Wayland 圖形工作階段；Web 後端不用桌面。

## 內建憑證已納入移植流程

Linux 共用 DiPlay 的本地 P-256 認證，不會漏掉配對私鑰。但原始 Git 沒有身份檔，檔案位於上游 Release APK。提供兩種明確執行的匯入方式：

```sh
# 下載固定版本 0.2.8、比對 SHA-256、只匯入兩個身份檔：
./desktop/diplay-auth fetch-upstream --accept-experimental-identity
./desktop/diplay-auth check
```

已有 APK 時，不必重新下載：

```sh
./desktop/diplay-auth import-apk /path/to/DiPlay-0.2.8.apk \
  --sha256 9b36a0866608244e422053d6027706b4672d8f2f4a8be9eb7e612411ffb6bf48 \
  --accept-experimental-identity
```

工具不執行 APK，只讀 `identity.pk8` 與 `certificate.p7b`，檢查檔案上限、重複／符號連結 ZIP 項目、P-256、憑證與私鑰配對及簽章。匯入目錄權限為 0700、檔案 0600；不覆蓋既有不同身份。

完成後 Native 與 Web 都使用 `~/.config/diplay/identity`，這條路 **不需要 dongle 或 Remote MFi server**。初次下載需要網路；之後本地簽章不依賴認證伺服器。

**這仍是上游實驗身份，不是 Apple 認證或未來 iOS 保證。** 驗證成功表示私鑰／憑證相符，不代表 iPhone 必然接受。CI 只保存檢查報告，不上傳私鑰。不要把 APK、身份、token、配對資料加入公開 Git。

也支援自己已有的相容 Remote MFi，設定方法見英文 README。單獨自簽憑證不會產生 Apple 信任。

需要離線搬到目標機時，可明確製作私人部署包：

```sh
python3 desktop/package.py /tmp/diplay-private.tar.gz \
  --with-identity ~/.config/diplay/identity --private-deployment-bundle
```

此檔不能公開發佈。在目標機把 `[auth].directory` 指到解壓後 `desktop/runtime-identity` 的絕對路徑。

## 設定 T100／Pi

```sh
mkdir -p ~/.config/diplay
cp desktop/profiles/t100.toml ~/.config/diplay/config.toml
chmod 600 ~/.config/diplay/config.toml
```

Pi 改用 `rpi4.toml` 或 `rpi5.toml`。

| 機器 | 起始設定 | 尚須驗證 |
|---|---|---|
| T100／Z3740／2 GB | 960×540、H.264、30 fps、native | i965、Broadcom AP／藍牙、SST 音訊、觸控 |
| Pi 4 | 1280×720、H.264、30 fps | V4L2 硬解是否實際可用 |
| Pi 5 | 1280×720、H.264、30 fps、軟解 | Pi 5 沒有 H.264 專用硬解；量測 CPU／散熱 |

程式不要求 LIVI 的 Electron／OpenGL ES 3.x 組合，也沒有零拷貝承諾。JVM heap 限制 256 MB 不代表整機程式只占 256 MB。T100 的 32-bit UEFI 與 x86_64 userspace 不同；本專案不刷 BIOS、不安裝 OS。

先用 Linux 藍牙設定／`bluetoothctl` 與 iPhone 配對，填入 `[network].phone`。准备專用 WPA2 AP，設定檔的介面／SSID／密碼／頻道／IP 必須和實際值相符。Linux 用一般 AP，不依賴 Android Wi-Fi Direct。可選用：

```sh
./desktop/setup-ap.sh ~/.config/diplay/config.toml --create-dedicated-ap
```

**這會占用指定 Wi-Fi 介面，可能中斷原連線，請用本機或 Ethernet 操作。** 工具拒絕覆寫同名 profile，預設 AP 為 `10.42.0.1`，關閉自動連線，並列出還原指令。以 `iw list`、`iw dev wlan0 info` 確認 AP 能力／實際頻道，國碼須符合所在地要求。

匯入身份並完成 AP／藍牙設定後：

```sh
./desktop/diplay --doctor --config ~/.config/diplay/config.toml
./desktop/diplay --mode native --config ~/.config/diplay/config.toml
# 或 Web，勿同時占用相同 radio／port：
./desktop/diplay --mode web --config ~/.config/diplay/config.toml
```

`--doctor` 看得到 decoder 名称，不表示硬解成功。先測音樂，再開 `[audio].microphone=true` 測 Siri／電話。Web 麥克風另外需要使用者允許。初期驗收請停車進行。

## Web 安全與限制

預設只聽 `127.0.0.1`。遠端可用 SSH port forwarding，或設定 TLS 憑證／私鑰及精確 `allowed_origin`。WebCodecs、麥克風需要 HTTPS／localhost；不支援直接公開未加密、免認證控制頁。

WebSocket 檢查 token／Host／Origin，只允許一位操作者，影音佇列設有上限。後端轉送壓縮 H.264，不重新編碼；音訊轉成 PCM，由瀏覽器播放。

必須另外做真機認證、Wi-Fi 交接、畫面、觸控、音樂、導航混音、Siri、來電、重連和長時間測試。本版不以 demo 成功代替這些驗收。詳細範圍見 `VALIDATION.md`。
