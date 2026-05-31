#!/usr/bin/env python3
"""Capture a photo from the Raspberry Pi camera (Arducam IMX519) and, if the
shot contains a lawn, ask the Claude API to assess how healthy it is.

How it works:
  1. Capture a still with picamera2 (libcamera) — falls back to the
     `rpicam-still` / `libcamera-still` CLI if picamera2 isn't installed.
  2. Downscale + JPEG-encode the frame for the API.
  3. Send it to Claude (vision) with a turf-care system prompt and ask for a
     structured assessment. Claude first decides whether a lawn is present;
     if not, it reports `lawn_present: false` and nothing else is graded.
  4. Print the report (and optionally save the photo + JSON report to disk).

Setup:
  - Camera: enable the IMX519 overlay (`dtoverlay=imx519` in
    /boot/firmware/config.txt — see Arducam's install guide) and reboot, then
    `rpicam-hello` / `libcamera-hello` should preview the sensor.
  - Python deps: `python3-picamera2`, `python3-pil`, and the `anthropic` SDK.
    `scripts/install_deps.sh` installs all of them.
  - API key: export `ANTHROPIC_API_KEY` (the Anthropic client reads it).

Examples:
  ANTHROPIC_API_KEY=sk-ant-... python3 scripts/lawn_camera.py
  python3 scripts/lawn_camera.py --save-dir ~/lawn-reports
  python3 scripts/lawn_camera.py --image some_photo.jpg          # analyse an existing file
  python3 scripts/lawn_camera.py --no-api                        # just capture, don't call Claude
  python3 scripts/lawn_camera.py --interval 3600 --save-dir /var/lib/robot/lawn
"""

import argparse
import base64
import datetime as _dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

MODEL = "claude-opus-4-7"
MAX_IMAGE_EDGE = 1568  # px on the long edge sent to the API (Opus 4.7 accepts up to 2576)

SYSTEM_PROMPT = """\
You are a turf-care assistant. You receive a single photo taken by a camera \
mounted on a lawn-care robot and must report on it in a structured way.

Step 1 — Is there a lawn? A "lawn" is an area of managed/mown turf grass \
(home lawn, park, sports field, etc.). Patchy grass on dirt, ornamental \
grasses, crops, hay fields, indoor scenes, pavement, gravel, walls, the sky, \
or close-ups of the robot itself are NOT lawns. If you are not reasonably sure \
a lawn is the subject of the photo, set `lawn_present` to false and treat the \
rest of the assessment as "no lawn".

Step 2 — If a lawn IS present, assess its health from what is visible:
  - Color & uniformity: deep, even green is healthy; yellowing, browning, or \
patchy color signals stress (drought, dormancy, nutrient deficiency, disease).
  - Density & coverage: thick, full turf is healthy; thin spots, bare soil, or \
visible thatch are problems.
  - Weeds & invaders: clover, dandelions, crabgrass, moss, broadleaf weeds.
  - Mowing/condition: scalped areas, ruts, overgrown/leggy growth, debris.
  - Disease/pest signs: rings, irregular dead patches, fungal mats.
  Map your overall judgement to `health_status` and to a 0–100 `health_score` \
(0 = dead/bare, 100 = lush, dense, uniform, weed-free). If no lawn is present, \
use `health_status: "no_lawn"` and `health_score: 0`.

Be specific and concise. Only report issues and recommendations you can \
actually justify from the image — do not speculate beyond what is visible. \
Recommendations must be concrete turf-care actions (water, mow at X height, \
overseed, fertilise, spot-treat weeds, dethatch, etc.).

For EACH recommendation, also say WHEN to do it and how URGENT it is:
  - `when`: a specific schedule or cadence — e.g. "twice weekly until rain \
returns", "next mow, within 3–5 days, dry grass only", "now, then again in \
4–6 weeks", "early autumn (Sept–Oct)", "after the next mow when grass is \
dry". Never use vague phrasing like "as needed" or "regularly" — give a \
concrete window or frequency the operator can act on.
  - `priority`: "high" (do this week), "medium" (do within a month), or \
"low" (seasonal / routine maintenance)."""


# --------------------------------------------------------------------------- #
# Camera capture
# --------------------------------------------------------------------------- #
def capture_image(out_path):
    """Capture a still to out_path (JPEG). Tries picamera2, then the CLI tools."""
    try:
        from picamera2 import Picamera2  # type: ignore

        picam2 = Picamera2()
        picam2.configure(picam2.create_still_configuration())
        picam2.start()
        time.sleep(2)  # let auto-exposure / white balance settle
        picam2.capture_file(out_path)
        picam2.stop()
        picam2.close()
        return "picamera2"
    except ImportError:
        pass
    except Exception as e:  # picamera2 present but the sensor isn't usable
        print(f"[lawn-cam] picamera2 capture failed ({e}); trying the CLI tools", file=sys.stderr)

    for cli in ("rpicam-still", "libcamera-still"):
        if shutil.which(cli):
            subprocess.run([cli, "--nopreview", "--timeout", "2000", "--output", out_path],
                           check=True)
            return cli

    raise RuntimeError(
        "No way to capture an image: picamera2 isn't installed and neither "
        "rpicam-still nor libcamera-still is on PATH. Run scripts/install_deps.sh, "
        "and make sure the IMX519 overlay is enabled in /boot/firmware/config.txt."
    )


def encode_for_api(image_path, max_edge=MAX_IMAGE_EDGE):
    """Return (base64_jpeg_str, media_type) for the image, downscaled to max_edge."""
    from PIL import Image  # noqa: PLC0415

    img = Image.open(image_path)
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_edge:
        scale = max_edge / float(max(w, h))
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


# --------------------------------------------------------------------------- #
# Claude vision call
# --------------------------------------------------------------------------- #
def assess_lawn(image_b64, media_type):
    """Call the Claude API and return a dict assessment (or raise)."""
    import anthropic  # noqa: PLC0415
    from pydantic import BaseModel, Field  # noqa: PLC0415
    from typing import Literal  # noqa: PLC0415

    class Recommendation(BaseModel):
        action: str = Field(
            description="Concrete turf-care action — e.g. 'water deeply, ~1 inch', "
                        "'mow at 3 inches', 'overseed thin areas with tall fescue', "
                        "'spot-treat broadleaf weeds', 'apply balanced slow-release fertiliser'."
        )
        when: str = Field(
            description="Specific schedule or cadence for this action — e.g. "
                        "'twice weekly until rain returns', 'next mow within 3-5 days, dry grass only', "
                        "'now, then again in 4-6 weeks', 'early autumn (Sept-Oct)'. "
                        "Avoid vague phrasing like 'as needed' or 'regularly'."
        )
        priority: Literal["high", "medium", "low"] = Field(
            description="Urgency: 'high' = address this week, 'medium' = within a month, "
                        "'low' = seasonal or routine maintenance."
        )

    class LawnAssessment(BaseModel):
        lawn_present: bool = Field(
            description="True if a managed lawn / mown turf grass is the subject of the photo."
        )
        confidence: float = Field(
            description="Confidence in the lawn_present decision, 0.0 to 1.0."
        )
        health_status: Literal["healthy", "fair", "stressed", "unhealthy", "no_lawn", "unknown"] = Field(
            description="Overall lawn health. Use 'no_lawn' when lawn_present is false."
        )
        health_score: int = Field(
            description="Estimated lawn health, 0 (dead/bare) to 100 (lush, dense, uniform). Use 0 if no lawn."
        )
        issues: list[str] = Field(
            description="Short phrases for each visible problem (e.g. 'brown patches', 'weeds', "
                        "'thin/bare spots', 'drought stress', 'disease ring', 'overgrown'). Empty if none."
        )
        recommendations: list[Recommendation] = Field(
            description="Concrete turf-care actions, each with WHEN to do it and a priority. "
                        "Empty if none / no lawn."
        )
        summary: str = Field(
            description="One or two plain-language sentences summarising the lawn's condition."
        )

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    response = client.messages.parse(
        model=MODEL,
        max_tokens=2048,
        thinking={"type": "adaptive"},  # let Claude reason about subtle turf-health cues
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},  # reused across repeated runs
        }],
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": "Assess this photo: is a lawn present, and if so, how healthy is it?"},
            ],
        }],
        output_format=LawnAssessment,
    )

    if response.parsed_output is None:
        # Refusal or truncation — surface whatever text came back.
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), "")
        raise RuntimeError(f"Claude did not return a structured assessment (stop_reason="
                           f"{response.stop_reason}). Text: {text!r}")

    result = response.parsed_output.model_dump()
    result["_model"] = response.model
    result["_request_id"] = response._request_id
    result["_usage"] = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0),
    }
    return result


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def print_report(result):
    if not result.get("lawn_present"):
        print(f"No lawn detected (confidence {result.get('confidence', 0):.0%}). "
              f"{result.get('summary', '')}".rstrip())
        return
    print(f"Lawn detected — health: {result.get('health_status', '?')} "
          f"({result.get('health_score', '?')}/100, confidence {result.get('confidence', 0):.0%})")
    print(f"  {result.get('summary', '').strip()}")
    issues = result.get("issues") or []
    if issues:
        print("  Issues: " + ", ".join(issues))
    recs = result.get("recommendations") or []
    if recs:
        print("  Recommendations:")
        for r in recs:
            if isinstance(r, dict):
                pri = (r.get("priority") or "").upper()
                pri_tag = f"[{pri}] " if pri else ""
                action = r.get("action") or ""
                when = r.get("when") or ""
                line = f"    - {pri_tag}{action}"
                if when:
                    line += f"  —  WHEN: {when}"
                print(line)
            else:
                # Tolerate the legacy plain-string shape if older results
                # are re-loaded from disk.
                print(f"    - {r}")


def run_once(args):
    # Acquire an image: either a file the user gave us, or a fresh capture.
    if args.image:
        image_path = args.image
        captured_tmp = None
    else:
        fd, image_path = tempfile.mkstemp(prefix="lawncam_", suffix=".jpg")
        os.close(fd)
        captured_tmp = image_path
        src = capture_image(image_path)
        print(f"[lawn-cam] captured via {src}")

    try:
        if args.no_api:
            print(f"[lawn-cam] image at {image_path} (skipping Claude call: --no-api)")
            return 0

        b64, media_type = encode_for_api(image_path)
        result = assess_lawn(b64, media_type)
        print_report(result)

        if args.save_dir:
            os.makedirs(args.save_dir, exist_ok=True)
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            saved_img = os.path.join(args.save_dir, f"lawn-{stamp}.jpg")
            saved_json = os.path.join(args.save_dir, f"lawn-{stamp}.json")
            shutil.copyfile(image_path, saved_img)
            result["_image"] = saved_img
            result["_timestamp"] = _dt.datetime.now().isoformat(timespec="seconds")
            with open(saved_json, "w") as f:
                json.dump(result, f, indent=2)
            print(f"[lawn-cam] saved {saved_img} and {saved_json}")
        return 0
    finally:
        if captured_tmp and not args.save_dir and os.path.exists(captured_tmp):
            os.unlink(captured_tmp)


def main():
    parser = argparse.ArgumentParser(description="Capture a photo and assess lawn health with Claude.")
    parser.add_argument("--image", help="Analyse this image file instead of capturing a new one.")
    parser.add_argument("--save-dir", help="Directory to save each photo + JSON report into.")
    parser.add_argument("--no-api", action="store_true", help="Only capture; don't call the Claude API.")
    parser.add_argument("--interval", type=float, metavar="SECONDS",
                        help="Repeat forever, waiting this many seconds between runs.")
    args = parser.parse_args()

    if not args.no_api and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Export it (or pass --no-api to just capture).")

    if args.interval:
        while True:
            try:
                run_once(args)
            except Exception as e:  # keep the loop alive across transient failures
                print(f"[lawn-cam] error: {e}", file=sys.stderr)
            time.sleep(args.interval)
    else:
        sys.exit(run_once(args))


if __name__ == "__main__":
    main()
