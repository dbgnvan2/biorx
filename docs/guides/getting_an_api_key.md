<!--
Maintainer notes (not shown to users in the app):
- Spec: docs/spec_make_it_great.md#GL.9, D7.
- Checked 2026-09-16 against the providers' public pages:
  * DeepSeek: docs at api-docs.deepseek.com (models deepseek-flash, deepseek-v4-pro;
    prices per 1M tokens: flash $0.30 input / $1.20 output at peak, half off-peak).
    platform.deepseek.com requires sign-in, so the button names inside it
    ("API keys", "Create new API key", "Top up") were NOT seen and must be
    checked by hand before release.
  * Anthropic: sign-in page at platform.claude.com/settings/keys (titled "API keys")
    and platform.claude.com/settings/billing (titled "Billing"). Haiku 4.5 is
    $1 / $5 per 1M input/output tokens. Button names after sign-in were NOT seen.
- Cost per summary assumes ~12,000 characters of paper text (llm_config.yaml
  max_text_chars) ≈ 3,000 input tokens and ~800 output tokens. Re-check if either
  changes.
- Walk through this guide on a fresh account before each release (plan GL.9).
-->

# Getting an API key for summaries

biorx can summarize papers for you using an AI service. To do that, it needs an
**API key**: a long password that lets biorx use the service on your account.
You pay the service directly for what you use. biorx never sees your bill and
never sends your key anywhere except to the service you choose.

You only need to do this once. It takes about 10 minutes.

**You do not need a key to use biorx.** Searching, downloading papers, saving
reference lists and exporting all work without one. Only summaries need it.

---

## Step 1 — Choose a service

| | **DeepSeek** | **Anthropic (Claude)** |
|---|---|---|
| Cost per summary | Well under 1 cent | About 1 cent |
| Summary quality | Good | Very good |
| How you pay | Add credit in advance (e.g. $5) | Add credit in advance |
| Good choice if… | You want the lowest cost | You want the best summaries |

These costs are estimates. $5 of credit covers several hundred summaries with
either service. Prices are set by the services and can change.

If you are unsure, pick either — you can add the other one later in biorx's
Settings.

---

## Step 2a — DeepSeek

1. Open **https://platform.deepseek.com** in your web browser.
2. Sign up with your email address (or sign in if you already have an account).
   Confirm your email if asked.
3. Add credit: find **Top up** (or **Billing**) in the menu, choose an amount
   (a small amount such as $5 is plenty to start), and pay with a card.
   Summaries will not work until your account has credit.
4. Create a key: find **API keys** in the menu, then click the button to create a
   new key. Give it a name you will recognise, such as `biorx`.
5. A long piece of text starting with `sk-` appears. **Copy it now** — click the
   copy button next to it. DeepSeek will not show it to you again. If you lose it,
   delete that key and create a new one.

Go to **Step 3**.

## Step 2b — Anthropic (Claude)

1. Open **https://platform.claude.com** in your web browser.
2. Sign in with Google or with your email address. This is a separate account from
   the Claude chat app, even if you use the same email.
3. Add credit: go to **https://platform.claude.com/settings/billing**, add a
   payment card and buy credit (a small amount such as $5 is plenty to start).
   Summaries will not work until your account has credit.
4. Create a key: go to **https://platform.claude.com/settings/keys** and click the
   button to create a key. Name it `biorx`.
5. A long piece of text starting with `sk-ant-` appears. **Copy it now** — click
   the copy button. It will not be shown again. If you lose it, delete that key
   and create a new one.

---

## Step 3 — Put the key into biorx

1. Open biorx.
2. Go to **Settings → Summaries**.
3. Choose the service you signed up with.
4. Click in the **API key** box and paste (**Cmd+V** on a Mac, **Ctrl+V** on
   Windows).
5. Click **Test key**.
   - **"Key works"** — you are done.
   - **"Key not accepted"** — the key was copied incompletely. Copy it again, or
     create a new one.
   - **"No credit"** — add credit to your account (Step 2, item 3), then test again.
   - **"Could not reach the service"** — check your internet connection and try
     again.

biorx stores your key in your computer's own secure password storage (the
Keychain on a Mac, Credential Manager on Windows), not in a file.

---

## Keeping your key safe

- Treat the key like a password. Do not email it or paste it into documents.
- If you think someone else has seen it, delete it on the service's website and
  create a new one, then paste the new one into biorx.
- To limit what you could ever be charged, only keep a small amount of credit on
  the account, and leave any **automatic reload** / **auto top-up** option turned
  off. With prepaid credit, summaries simply stop when the credit runs out.

## What gets sent to the service

When you summarize a paper, biorx sends that paper's text to the service you
chose. Your saved searches, reference lists and the rest of your library are
never sent.
