# Prompt Cleaner

Select text, press **Ctrl+Shift+Space**, and the filler words and typos are cleaned in place.
Made for Windows voice typing (Win+H), which writes down every "um", "like", "you know" and "the the",
and mishears tech words ("cloud code" for Claude Code, "kithub" for GitHub).

Runs entirely on your PC with a local model through [Ollama](https://ollama.com). Nothing is sent anywhere.

<!-- [[DEMO_GIF]]: add a short screen recording here before posting, e.g. ![demo](demo.gif) -->

## How it works

```
hotkey ─► grab text ─► rules ─► local model ─► guard ─► paste
                         │          │ slow / busy  │ rejects
                         └──────────┴──────────────┴──► rules-only result
```

1. **Rules** (plain Python): your custom word list (`custom_words.json`), safe filler (`um`, `uh`, a standalone
   `you know`), repeated words (`the the`), spacing. URLs, `#123` and backticked text pass through untouched.
2. **Local model**: `qwen3:8b` fixes spelling, grammar and punctuation and removes the filler rules can't judge
   ("like the asterisks" is not filler; "it was like really slow" is). Thinking mode off, temperature 0.
3. **Guard**: the model is never trusted to keep your meaning. The guard lines its output up against the
   rules output, word by word:
   - a word the model **added** is dropped;
   - a word the model **deleted** is put back, unless it is filler, a small function word or a repeat;
   - if more than 30% needed repair, the rules-only text is used instead.
4. **Paste**: the result replaces your selection (Ctrl+Z in the app brings the original back).

If the model is slow (6 s timeout), not running, or another model holds the GPU, you still get the rules-only
cleanup right away. It never unloads or evicts another model.

**Terminals are different:** Ctrl+C in a terminal interrupts the running program, so in Windows Terminal,
cmd and PowerShell the hotkey opens a small dictation box instead. Dictate into it, press Enter, and the cleaned
text is pasted into the terminal (Shift+Enter = new line, Esc = cancel).

## Requirements
- Windows 10 or 11 (uses the Win32 API through `ctypes`).
- Python 3 with tkinter (the python.org installer includes it). Standard library only: nothing to `pip install`.
  Tested on Python 3.14.
- [Ollama](https://ollama.com) with the model: `ollama pull qwen3:8b`. Without it the tool still runs, rules-only.

## Run
```
git clone https://github.com/Berryderi/prompt-cleaner
cd prompt-cleaner
start.cmd          (runs in the background with pythonw; stop with stop.cmd)
```
Or in a console: `python app.py` · rules-only: `python app.py --no-model` · other key: `python app.py --hotkey ctrl+shift+j`.

## Configure
- `config.json`: hotkey, model, Ollama URL, timeout, which processes count as terminals.
- `custom_words.json`: your own mishearings, `{"from": [...], "to": "..."}`, with optional `except_phrases`
  (for example "cloud" becomes "Claude" but "Google Cloud" stays).

## Privacy
- It only talks to Ollama on a loopback address (`127.0.0.1`), and it ignores system and `HTTP_PROXY` proxy
  settings so the request can't be routed elsewhere. Ollama cloud models (`…-cloud`) are refused, because
  they process text on ollama.com.
- The log (`logs/cleaner.jsonl`) holds timings, word counts, which path was taken and the name of the app you
  used it in. Never your text.
- It works through the clipboard like a normal copy and paste, so the original and the cleaned text can show up
  in Windows clipboard history (Win+V) or clipboard sync if you have those turned on.

## Test it
```
python -m unittest discover -s tests     (no Ollama needed)
python eval.py                           (real model; runs tests/samples.example.jsonl)
```
To test on your own dictation, put `{"id", "kind", "raw", "expected"}` rows in `tests/samples.jsonl` (gitignored).
On my own private set of 9 real prompts (7 dictated, 2 typed) it passes 9/9: no filler left from the checked
list, no mishearings left, no content words lost, links unchanged. With the model loaded on an RTX 2080 SUPER
(8 GB), one paste took 0.6 s for 27 words, 1.7 s for 115 words and 3.1 s for 190 words.

## Limits
- Windows only, English only.
- It is conservative: when in doubt it keeps your words, so some filler and false starts survive, and
  punctuation is sometimes off.
- Output time grows with length (the model rewrites the whole text).
- The GPU check only sees models loaded in Ollama, not other GPU programs.
- Win+H voice typing into the terminal dictation box has not been tested yet.
- Known issues, not fixed yet: if you switch windows while it is working, the paste can land in the new window;
  text with line breaks pasted into a terminal may run as separate commands, so keep terminal dictation to one
  line; `stop.cmd` can fail if the folder path contains an apostrophe.
- `Ctrl+Alt+Space` is often taken by other apps, and Alt hotkeys open the menu bar in some apps, hence Ctrl+Shift+Space.

## License
MIT
