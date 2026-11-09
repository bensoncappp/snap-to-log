"""
Estimate a meal's calories and macros from a photo with a vision model (Gemini or Claude).

    python estimate.py lunch.jpg
    python estimate.py lunch.jpg --note "ate about half"
    python estimate.py lunch.jpg --compare            # every provider with a key, side by side

Only Python's standard library is used for the API calls. On a Mac, photos (iPhone HEIC
included) are converted and shrunk with the built-in `sips`; elsewhere Pillow is used.
"""
import argparse
import base64
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from config import load_env
from meal import Meal

MAX_SIDE = 1092  # px: bigger photos cost more without estimating better

PROMPT = """You are a careful sports-nutrition assistant estimating what a person ATE from a photo.

The person's note (may be empty). It ALWAYS overrides what the photo suggests:
<note>{note}</note>
{context}
Rules:
1. Estimate only what the note says was eaten. "Ate half" means half of what's shown;
   "shared between 3" means a third; "no rice" means exclude the rice even if visible.
2. Identify each component and estimate its portion in grams, using plates, cutlery,
   hands and packaging for scale. Restaurant food usually has more oil, butter, sugar
   and sauce than home cooking: include it.
3. If the note names a restaurant or chain, use what that dish typically contains.
4. Calories must roughly match the macros (protein 4, carbs 4, fat 9 kcal per gram; alcohol 7).
5. Be honest about uncertainty. Don't round towards "healthy".

Reply with ONLY this JSON:
{{
  "name": "short meal name, max 40 characters",
  "description": "1-2 sentences: what you see, the portion you estimated, how you applied the note",
  "components": [{{"item": "...", "grams": 0, "kcal": 0}}],
  "kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0,
  "confidence": "low | medium | high",
  "biggest_uncertainty": "the one thing most likely to make this wrong"
}}"""


def prepare_image(path: Path) -> tuple[str, str]:
    """(base64 data, mime type) of a JPEG no larger than MAX_SIDE on its long side."""
    if not path.exists():
        raise FileNotFoundError(f"photo not found: {path}")
    if platform.system() == "Darwin" and shutil.which("sips"):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "meal.jpg"
            subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85",
                            "-Z", str(MAX_SIDE), str(path), "--out", str(out)],
                           check=True, capture_output=True)
            return base64.b64encode(out.read_bytes()).decode(), "image/jpeg"
    from io import BytesIO
    from PIL import Image  # pip install pillow (only needed off a Mac)
    img = Image.open(path).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"


def post_json(url: str, headers: dict, body: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:  # one readable line: the API's own status + message
            err = json.loads(raw).get("error", {})
            detail = " ".join(str(x) for x in (err.get("status") or err.get("type"), err.get("message")) if x)
        except (ValueError, AttributeError):
            detail = ""
        raise RuntimeError(f"HTTP {e.code} from API: {(detail or raw)[:300]}") from None
    except (TimeoutError, urllib.error.URLError) as e:
        raise RuntimeError(f"no reply from the API within {timeout}s ({e})") from None


def ask_gemini(image_b64: str, mime: str, prompt: str) -> tuple[str, str]:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set in .env")
    model = os.environ["GEMINI_MODEL"]
    data = post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"x-goog-api-key": key},
        {"contents": [{"parts": [{"inline_data": {"mime_type": mime, "data": image_b64}},
                                 {"text": prompt}]}],
         "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}},
    )
    return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]), model


def ask_claude(image_b64: str, mime: str, prompt: str) -> tuple[str, str]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")
    model = os.environ["CLAUDE_MODEL"]
    data = post_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
        {"model": model, "max_tokens": 800, "temperature": 0.2,
         "messages": [{"role": "user", "content": [
             {"type": "image", "source": {"type": "base64", "media_type": mime, "data": image_b64}},
             {"type": "text", "text": prompt}]}]},
    )
    return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text"), model


PROVIDERS = {"gemini": ask_gemini, "claude": ask_claude}


def extract_json(text: str) -> dict:
    """Models sometimes wrap JSON in ```fences``` or add a sentence: take the {...} part."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    text = fenced.group(1) if fenced else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON in the model's reply: {text[:200]!r}")
    return json.loads(text[start:end + 1])


def estimate(photo: Path, note: str = "", provider: str = None) -> dict:
    """Returns {"meal": Meal, "description", "components", "confidence", ...}."""
    provider = provider or os.environ.get("PROVIDER", "gemini").lower()
    context = os.environ.get("FOOD_CONTEXT", "").strip()
    prompt = PROMPT.format(note=note.strip() or "(none)",
                           context=f"\nWhere and what the person usually eats: {context}\n" if context else "")
    image_b64, mime = prepare_image(photo)
    started = time.time()
    raw, model = PROVIDERS[provider](image_b64, mime, prompt)
    r = extract_json(raw)

    name = str(r.get("name", "Meal")).strip()[:40] or "Meal"
    short = " ".join(note.split())[:18]
    if short and short.lower() not in name.lower():
        name = f"{name} ({short})"  # keep the user's own words visible in the diary
    meal = Meal(name, round(float(r["kcal"])), round(float(r["protein_g"])),
                round(float(r["carbs_g"])), round(float(r["fat_g"])))
    return {"meal": meal, "provider": provider, "model": model,
            "seconds": round(time.time() - started, 1),
            "description": r.get("description", ""), "components": r.get("components", []),
            "confidence": r.get("confidence", ""), "uncertainty": r.get("biggest_uncertainty", "")}


def show(r: dict) -> None:
    print(f"\n🍽️  {r['meal'].name}   [{r['provider']} · {r['model']} · {r['seconds']}s]")
    print(f"   {r['description']}")
    for c in r["components"]:
        print(f"     • {c.get('item')}: ~{c.get('grams')} g, ~{c.get('kcal')} kcal")
    print(f"   Confidence: {r['confidence']}. Biggest uncertainty: {r['uncertainty']}")
    print(f"\n   {r['meal'].line()}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Estimate a meal from a photo")
    ap.add_argument("photo", type=Path)
    ap.add_argument("--note", default="", help='e.g. "ate half" or "no rice"')
    ap.add_argument("--provider", choices=list(PROVIDERS))
    ap.add_argument("--compare", action="store_true", help="run every provider that has a key")
    args = ap.parse_args()
    load_env()
    providers = ([p for p, k in (("gemini", "GEMINI_API_KEY"), ("claude", "ANTHROPIC_API_KEY"))
                  if os.environ.get(k)] if args.compare else [args.provider])
    ok = False
    for p in providers:
        try:
            show(estimate(args.photo, args.note, p))
            ok = True
        except Exception as e:
            print(f"❌ {p or os.environ.get('PROVIDER')}: {e}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
