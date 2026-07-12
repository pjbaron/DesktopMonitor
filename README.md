# Desktop Remote

Watch and lightly control this PC from a phone browser over your local network.

## Purpose

Turn any phone (or second device) on the same wifi into a live viewer and basic
remote for this desktop. Pinch to zoom in on a region and the server captures
only that rectangle at native resolution, so text stays crisp while bandwidth
stays bounded (one screen of pixels at a time).

## Usage

```
pip install mss pyautogui uvicorn fastapi pillow
python desktop_remote.py
```

Then on your phone (same wifi) open `http://<this-pc-ip>:8000`.

Options:

```
python desktop_remote.py --port 8000 --monitor 1 --fps 10
```

On the phone: drag to pan, pinch or `+`/`-`/double-tap to zoom, `Fit` to reset,
`M1/M2...` to switch monitors. Toggle `Click: on` to send taps as mouse clicks.
Use the text box, `Enter`, arrow/Esc/Tab keys, and `^C` to send input.

## Risks

- **No authentication or encryption.** Anyone who can reach the port sees your
  screen and can control the mouse/keyboard. The server binds `0.0.0.0`, so it
  is exposed to the whole local network.
- **Full input control.** Remote taps become real clicks; the text box types
  into whatever window has focus.
- `pyautogui` corner failsafe is disabled deliberately.
- Intended only for a trusted LAN. Do not expose the port to the internet.

## Implementation

A FastAPI app serves a single self-contained HTML page and a WebSocket. The
client reports which screen rectangle it is viewing (`view` messages); a
per-connection async loop grabs that region with `mss`, JPEG-encodes it via
Pillow (downscaled past `MAX_W`), and streams frames at `FPS`. Input messages
(`click`, `text`, `key`, `hotkey`, `monitor`) are dispatched to `pyautogui`.
