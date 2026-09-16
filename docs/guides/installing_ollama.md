<!--
Maintainer notes (not shown to users in the app):
- Spec: docs/spec_make_it_great.md#GL.9, D7. Ollama is optional.
- Checked 2026-09-16 at ollama.com:
  * Download page: macOS requires macOS 14 Sonoma or later; Windows requires
    Windows 10 or later.
  * qwen3.5 sizes: 4b = 3.4 GB, 9b = 6.6 GB (the "latest" tag is 9b).
- Memory guidance (8 GB → 4b, 16 GB+ → 9b) is a rule of thumb, not a figure from
  Ollama. Confirm summary quality and speed of qwen3.5:4b on an 8 GB machine
  before release (plan GL.9).
- Where the Ollama icon appears (menu bar / system tray) was not re-checked.
-->

# Installing Ollama (optional)

**Most people do not need this.** The easiest way to get summaries is an API key
— see *Getting an API key for summaries*.

Ollama runs an AI model on your own computer instead of an online service. Choose
it if:

- you do not want paper text sent to any online service, or
- you do not want to pay per summary.

The trade-offs: it uses 4–7 GB of disk space, summaries are slower (often a
minute or more each), their quality is lower than the online services, and your
computer works hard while it runs.

**Your computer needs:**

- a Mac with **macOS 14 (Sonoma) or later**, or a PC with **Windows 10 or later**;
- **8 GB of memory or more** (16 GB or more gives better results);
- about **10 GB of free disk space**.

Not sure how much memory you have?
- **Mac:** Apple menu () → **About This Mac** → look at **Memory**.
- **Windows:** Start → **Settings** → **System** → **About** → look at
  **Installed RAM**.

It takes about 20 minutes, most of it waiting for downloads.

---

## Step 1 — Install Ollama

1. Open **https://ollama.com/download** in your web browser.
2. Choose **macOS** or **Windows**, then click the **Download** button. Do not use
   the command shown above the button; the download button is simpler.
3. Open the file you downloaded (it will be in your **Downloads** folder).
   - **Mac:** drag Ollama into **Applications** if asked, then open it from
     Applications. If the Mac asks whether you are sure you want to open it,
     click **Open**.
   - **Windows:** run the installer and click through it. If Windows asks
     whether to allow the app to make changes, click **Yes**.
4. Ollama starts and keeps running quietly in the background. You do not need to
   open a window for it.

## Step 2 — Download a model

Ollama needs a model: the AI itself. You download it once by typing one line.

1. Open the command window:
   - **Mac:** press **Cmd+Space**, type `Terminal`, press **Return**.
   - **Windows:** click **Start**, type `PowerShell`, press **Enter**.
2. Copy the line for your computer's memory, paste it into that window, and press
   **Return** (Mac) or **Enter** (Windows):

   - **8 GB of memory:**
     ```
     ollama pull qwen3.5:4b
     ```
   - **16 GB of memory or more:**
     ```
     ollama pull qwen3.5:9b
     ```

3. Wait. A progress bar shows the download (3.4 GB or 6.6 GB). When it says
   `success`, close the window.

If you see **"command not found"** (Mac) or **"not recognized"** (Windows),
Ollama is not running yet: open the Ollama app from Applications (Mac) or the
Start menu (Windows), wait a few seconds, and try again in a *new* command window.

## Step 3 — Tell biorx to use Ollama

1. Open biorx.
2. Go to **Settings → Summaries → Advanced**.
3. Choose **Ollama (on this computer)**.
4. Choose the model you downloaded (`qwen3.5:4b` or `qwen3.5:9b`).
5. Click **Test**.
   - **"Ollama works"** — you are done.
   - **"Ollama is not running"** — open the Ollama app, wait a few seconds, and
     test again.
   - **"Model not found"** — repeat Step 2 with the exact line shown.

---

## Good to know

- Ollama must be running when you summarize. It normally starts when your
  computer starts; if summaries fail, open the Ollama app.
- To free the disk space later, open the command window and type
  `ollama rm qwen3.5:4b` (or `qwen3.5:9b`), then uninstall Ollama like any other
  app.
- You can have both an API key and Ollama set up and switch between them in
  Settings.
