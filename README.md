# <img src="assets/lyrebot.png" alt="" height="40" align="absmiddle"> LyreBot

把文本曲谱变成定时的键盘按键，用来在原神等游戏里自动弹琴。也可以从 MIDI 文件、录音或自己的演奏生成曲谱，或者把曲谱导出成简谱。只支持 Windows。便携版自带 Python，下载解压就能用，见 [下载](#下载)；从源码运行需要 Python 3.11 或更新版本，不需要安装其他包。从录音生成曲谱还要另外下载免费的 audio.cpp 和 MuScriptor 模型，见 [从录音生成曲谱](#从录音生成曲谱)。

## 下载

到 [Releases](https://github.com/JasonZiY/LyreBot/releases/latest) 下载 `LyreBot-portable.zip`。它自带 Python，不用另外安装任何东西。

1. 解压之前，先右键这个 zip，选「属性」，勾选「常规」页最下面的「解除锁定」，再点「确定」。不然 Windows 会把解压出来的每个文件都当成网上下载的，启动时弹出安全警告。
2. 解压到一个自己找得到的普通文件夹，例如 `D:\LyreBot`。曲谱、MIDI 和录音都放在这个文件夹里。
3. 打开解压出来的 `LyreBot` 文件夹，照下面的 [使用](#使用) 启动。

电脑上已经装了 Python 3.11 或更新版本的话，也可以直接下载源码（本页上方的 Code → Download ZIP），用法完全一样。

## 使用

1. 双击 `LyreBot.vbs` 启动，不会弹出黑色的命令行窗口。LyreBot 会请求管理员权限，点「是」即可：游戏以管理员身份运行，LyreBot 也要以管理员身份运行，按键才能送进游戏。启动不了时，改为右键 `Start LyreBot.cmd`，选择「以管理员身份运行」，窗口里会显示报错。
2. 在列表里点一下曲名，选中这首曲子。
3. 双击曲名或点 <img src="assets/readme/play.png" alt="▶" height="22" align="absmiddle">，然后在 2 秒内切到游戏并打开琴。倒计时结束时在前台的窗口就是演奏对象。也可以先在游戏里打开琴，再按 <kbd>F7</kbd>：不用倒计时，立刻对着当前窗口弹选中的曲子。
4. 按 <kbd>F8</kbd> 或切换到别的窗口，演奏就会停止。

第一次用，可以点标题栏右上角那本打开的书 <img src="assets/readme/book.png" alt="快速开始" height="22" align="absmiddle">（图钉左边），「快速开始」用六步小图讲怎么用。把鼠标停在按钮上，会显示它的用途。

想一边弹一边看着 LyreBot，点标题栏上的图钉 <img src="assets/readme/pin.png" alt="窗口置顶" height="22" align="absmiddle">（最小化按钮左边），窗口就置顶，一直盖在游戏和别的窗口上面，图钉立起来、头变成橙色 <img src="assets/readme/pinned.png" alt="取消置顶" height="22" align="absmiddle">；再点一次取消。下次打开还记得。点置顶的 LyreBot 同样算切换窗口，演奏会停下。游戏要用窗口化或无边框窗口，独占全屏时什么窗口都盖不上去。

游戏里没有反应时，可以试着把输入法切成英文：中文输入法或许会有影响。

### 窗口和列表

窗口会回到上次关掉时的位置，大小则总是打开时的默认大小（最窄的宽度，也就是简谱页 <img src="assets/readme/shrink.png" alt="缩小" height="22" align="absmiddle"> 缩回的那个大小）。界面的标题用宋体（Noto Serif SC）显示，电脑上没有这款字体时会改用微软雅黑。

列表里的曲目就是 `txt` 文件夹里的 `.txt` 曲谱，文件改动后列表会自动刷新。`txt/OST/地区名` 里的曲谱归到 OST 的对应地区，`txt/歌曲` 里的归到歌曲，其余都是未归类，显示在最上面。新生成的曲谱也放在 `txt` 里，也就是未归类；想归到哪里，自己把文件挪进去，或者右键曲子选「移到…」，挪到别的地区、歌曲或未归类。

打开时只展开 OST，各地区和歌曲都是收起的，点名字展开；名字后面的数字是里面有几首曲子。曲子分两列排在下面，先从上到下排满左边一列，再接着排右边一列。同一组里英文名在前、按字母排，中文名在后、按拼音排，以后加进去的曲谱也一样。用滚轮或方向键上下翻，界面上没有滚动条。

在列表下方、播放栏分隔线上面右侧的搜索框 <img src="assets/readme/search.png" alt="搜索" height="22" align="absmiddle"> 里输入曲名中的字，按 <kbd>Enter</kbd>，会跳到第一首名字里有这些字的曲子；再按一次 <kbd>Enter</kbd> 跳到下一首，到底后从头再来。写错的曲谱前面有 <img src="assets/readme/warning.png" alt="⚠" height="22" align="absmiddle">，选中后底部会显示错在哪一行。

### 演奏

只要 LyreBot 开着，<kbd>F7</kbd> 在哪个窗口里按都算，所以别在打字的窗口里按。演奏时 <img src="assets/readme/stop.png" alt="■" height="22" align="absmiddle"> 是灰的，不用点它：切回 LyreBot 的时候演奏就已经停了。

被打断的曲子会记下停在第几小节，之后点 <img src="assets/readme/play.png" alt="▶" height="22" align="absmiddle"> 或按 <kbd>F7</kbd>，就从那一小节的开头接着弹；点 <img src="assets/readme/play.png" alt="▶" height="22" align="absmiddle"> 左边的 <img src="assets/readme/restart.png" alt="↺" height="22" align="absmiddle"> 则从头弹。按住进度条上的圆点拖动，计数会显示拖到了第几小节，松开后同样倒数 2 秒，从那一小节的开头开始演奏；直接点一下进度条也一样。换了曲子或用进度条选了小节，就不再接着弹。点 <img src="assets/readme/next.png" alt="⏭" height="22" align="absmiddle"> 演奏下一首。

「速度」按比例缩放整首曲子，包括曲中的变速，可选 80%、100%、120%、150% 和 200%，也可以点「自定义…」填 25% 到 300% 之间的任意数值。旁边的选项决定一首弹完以后做什么：「单曲」就此停下，「单曲循环」从头再弹这一首，「顺序演奏」接着弹同一个播放列表的下一首，「随机演奏」像洗牌一样，在这个播放列表里随机挑一首这一轮还没弹的，整个列表都弹过一遍才重新洗，中途自己点的曲子也算这一轮弹过了，后三种会一直弹到按 <kbd>F8</kbd> 或切换窗口为止。OST 和歌曲是两个播放列表：OST 顺序演奏时先弹完本地区，再去下一个地区，随机时在整个 OST 里挑。未归类的曲谱不属于任何播放列表，只能单曲或单曲循环。

### 生成和删除曲谱

曲谱也可以不手写：把 MIDI 文件放进 `midi` 文件夹，LyreBot 会自动编配成「曲名-MIDI」的曲谱加进列表（见 [从 MIDI 生成曲谱](#从-midi-生成曲谱)）；点 <img src="assets/readme/play.png" alt="▶" height="22" align="absmiddle"> 左边的 <img src="assets/readme/record.png" alt="●" height="22" align="absmiddle">，可以把电脑正在播放的音乐录下来，同样编配成曲谱（见 [从录音生成曲谱](#从录音生成曲谱)）；<img src="assets/readme/record.png" alt="●" height="22" align="absmiddle"> 左边的 <img src="assets/readme/keys.png" alt="键盘" height="22" align="absmiddle"> 会把你在游戏里弹的琴记成曲谱（见 [记录演奏](#记录演奏)）。点列表右上方垂下来的橙色书签 <img src="assets/readme/ribbon.png" alt="橙色书签" height="22" align="absmiddle">，翻到选中曲子的简谱页，可以在电脑上试听（见 [试听](#试听)）。右键曲子可以把它移到别处、按 MIDI 重新编配、导出成简谱（见 [导出简谱](#导出简谱)）或删除；右键 OST、歌曲或地区名会打开对应的文件夹。

删除（右键选「删除」，或选中后按 <kbd>Delete</kbd>）把文件移到回收站，需要时可以从回收站还原，删之前会列出要删的文件。手写的曲谱和「曲名-演奏」只删曲谱本身；从 MIDI 或录音编配的曲谱（「曲名-MIDI」「曲名-录音」，以及第一行写着由哪个 MIDI 编配的曲谱）连同 `midi` 文件夹里的那个 MIDI 和同名录音一起删，不然 LyreBot 一两秒后又会从它们生成一份。导出到 `jianpu_output` 的同名简谱也一起删。

## 试听

想检查一份曲谱，不用进游戏。在列表里选中一首曲子，点右上方垂下来的橙色书签 <img src="assets/readme/ribbon.png" alt="橙色书签" height="22" align="absmiddle">，书签被往下一拽、弹回去，纸页就翻到这首曲子的简谱；再点一次书签，翻回曲目。书签弹回去之前再点不算。简谱和「导出简谱」排得一样，每个小节上方用金色标着小节号，窗口拖宽了，一行就多放几小节。

在简谱页上点 <img src="assets/readme/play.png" alt="▶" height="22" align="absmiddle">，LyreBot 就用电脑的扬声器从头弹一遍；点简谱上的任何一个音，就从那个音开始。只有这两样会开始试听。声音来自 Windows 自带的 MIDI 合成器，和游戏里的琴不一样，但每个音的音高和按下的时刻都和在游戏里弹的一样，正好听音高和节奏对不对。从 MIDI、录音和演奏生成的曲谱，尤其值得先这样听一遍。

试听时 LyreBot 留在前台，正在响的音和它所在小节的小节号变成橙色，页面跟着往下翻；自己滚动去看别处时，页面先不跟。<img src="assets/readme/play.png" alt="▶" height="22" align="absmiddle"> 换成 <img src="assets/readme/halt.png" alt="■" height="22" align="absmiddle">，点它或按 <kbd>F8</kbd> 停止，停下时亮着的音，就是下次点 <img src="assets/readme/play.png" alt="▶" height="22" align="absmiddle"> 接着听的地方。听的时候点别的音，直接跳到那里。速度用曲目页「速度」里选的那个，只放一遍。试听时不能演奏、录音或记录演奏；翻回曲目，试听就停下。曲谱文件改了，简谱页一两秒后会照新的重画。

左下角选音色，默认是钢琴，还有竖琴、钢片琴、八音盒、筝、卡林巴等十二种，试听途中换也立刻生效，选过的下次打开还在。音色选项右边的 <img src="assets/readme/grow.png" alt="放大" height="22" align="absmiddle"> 把窗口一下放大：一行正好四小节，最宽的那一行刚好放得下，任意相邻的四行也刚好放得下（屏幕放不下时以屏幕为限）；再点一次（这时它是 <img src="assets/readme/shrink.png" alt="缩小" height="22" align="absmiddle">）缩回最窄的窗口。翻回曲目时，放大了的窗口先缩回去。

## 修改原文

简谱页的橙色书签左边还垂着一条绿色书签 <img src="assets/readme/green.png" alt="绿色书签" height="22" align="absmiddle">。点它，翻到这首曲子的原文，也就是 `txt` 里那个文件，写在一张稿纸上。每个小节那一行的左边标着小节号，和简谱页上的一样；注释、空行和 `speed=` 这样的行不标。直接改，停手半秒 LyreBot 就按曲谱格式查一遍；有错时，那一行垫一层淡红，下面那行说明错在哪里，错在小节里的就说第几小节；这时用鼠标选中的字垫深一点的桃色，和淡红分得开。

左下角两个按钮是撤销 <img src="assets/readme/undo.png" alt="撤销" height="22" align="absmiddle">（<kbd>Ctrl</kbd>+<kbd>Z</kbd>）和保存 <img src="assets/readme/save.png" alt="保存" height="22" align="absmiddle">（<kbd>Ctrl</kbd>+<kbd>S</kbd>），同一行最右边的 <img src="assets/readme/guide.png" alt="?" height="22" align="absmiddle"> 打开一张曲谱格式速查卡，开在窗口旁边，边看边写，再点一次或按 <kbd>Esc</kbd> 收起；用绿色书签翻到简谱页再翻回来，它一直开着，翻回曲目时才收起。撤销最多退回这次翻进来时的样子。有错时不能保存，也不能翻页：点保存或任何一条书签，都只会冒出那句报错。翻页时没存的改动会自动存。点绿色书签回到简谱，简谱照新的重画；点橙色书签回曲目。简谱页和原文页各自记着离开时看到哪里（原文还记着光标在哪），翻回来还在原处；换了一首曲子，就都从开头看起。在原文页时按 <kbd>F7</kbd> 不会开始演奏。关闭 LyreBot 时原文有错，会先问一句。

## 手写曲谱

曲谱是普通的文本文件，一行一个小节，用键盘字母写音，用符号写时值，比如 `a-` 是中音 1 弹一拍，`|` 分开同时演奏的声部。键位、时值、连音、琶音、倚音和变速的写法见 [曲谱格式](docs/曲谱格式.md)。

## 从 MIDI 生成曲谱

把 `.mid` 文件放进 `midi` 文件夹，LyreBot 开着时一两秒后、没开时下次启动时，就会在 `txt` 文件夹里生成曲谱，并在列表里选中它。`飞鼠进行曲.mid` 编出来的曲谱叫「飞鼠进行曲-MIDI」，和录音转出来的「曲名-录音」、键盘记下来的「曲名-演奏」区分开；录音转成的 MIDI（旁边有同名的录音，或者名字以「-录音」结尾）沿用原来的名字。编配时不会演奏，演奏时也不会编配。

已经有同名曲谱（或者叫「曲名-MIDI」的曲谱）的 MIDI 不会再编配，曲谱放在 `txt` 里哪个文件夹都算，所以手改过的曲谱不会被覆盖。曲谱的第一行写着它是从哪个 MIDI 编配的，所以改名或挪进别的文件夹以后，也不会再从这个 MIDI 生成一份旧名字的曲谱。想按 MIDI 从头再来，在列表里右键那首曲子，选「从 MIDI 重新编配」，确认后会照 `midi` 文件夹里那份 MIDI 把整首重新编配一遍，覆盖原来的曲谱；长一些的曲子要几秒，这时状态栏写着「正在重新编配」。录音转出的曲子不会重新转写录音，用的仍是转写好的那份 MIDI。编配失败的 MIDI 只提示一次，换一份同名文件进去会再试。

编配时会自动挑出旋律、移调并重写伴奏。和弦是按音高猜出来的，可能猜错，生成后请先试听（见 [试听](#试听)），再按需修改。自动判断不对时可以用命令行手动指定，参数见 [手动调整](docs/原理与开发记录.md#手动调整)，编配的步骤见 [从 MIDI 编配](docs/原理与开发记录.md#从-midi-编配)。

## 从录音生成曲谱

点 <img src="assets/readme/play.png" alt="▶" height="22" align="absmiddle"> 左边的 <img src="assets/readme/record.png" alt="●" height="22" align="absmiddle">，填上曲名（录音和曲谱都叫「曲名-录音」，不填就按时间起名），然后在别处播放想编配的音乐，视频网站、音乐软件、游戏里的背景音乐都可以。LyreBot 录的是电脑扬声器正在播放的声音，所以开始前先关掉其他声音。有声音时才开始录（等 5 分钟还没有声音就不录了），点 <img src="assets/readme/recording.png" alt="■" height="22" align="absmiddle">、安静 5 秒或录满 10 分钟时结束。

录完以后，录音存成 `midi` 文件夹里的「曲名-录音.wav」，再由 MuScriptor 模型转成同名的 MIDI，最后照上一节的方法编配成 `txt` 里的曲谱并选中它。转录在后台进行，不影响演奏；演奏时不会开始新的转录。也可以直接把音频文件放进 `midi` 文件夹：WAV 可以直接转，MP3、FLAC、M4A、OGG 需要电脑上装有 [ffmpeg](https://ffmpeg.org/)。已经有同名 MIDI 或曲谱的音频不会再转。

录音转出来的曲谱只求弹起来和原曲一样，节拍和小节线不一定规整。钢琴独奏转得最好，配器复杂的曲子（乐队、电子乐）只能当作手改的底稿，转完先听一遍。转录是怎么做的、准确度如何，见 [转录录音](docs/原理与开发记录.md#转录录音)。

### 准备转录工具

转录要用到 [audio.cpp](https://github.com/0xShug0/audio.cpp)（Apache-2.0 许可证）和 MuScriptor 模型（CC BY-NC 4.0 许可证，只能用于非商业用途），LyreBot 没有自带。三个文件一共约 1.3 GB，都不用登录就能下载。一步一步的装法、怎么检查装好了、常见的报错，见 [从录音生成曲谱-安装说明](docs/从录音生成曲谱-安装说明.md)。

## 记录演奏

点 <img src="assets/readme/record.png" alt="●" height="22" align="absmiddle"> 左边的 <img src="assets/readme/keys.png" alt="键盘" height="22" align="absmiddle">，填上曲名（曲谱叫「曲名-演奏」，不填就按时间起名），然后切到游戏，打开琴照常弹。LyreBot 在后台记下你按的每一个琴键和按下的时刻；弹完按 <kbd>F8</kbd>、切回 LyreBot 点 <img src="assets/readme/recording.png" alt="■" height="22" align="absmiddle">，或者 30 秒不弹，就把记下的按键写成 `txt` 文件夹里的曲谱并选中它（5 分钟内一个琴键都没按就不记了，最长记 30 分钟）。只记 21 个琴键；按着 <kbd>Ctrl</kbd>、<kbd>Alt</kbd> 或 <kbd>Win</kbd> 时按的键、LyreBot 自己的窗口在前台时按的键都不算；按住不放的键只算一次。别的程序模拟出来的按键也会记下，所以别的自动弹琴工具弹的曲子同样能记成曲谱。

写谱的目标只有一个：用 LyreBot 弹回去时，每个键按下的时刻都和你弹的一样。写好后会选中这份曲谱，底部显示有百分之几的按键在 0.05 秒内照原样弹回。手越稳，写出来的曲谱越整齐。写谱的方法和测试结果见 [记录演奏](docs/原理与开发记录.md#记录演奏)。

## 导出简谱

在列表里右键一首曲子，选「导出简谱」，就会在 `jianpu_output` 文件夹里生成同名的网页，并用浏览器打开。想要 PDF，在浏览器里打印，选「另存为 PDF」即可。导出的简谱只存在自己电脑上的 `jianpu_output` 文件夹里，不放进仓库，随时可以重新生成。

录音编出来的和键盘记下来的曲谱，导出时节奏会整理得便于阅读，右上角有注明，演奏仍以曲谱为准，曲谱本身不会改。版式和各类曲谱的导出方式见 [导出简谱](docs/原理与开发记录.md#导出简谱)。

命令行也可以导出，可以一次给多个曲谱：

```text
python -m lyrebot.jianpu txt/OST/枫丹/柔灯轻漾时.txt
```

## 更多文档

- [曲谱格式](docs/曲谱格式.md)：手写和修改曲谱时查阅。
- [从录音生成曲谱-安装说明](docs/从录音生成曲谱-安装说明.md)：一步一步装好转录工具。
- [原理与开发记录](docs/原理与开发记录.md)：编配、转录、记录演奏和导出的原理，测试结果，代码结构。

## 曲谱来源

`txt` 文件夹里的曲谱，一部分转写自 <https://space.bilibili.com/76052941> 发布的诗琴简谱，其余来自公开的音频和乐谱资源。乐曲和编曲归各自的作者所有。

## 许可证

代码采用 [MIT 许可证](LICENSE)。`txt` 里的曲谱不在此列。
