"""
Regenerate the test fixtures (small, synthetic, no copyrighted content):

  bursts.mp4  120 s test pattern, a quiet tone with loud bursts at 40-44 s and 85-88 s
  speech.mp4  ~40 s of speech from the Windows speech synthesizer (Windows only)

    python tests/fixtures/make_fixtures.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

SPEECH = ("Welcome back to the show. Today we are talking about the biggest mistake new streamers make. "
          "Here is the thing. Nobody tells you that consistency beats talent. "
          "I streamed for two years to nobody. Then one clip changed everything. "
          "Honestly, that is insane, right? So what should you actually do? "
          "Post one short every single day, and never skip a week. That is the secret.")


def bursts():
    out = os.path.join(HERE, "bursts.mp4")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=120",
        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=16000:duration=120",
        "-filter_complex", "[1:a]volume='if(between(t,40,44)+between(t,85,88),1.0,0.03)':eval=frame[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "40",
        "-c:a", "aac", "-b:a", "32k", "-shortest", out], check=True)
    print("wrote", out)


def speech():
    if sys.platform != "win32":
        print("speech.mp4 needs the Windows speech synthesizer; skipped")
        return
    wav = os.path.join(tempfile.mkdtemp(), "speech.wav")
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.Rate = 0; $s.SetOutputToWaveFile('{wav}'); $s.Speak($env:RC_TEXT); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, env={**os.environ, "RC_TEXT": SPEECH})
    out = os.path.join(HERE, "speech.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x1d2a44:s=160x90:r=10",
                    "-i", wav, "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "40",
                    "-c:a", "aac", "-b:a", "48k", out], check=True)
    print("wrote", out)


if __name__ == "__main__":
    bursts()
    speech()
