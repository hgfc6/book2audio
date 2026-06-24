# Book To Audio

Windows 桌面端图书转音频工具，导入 `epub`、`mobi`、`pdf`，解析章节并生成 `mp3`。

## 功能

- 支持 `epub / mobi / pdf`
- 桌面可视化客户端窗口，不依赖外部浏览器标签页
- 调用 Windows 原生文件 / 目录选择框
- 支持中文女声、中文男声、英文女声、英文男声
- 支持 `每章一个 mp3` 或 `整本合并成一个 mp3`
- 自动识别 `篇 / 章 / 节` 层级并按顺序展开
- 每章输出自动加 `001-`、`002-` 这类前缀，方便本地排序
- `每章一个 mp3` 模式支持并发生成
- 章节列表直接展示转换进度，不再依赖预览区和日志区

## 项目结构

```text
book_to_audio_app.py    主程序
run_book_to_audio.bat   Windows 启动脚本
build_exe.ps1           Windows 单文件 exe 打包脚本
requirements.txt        运行依赖
requirements-build.txt  打包依赖
tests/                  回归测试
```

## 本地运行

1. 安装 Python 3.14 或更高版本
2. 安装依赖

```powershell
python -m pip install -r requirements.txt
```

3. 启动程序

```powershell
python book_to_audio_app.py
```

或者直接双击：

`run_book_to_audio.bat`

## 打包单文件 EXE

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

打包脚本会自动创建本地 `.venv-build`，安装打包依赖，然后输出 `dist/BookToAudio.exe`。

## 测试

```powershell
python -m unittest tests.test_book_to_audio_app -v
python -m py_compile book_to_audio_app.py
```

## 说明

- `edge-tts` 依赖微软语音服务，生成音频时需要联网。
- `mobi` 会先解包再继续提取正文。
- 图形层基于 `pywebview`，关闭窗口时进程会一起退出。
- Windows 文件 / 目录选择框会以隐藏方式调用系统进程，不会额外弹出 `cmd` 窗口。
