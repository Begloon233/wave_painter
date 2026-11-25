# legacy_specs

这里是从仓库根目录移进来的旧版 PyInstaller 配置，**已不再使用**，仅作历史留档：

- `音频黑线转换器.spec`：v1.0.0 的 Release 产物
  [`wave_painter.exe`](https://github.com/Begloon233/wave_painter/releases/download/2026.10.29/wave_painter.exe)
  就是由它打包出来的（窗口版）。
- `音频黑线转换器_cmd.spec`：带控制台的调试版配置。
- `waveform_ui.spec`：早期配置。

现在统一改用仓库根目录的 [`build.spec`](../build.spec)：

- 一条命令产出各平台产物，不再为每个平台/每种模式各写一份 spec；
- 支持窗口版 / 控制台调试版切换（`WP_CONSOLE=1`）、自定义产物名（`WP_EXE_NAME`）、
  Windows 版本信息（`version_info.txt`）、按平台收集 `tools/ffmpeg/<平台>/`；
- 与 `.github/workflows/build.yml` 中 CI 使用的是同一份配置，保证“本地能打包”等于“CI 能打包”。

需要复刻 v1.0.0 产物时，请先 checkout v1.0.0 对应的提交再打包，而不是直接运行这些旧 spec。
