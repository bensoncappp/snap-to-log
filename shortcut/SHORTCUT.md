# The iPhone Shortcut

The phone does the quick part (photo → AI estimate → you check it), then sends the meal to GitHub as an
event. GitHub Actions does the slow part (driving the Cronometer website).

```
Snap or pick a photo → add a note → Gemini estimates → edit one line → send event to GitHub
                                                                              ↓
                                           .github/workflows/log-meal.yml logs it to Cronometer
```

## Build it (Shortcuts app → +)

1. **Choose from Menu**: `📷 Snap` → **Take Photo** · `🖼️ From Photos` → **Select Photos** (Select Multiple off).
2. **Set Variable** `Meal Photo` to **Menu Result**.
3. **Convert Image** `Meal Photo` to **JPEG** → **Resize Image** (Converted Image) to width **1092**, height auto
   → **Encode** (Resized Image) with **base64**, line breaks **None**.
4. **Ask for Input** (optional): "Anything the photo can't show? e.g. ate half, no rice" → variable `Note`.
5. **Get Contents of URL**: Gemini
   - URL `https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent`
   - POST · header `x-goog-api-key` = your Gemini key
   - JSON body: `contents` → [ `parts` → [ {`inline_data`: {`mime_type`: `image/jpeg`, `data`: base64 text}},
     {`text`: prompt} ] ], where the prompt asks for exactly one line: `Meal name | 000 kcal | 00p 00c 00f`,
     and includes the `Note`.
   - **Get Dictionary Value** `candidates.1.content.parts.1.text` (Shortcuts lists count from 1).
6. **Ask for Input** with that line as the default answer, so you can fix any number. Variable `Meal Line`.
7. **Get Contents of URL**: GitHub
   - URL `https://api.github.com/repos/<you>/snap-to-log/dispatches`
   - POST · headers `Authorization` = `Bearer <token>`, `Accept` = `application/vnd.github+json`
   - JSON body: `event_type` (Text) = `meal` · `client_payload` (Dictionary) → `line` (Text) = `Meal Line`
8. **If** *Contents of URL* **has any value** → **Quick Look** it (GitHub only replies with text when something
   went wrong) · **Otherwise** → **Show Notification** "Meal sent ✅".

## The token
A fine-grained personal access token with access to **only this repo** and **Contents: Read and write**
(what GitHub requires to send a `repository_dispatch` event). Paste it into the Shortcut; never screenshot or
share the Shortcut, because the keys travel with it.

## Check it worked
GitHub → **Actions → Log Meal**: a green run about a minute later, and the meal in today's Cronometer diary.
