# LyreBot

LyreBot reads a text music score and generates timed keyboard presses for the 21 physical QWERTY positions `qwertyu`, `asdfghj`, and `zxcvbnm`. Version 0.1 is a Windows desktop prototype.

The input is a `.txt` score. Converting sheet music, images, PDFs, or audio into a score is outside this application's scope. The existing numbered-notation conversion skill can continue producing those files separately.

The complete [current transformation protocol](#current-transformation-protocol) is included below for inspection. It records the saved conversion rules alongside the player's existing extensions. An available player feature does not automatically change the saved converter's defaults; this documentation update does not select new rules.

## Start here（快速上手）

The interface is in Chinese.

1. 双击 **Start LyreBot.cmd**（需要 Python 3.10+，无需额外安装包）。
2. 原神需要管理员权限才能接收按键：界面顶部若提示未以管理员身份运行，点「以管理员身份重新启动」，或右键启动脚本选择「以管理员身份运行」。
3. 左侧列表自动列出 `music` 文件夹中的所有 `.txt` 曲谱。单击查看信息；格式有误的曲谱会显示错误所在行。
4. 点「开始演奏」或双击曲目，在倒计时内切换到游戏并打开琴。倒计时结束时位于前台的窗口就是演奏目标；如果那时前台仍是 LyreBot，则不会发送任何按键。
5. 停止：按住 **F8**、切换到其他窗口、按住 Ctrl/Shift/Alt/Win，或把鼠标移到主屏幕左上角。
6. 如果游戏中没有声音，请确认以管理员身份运行，并把输入法切换到英文模式：中文输入法会把字母键当作拼音拦截。

「速度（%）」按比例缩放整首曲子（包括曲中变速）；「按键时长」是每次按下的毫秒数；「倒计时」是开始前的等待秒数。

The earlier Preview, test pad, example, and target-window picker were removed; the `examples/` folder is no longer used by the interface.

## The structure

| Component | Responsibility |
| --- | --- |
| Score reader | Check the text and interpret notes, chords, rests, and durations |
| Timeline compiler | Combine the simultaneous parts into one schedule of press/release events |
| Playback engine | Follow fixed clock deadlines; watch for stop conditions |
| Windows sender | Generate physical keyboard scan-code events using Windows `SendInput` |
| Desktop interface | Open files, preview notes, choose a target, set tempo/taps/countdown, and stop |

The score reader, timing engine, and Windows sender are separate so future format extensions do not require replacing the whole application. The format is documented in [docs/score-format.md](docs/score-format.md).

## Current musical behavior

- `speed=120` means 120 quarter-note beats per minute. The Tempo box overrides playback speed without editing the file.
- The familiar punctuation durations, chords, rests, two simultaneous parts, and continuation across measures are supported.
- Optional `{fraction}` durations express exact rhythms, including triplets. For example, `a{1/3}s{1/3}d{1/3}` occupies one beat. This LyreBot extension is not claimed to work in Qin Xian.
- Notes use **short taps** (35 ms by default). Written duration determines musical timing; it does not hold the key for the whole note. Taps shorten automatically where a repeated note needs space for release.
- Tied continuations extend timing without another tap. Two simultaneous requests for the same key become one tap; an explicit later request retriggers it.
- The 21 positions are physical QWERTY keys. They are not arbitrary text characters: with another keyboard layout the test pad may display different letters.
- Score lines have one or two parts separated by `|`. Each part on a line must occupy the same number of beats. A file keeps the same number of parts throughout. There is no time-signature declaration or automatic correction of missing rests.

## Playback controls and limits

Keyboard output starts only when you click **Play to window**. Opening a file and Preview never send keyboard input.

Real playback requires the selected window to be foreground at countdown completion and during playback. LyreBot does not switch focus for you. The window check and Windows input submission are separate operations, so a tiny focus-change race remains possible; use the test pad first and keep unrelated applications out of the way.

Playback stops when F8 is held, the foreground window changes, a Control/Shift/Alt/Windows modifier is held, the mouse reaches the primary screen's top-left, or the timing engine falls more than 150 ms behind. It also stops sooner if another note onset has already become overdue, so it does not send a burst of missed notes. It releases keys it owns on normal completion, stop, or an exception, and reports an error if Windows rejects input. A forced process termination or OS failure cannot guarantee cleanup; release the affected physical keys if needed. Avoid manually pressing instrument keys during autoplay.

Windows can reject input across application permission levels. LyreBot reports failures and does not automatically request administrator rights. Successfully submitting input to Windows does not prove a particular application accepted it or produced sound.

This version does not yet provide sustained-key playback, pause/resume, a sound synthesizer, configurable key mappings, MIDI import, or an installer. It supports BPM 1–1000, scores up to 2 MiB, and up to 24 hours of musical duration. Very rapid taps may be shorter than a target application's input sampling interval; preview correctness cannot guarantee that application will hear every note.

## Testing and next steps

The automated tests exercise score interpretation, exact durations, timing, and simulated failure/stop cases. They do not send global keyboard input.

Run them from the project folder with:

```powershell
python -m unittest discover -v
```

Next, compare a few existing scores that already work in Qin Xian. Fix any format discrepancies with short reproducible examples. Then check key delivery in the practice window and musical behavior in the intended instrument. These human checks establish compatibility that automated tests cannot establish here.

If text fields accept LyreBot's keys but the instrument does not, follow [input troubleshooting](docs/input-troubleshooting.md). The included `examples/input_check.txt` plays three isolated notes for a controlled comparison.

The saved score-conversion skill and previously delivered scores have not been changed by the application rename. The protocol below is the review copy for our next revision.

## Current transformation protocol

### 1. Scope, filenames, and delivery

These are the current rules for converting numbered notation (jianpu) into a playable text score; the desktop player does not perform the transcription.

- Transcribe the **entire requested score**, including its complete ending and final rest measures.
- Prefer accuracy over speed. Inspect enlarged views for octave dots, stacked chords, underlines, ties, and dense notation. Identify visually ambiguous measures and explain the chosen interpretations outside the playable script.
- The saved conversion filename is **`琴仙乐谱_<score-name>.txt`**. Obtain the score title from the supplied image filenames and remove only trailing page numbers. For example, `柔月下的暗鸦1.png` and `柔月下的暗鸦2.png` produce `琴仙乐谱_柔月下的暗鸦.txt`.
- That Chinese filename prefix is a historical conversion convention, not a filename requirement imposed by LyreBot. Renaming the application does not rename previously delivered scores or change that saved convention.
- Deliver the playable `.txt` as a download in chat; the user places it in their music folder. Normal conversion does not require music-folder access.
- Keep interpretation notes, explanatory text, and measure labels outside the playable script. The saved naming preference applies to future conversions and does not request moving or renaming older files.
- LyreBot can open a score with any filename; it reads the contents rather than interpreting the title.

### 2. Header, measures, parts, and meter

- Begin with `speed=` and the score's starting **quarter-note BPM**, for example `speed=99` or `speed=120`.
- Put each actual measure on its own line. Count actual bars, not just the measure numbers printed at the start of score systems.
- Separate simultaneous upper and lower parts using `|`, keeping their original order. The separator means **play these parts together**, not play the left side and then the right side.
- Use lowercase pitch letters and compact measure lines without spaces when producing a score under the saved conversion guide.
- Preserve meter changes, genuine pickups, explicitly requested exceptions, and final rest measures. The converter checks each part against the local meter.
- **Player behavior:** LyreBot accepts one-part files without `|` or two-part files with it. Keep the same number of parts throughout a file. Both parts on a line must have equal total duration. Use explicit rests for silent parts; empty parts are invalid.
- **Meter limitation:** LyreBot does not accept a time-signature command or infer a meter. Different measure lengths are allowed. Preserving a meter change currently means preserving the appropriate durations and line boundaries; the human transcription check establishes whether those lengths match the original score.
- **Tempo changes:** a `speed=<BPM>` line between measures changes the tempo from the next measure on. The interface's speed percentage scales every tempo in the file proportionally.

Example with two parts and four beats per measure:

```text
speed=120
a-s-d-f-|z---0-
g-h-j-q-|x---0-
```

### 3. Pitch and octave mapping

| Numbered note | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Plain / middle register | a | s | d | f | g | h | j |
| Dot above / upper register | q | w | e | r | t | y | u |
| Dot below / lower register | z | x | c | v | b | n | m |

- Map each note's pitch number and octave independently.
- Distinguish octave dots above or below a note from augmentation dots beside it.
- Do not invent mappings for accidentals or octaves outside the specified 21-key range. Flag unresolved notes for discussion.
- Letters name keyboard positions, not fixed concert pitches. The receiving instrument determines the actual sound, tuning, and scale. A non-QWERTY keyboard layout may display different characters in the test pad even though LyreBot uses physical QWERTY positions.

### 4. Notes, chords, and rests

- One or more adjacent pitch letters form a note or chord. Append one duration for that whole event.
- Vertically stacked notes in a part are simultaneous. A lower part's chord may extend above its baseline; do not mistakenly assign its upper chord tones to the upper part.
- The saved conversion convention orders chord letters from lowest to highest pitch. Plain 6 + plain 7 + upper 1 for one beat becomes `hjq-`.
- The player accepts valid unique chord letters in any order and schedules them together. Repeated letters within one chord are an error.
- A rest is `0` followed by the same duration syntax as a note: `0-`, `0.`, `0~`, or `0*`.
- Duration separates consecutive events. `asd-` is one chord; `a-s-d-` is three successive notes.
- The legacy format has no defined rolled-chord/arpeggio timing. A rolled chord was previously converted to a simultaneous chord with that interpretation disclosed. Treat such cases as an explicit interpretation to review, rather than inventing an arpeggio convention.

| Token | Meaning |
| --- | --- |
| `a-` | Middle 1 for one beat |
| `adg-` | Middle 1 + 3 + 5 together for one beat |
| `q.` | Upper 1 for half a beat |
| `0--` | Two beats of silence |

### 5. Basic rhythm, extensions, and augmentation dots

Append duration symbols directly to each note, chord, or rest:

| Written notation | Symbol | Quarter-note beats |
| --- | --- | ---: |
| No underline: quarter note | `-` | 1 |
| One underline: eighth note | `.` | 1/2 |
| Two underlines: sixteenth note | `~` | 1/4 |
| Three underlines: thirty-second note | `*` | 1/8 |

- Consecutive duration symbols **add**. Each standalone extension dash in the source adds another `-` to the preceding event. Quarter 5 followed by two extension dashes becomes `g---` (three beats).
- An augmentation dot adds half the base duration. Dotted quarter 5 is `g-.` (1 + 1/2 beats), dotted eighth 5 is `g.~` (1/2 + 1/4), and dotted sixteenth 5 is `g~*` (1/4 + 1/8).
- The punctuation is a duration code, not a literal reconstruction of the printed score. In particular, `.` here contributes half a beat; its use after another duration can express an augmentation dot.
- A musical position in seconds is `beats × 60 / BPM`. At 120 BPM, `-` occupies 500 ms, `.` 250 ms, `~` 125 ms, and `*` 62.5 ms.

### 6. Ties, phrasing slurs, and cross-measure continuations

- The saved guide says to omit the later pitch when a tie/slur marks it as a continuation and append only its duration to the original event. Two tied upper-1 eighth notes become `q..`, with one attack and one beat of total duration.
- The guide also says not to suppress distinct pitches under a phrasing slur. **Terminology to resolve:** a phrasing slur and a tie are different musical markings; the guide's combined phrase "tie/slur" is ambiguous. A phrasing slur alone should not be used as evidence that an attack disappears. Ambiguous markings must be disclosed for review.
- Across a barline, preserve the measure boundary and begin the corresponding part on the next line with duration symbols only. Continue each part independently.
- A leading continuation needs a previous event in the same part. LyreBot also allows a preceding rest to be extended; it does not revive an earlier pitched note.

Complete two-part example:

```text
speed=120
w----|z---0-
----|x---0-
```

The first part has one W attack and eight beats of musical duration across two measures. The other part proceeds independently.

**Important playback distinction:** the saved guide previously assumed a player might retain a held note across lines and required that assumption to be disclosed if unverified. LyreBot currently merges the duration without another attack, but still uses one short initial tap. It does **not** keep the physical key down across tied measures.

### 7. Triplets: current saved conversion rule

Unless the user supplies an explicit override, the saved converter applies this adaptation to every triplet:

1. Keep the **FIRST and THIRD** notes; discard the SECOND.
2. Determine the total duration **T** of the original group from its notation and context. Do not assume every group occupies one beat.
3. Give each retained note duration **T/2**, preserving the group's total length.
4. Preserve the retained notes' pitches, octaves, and chord/rest identities.

For triplet pitches plain 1, 2, 5:

| Original group's total T | Current adapted output | Retained event duration |
| --- | --- | --- |
| 1 beat | `a.g.` | 1/2 beat each |
| 1/2 beat | `a~g~` | 1/4 beat each |
| 2 beats | `a-g-` | 1 beat each |

This supersedes the older rule that retained the second and third notes and the older assumption that every replacement uses eighth notes. Previously confirmed score lines using those older rules are not templates for future conversions. A protocol update does not automatically request edits to already delivered scores.

This is an intentional musical simplification: preserving total duration does not preserve the original three-note rhythm or every original pitch.

### 8. LyreBot extension: exact beat durations and unsimplified tuplets

The current player already supports a positive integer or rational beat duration in braces:

| Token | Meaning |
| --- | --- |
| `a{2}` | A occupies two beats |
| `adg{3/2}` | A+D+G occupies one and a half beats |
| `0{1/6}` | A rest occupies one sixth of a beat |
| Leading `{1/3}` in a later measure | Extend the preceding event in that part by one third of a beat |

- Use one brace duration per token, with positive integer numerator and denominator. Decimal values in braces, zero/negative values, zero denominators, and mixing braces with punctuation on one event are invalid. For example, `a{1/3}-` is invalid.
- Exact fractions are retained during score interpretation. Actual playback is limited by Windows scheduling and the receiving application's input handling.
- These are **LyreBot extensions**; compatibility with Qin Xian is not claimed.

For the same original triplet pitches 1, 2, 5, LyreBot can represent all three notes:

| Original group's total T | Exact three-note representation |
| --- | --- |
| 1 beat | `a{1/3}s{1/3}g{1/3}` |
| 1/2 beat | `a{1/6}s{1/6}g{1/6}` |
| 2 beats | `a{2/3}s{2/3}g{2/3}` |

**These capabilities do not yet replace section 7's saved conversion default.** That rule remains in force until we deliberately change it. Existing simplified files cannot recover a discarded note automatically.

### 9. Text accepted by the player

- Files must be UTF-8; a UTF-8 byte-order mark is accepted. Maximum file size is 2 MiB.
- The first meaningful line must be `speed=<BPM>`, with finite BPM from 1 to 1000 inclusive. Decimal tempos are accepted.
- Blank lines are ignored. `#` starts a comment that ends at the line break. Whitespace is ignored, including inside measure lines.
- Spaces do **not** separate events: `a s -` is read as the chord `as-`. Use durations to separate notes.
- Comments and whitespace tolerance are player features. The saved converter's output convention still uses compact lines and keeps interpretation notes outside the playable script.
- Only the 21 lowercase mapped letters and `0` can identify events. Every event needs a duration. Empty parts, changing the number of parts, duplicate chord keys, unknown symbols, and unsupported commands are errors.
- Parsed musical duration must not exceed 24 hours. A slower playback-tempo override must also respect this limit. Timing too small to represent in the playback schedule is rejected.
- The reader reports the source line and, where applicable, part number. It does not execute commands, silently substitute pitches, or repair missing rhythm.

### 10. How musical events become physical presses

- Written durations establish musical positions. Each pitched event triggers a short tap; a rest advances musical time without input.
- The requested tap length is 35 ms by default. An actual key press is shortened to the minimum of the requested tap length, that event's duration, and half the gap to the next explicit onset of the same key. The final term is omitted if that key does not occur again.
- Simultaneous requests for the same key in both parts become one attack. When their written durations differ, the shorter duration constrains that tap. A later explicit onset of the same key retriggers it.
- A continuation extends duration without a new attack. It does not sustain the physical key in this version.
- Presses at the same timestamp are submitted together. Releases sort before presses at equal timestamps. Windows handles individual events serially; this is scheduled simultaneity, not a guarantee of physically perfect simultaneity.
- Playback follows deadlines relative to one monotonic starting clock. It stops after excessive lateness instead of sending a backlog of missed notes. Final rests remain part of the playback duration.
- The 21 mappings are fixed physical keyboard positions. Volume, dynamics, articulation, configurable mappings, and sustained-key behavior have no additional score syntax in the current version.

### 11. Score-specific tempo exception already in the saved guide

For the previously converted **Wangyuezhe Project** score only, use `speed=99` throughout and ignore the printed tempo 90 at measure 73. Do not apply those values to unrelated scores. This is an existing score-specific decision, not a general default for LyreBot.

### 12. Check and deliver a converted score

- Check pitches and octaves visually, especially stacked chords, crowded octave dots, augmentation dots, and underline counts.
- Check voice assignment and every part's total duration against the local meter, allowing genuine pickups or explicitly requested exceptions.
- Triplet adaptation must not create a duration mismatch. Check the full ending, final rest measures, lowercase compact syntax, line boundaries, and part separators.
- State remaining visual ambiguities, rolled-chord interpretations, unsupported notes, and unverified player assumptions outside the playable script.
- Deliver the complete `.txt` and concise interpretation notes. Do not claim playback testing unless it was actually performed.
- The program's automatic duration checks supplement this review; equal part lengths alone do not prove a transcription matches the source score.

### 13. Decisions for the next protocol revision

The following questions are recorded for discussion, without selecting new defaults here:

- Should LyreBot-targeted conversions preserve all triplet notes using exact fractions by default?
- Should a named format/version identify LyreBot extensions and distinguish them from Qin Xian-compatible scores?
- Should new score filenames use a LyreBot-specific prefix?
- What explicit syntax and behavior should represent ties, sustained keys, phrasing slurs, and rolled chords?
- How should future tempo changes, meter declarations, accidentals, and out-of-range notes be handled?

## Interface parameters and controls

| Control or display | Current behavior |
| --- | --- |
| 曲目列表 | Every `.txt` in `music/`; selecting re-reads the file; double-click or Enter starts playback |
| 刷新列表 / 打开文件夹 | Rescan `music/`; open it in File Explorer |
| 曲目信息 | Tempo, tempo-change count, measures, parts, note/chord events, duration; or the parse error |
| 速度（%） | 25–300; default 100; scales every tempo in the file |
| 按键时长（毫秒） | 1–500; default 35; actual press may be clipped as described above |
| 倒计时（秒） | 1–30; default 3; the foreground window at the end becomes the target |
| 键盘显示 | Shows the keys being sent; it does not verify the target's sound |
| 开始演奏 | Start the countdown and then keyboard playback |
| 停止（F8） | Stop and release keys; restarting begins at the start, with no pause/resume |
| 以管理员身份重新启动 | Shown when not elevated; asks Windows (UAC) to start an elevated copy |

Settings are disabled while a playback is running.

## Public implementation references

- [Microsoft: SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) — input submission, permission restrictions, and existing keyboard state.
- [Microsoft: GetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getforegroundwindow) — identifying the active window.
- [PyAutoGUI: fail-safe guidance](https://pyautogui.readthedocs.io/en/latest/index.html#fail-safes) — an emergency mouse corner is a useful escape route. LyreBot implements its own top-left check and does not depend on PyAutoGUI.
- [Python: time](https://docs.python.org/3/library/time.html) — performance clocks and the fact that sleeps can overshoot. LyreBot uses deadlines and stops after excessive delay instead of sending a backlog of notes.
