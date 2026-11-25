# jianpu_output

导出的诗琴简谱存在这里。

在 LyreBot 的曲目列表里右键一首曲子，选「导出简谱」，就会在这个文件夹里生成「曲名.html」，并用浏览器打开。想要 PDF，在浏览器里打印，选「另存为 PDF」即可。

也可以在 LyreBot 文件夹里用命令行导出，一次可以给多个曲谱：

```text
python\python.exe -m lyrebot.jianpu txt/OST/枫丹/柔灯轻漾时.txt
```

这是便携版自带的 Python。用自己装的 Python 运行源码的，把开头的 `python\python.exe` 换成 `python`。

导出的文件不放进仓库，随时可以重新生成。版式和各类曲谱的导出方式见 [原理与开发记录](../docs/原理与开发记录.md#导出简谱)。
