# foobar2000 便携定制版生成器

从官方 foobar2000 2.26.2 x64 安装包、作者发行的组件包、官方编码器包和 Apple 安装包重新组装，并将指定 OKE qaac 目录完整复制到 `encoders\qaac`。构建过程不修改原 foobar2000 或 OKEGUI。

## 使用

需要 Windows 10/11、Python 3.10+、7-Zip，以及系统自带的 curl.exe。不需要管理员权限（目标目录需要可写）。无需安装 iTunes、Winamp 或修改系统 PATH。

在此目录的 PowerShell 中执行：

```powershell
.\Build-Foobar2000.ps1
```

默认应用目录为 `artifact\foobar2000`，转码输出目录为 `artifact\foobar2000\converted`。启动 `artifact\foobar2000\foobar2000.exe`。

qaac 默认来源为 `C:\Tools\OKEGui\tools\qaac`。此目录必须存在，包含 qaac64.exe、Apple 编码依赖及 libsoxr64.dll。复制全部文件和子目录，不通过 PATH 查找其他 qaac。可指定其他来源：

```powershell
.\Build-Foobar2000.ps1 -QaacDirectory 'D:\OKEGui\tools\qaac'
```

指定应用目录和转码输出目录：

```powershell
.\Build-Foobar2000.ps1 -TargetDirectory 'D:\Audio Tools\fb2k' -OutputDirectory 'D:\Audio Output'
```

六个自定义预设（`alac_multidisc`、`flac_multidisc`、`flac_image`、`flac_tracks`、`aac_image`、`wv_tracks`）默认均直接输出到指定目录，等同于 `-OutputMode Fixed`。构建时可统一切换为转换时询问目录：

```powershell
.\Build-Foobar2000.ps1 -TargetDirectory '.\artifact-ask\foobar2000' -OutputDirectory 'D:\Audio Output' -OutputMode Ask
```

询问模式下，`-OutputDirectory` 初始化六个预设的上次使用目录。不传时仍使用应用目录下的 `converted`。默认预设 `[default]` 的设置保持不变。Python 入口对应参数为 `--output-mode fixed` 或 `--output-mode ask`；构建清单记录该模式，`-VerifyOnly` 会校验六个预设是否符合记录。

```powershell
# 仅下载并核对所有锁定的依赖
.\Build-Foobar2000.ps1 -DownloadOnly

# 已有 cache 和 OKE qaac 来源目录时完全离线构建；目标必须不存在或为空
.\Build-Foobar2000.ps1 -Offline -TargetDirectory '.\artifact-new\foobar2000'

# 校验尚未使用过的产物
.\Build-Foobar2000.ps1 -VerifyOnly
```

已有非空目标目录会被拒绝，构建不会覆盖现有软件。运行中途失败时，已下载且通过校验的文件保留在 `cache`，不发布未完成的应用目录。

生成目录及输出目录支持中文、空格、`#`、`%`；SQLite 校验使用经过转义的文件 URI。输出目录按实际参数逐项核对，不使用旧机器路径的字符串黑名单。缓存检查仅针对已知配置目录位置，不会因普通输出目录名包含 `AccurateRip`、`vgmstream` 等词而误判。发布前会创建并检查输出目录的写入权限，并完成产物校验；外部输出目录可能在构建失败时保留为空目录。原 foobar2000 的目录保护规则保持不变。

AAC 自定义编码器固定使用 `./encoders/qaac/qaac64.exe`。该写法已通过真实转换和目录搬迁测试；请保留开头的 `./` 与正斜杠。FFmpeg 从应用根目录自动识别。转码输出目录仍按指定位置保存为绝对路径，移动软件后需要检查输出目录设置。

## 保留的功能和配置

- 官方 2.26.2 x64 核心、8 个官方组件、帮助、运行库和全部官方主题。与参考目录一样，不安装 `foo_out_upnp`、卸载器或系统文件关联工具。
- 8 个第三方组件：Cuesheet Creator 0.4.6d、DR Meter 1.0.8、DSD Processor 1.3.2、DVD-Audio 0.8.1、FFmpeg Wrapper 0.9.2、SACD 1.5.11、TAK 0.5.9、TTA 4.0.5。
- DSD Processor 从 SACD 1.5.11 的配套包提取；单独的 1.3.2 发行包与参考目录的 DLL 不同，不能混用。
- `Depth`、`Sampling rate`、`Album with Dep&SR`、原主题和组件格式规则。
- 六个自定义转码预设及默认预设，保留文件名模板、标签/封面传递、ReplayGain、解码后处理等设置。
- AAC 的 `-i -q 2 -v 192 --no-delay -o %d -` 参数不变，使用 `./encoders/qaac/qaac64.exe`。转换器的自定义格式列表与 aac_image 预设均使用这个相对路径。
- OKE qaac 文件夹完整复制，保留其 Apple、soxr、convolver、FLAC 和运行库。原有编码器包文件及 `encoders\QTfiles64` 保留，其他格式的编码器布局不变。
- 六个自定义预设默认直接使用指定输出目录；可用 `-OutputMode Ask` 统一改为每次询问，并初始化上次使用目录。其他转换设置保持不变。
- 在应用根目录附带实际的 FFmpeg 9.0.1 工具。插件自动识别应用目录中的 ffmpeg/ffprobe，不依赖本机 `C:\local\bin` 启动器或全局 PATH，也无需改写组件自带校验的二进制配置。

官方编码器包中的 fhgaacenc 前端按原样附带；它和参考安装一样，需要另行提供 Winamp 的实际 Fraunhofer 编码库才能使用。六个保留预设均不依赖它，AAC 由已附带 Apple 依赖的 qaac 完成。

## 排除的状态

不导入 vgmstream 组件或残留配置、媒体数据库、播放列表、AccurateRip 缓存、组件更新下载缓存、`config.sqlite.bad`、累计播放时间和上次使用预设。保留当前主题、界面设置及偏好设置页面的展开状态。软件首次运行后会自行创建必要的空数据库，这是正常行为。

`config-template` 是最小配置模板，含 SQLite 配置的可读 JSON、主题、组件配置和 INI 预设。二进制字段以 Base64 保存，编码器路径使用占位符并在生成时按长度字段重建。保留未知的用户配置项，避免丢失现有功能设置。

## 可复现性

`sources.lock.json` 固定下载包的版本、上游 URL 和 SHA-256。播放器、插件与原编码器包从这些下载包提取。嵌套 qaac 来自 `-QaacDirectory` 指定的本地 OKE 目录，构建清单另行记录其来源路径和每个文件的 SHA-256。复制前后核对来源及目标内容，来源在复制过程中变化会使构建失败。以后更新 OKE qaac 后，重新构建会复制更新后的内容；如需严格重现，请保留对应的来源目录快照。

Apple 安装包仅解包，不执行安装。`cache` 仅供构建使用，位于产物之外。

`artifact\foobar2000\build-manifest.json` 记录来源、模板哈希和产物文件哈希。应用运行后配置、数据库会变化，`-VerifyOnly` 此时可能报告变化；它主要用于验证刚生成的干净产物。

如需修改模板，应同时重新计算 `config-template\manifest.json` 中的 SHA-256；如需升级软件，应核对新的上游包、哈希和兼容性，不直接替换版本号或跟随 latest 链接。

`work` 保存本次验证数据和测试用副本；它不参与后续构建。

## 音频自检

```powershell
python .\test_build.py

# 同时对比当前 OKE qaac 的实际 AAC 码流
python .\test_build.py --qaac-reference 'C:\Tools\OKEGui\tools\qaac\qaac64.exe'
```

使用自生成的 16-bit/44.1kHz 和 24-bit/96kHz 音频，清除测试子进程的全局编码器 PATH，执行 FLAC/WavPack/ALAC 的 PCM 无损往返、AAC no-delay，以及 MP3/Opus/Vorbis/Musepack 编码与解码。结果写入 `work\smoke\results.json`，不使用用户音乐文件。

自检调用 `encoders\qaac\qaac64.exe`。指定参考 qaac 后，还会比较 16-bit/44.1kHz 与 24-bit/96kHz 输入的 AAC 裸码流，忽略 MP4 容器时间戳。测试副本确认全部 8 个第三方组件已加载。未通过 UI 逐项执行 SACD/DVD-Audio/TAK 等格式的解码测试。
