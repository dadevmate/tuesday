"""Download a slice of FineTome-100k (open, high-quality general instruction data distilled from many
web-sourced datasets) to keep Tuesday Core a capable general assistant after personal fine-tuning."""
import json
import time
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out" / "general.jsonl"
API = "https://datasets-server.huggingface.co/rows?dataset=mlabonne%2FFineTome-100k&config=default&split=train&offset={o}&length=100"


def fetch(n_keep: int = 1500, max_chars: int = 5000):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    kept = []
    offset = 0
    while len(kept) < n_keep and offset < 99000:
        try:
            with urllib.request.urlopen(API.format(o=offset), timeout=60) as r:
                rows = json.loads(r.read())["rows"]
            time.sleep(1.5)
        except Exception as e:
            if "429" in str(e):
                time.sleep(20)  # rate limited: back off and retry the same page
                continue
            print("fetch error", offset, e)
            offset += 100
            continue
        for row in rows:
            r = row["row"]
            conv = r["conversations"]
            if r.get("score", 0) < 4.0 or len(conv) < 2 or sum(len(c["value"]) for c in conv) > max_chars:
                continue
            msgs = [{"role": "user" if c["from"] == "human" else "assistant", "content": c["value"]} for c in conv if c["from"] in ("human", "gpt")]
            if msgs and msgs[0]["role"] == "user" and msgs[-1]["role"] == "assistant":
                kept.append({"messages": msgs, "source": r.get("source")})
        offset += 100
    OUT.write_text("".join(json.dumps(k) + "\n" for k in kept[:n_keep]))
    print(f"{len(kept[:n_keep])} general examples -> {OUT}")


if __name__ == "__main__":
    fetch()
